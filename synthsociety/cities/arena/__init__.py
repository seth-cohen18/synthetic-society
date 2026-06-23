"""City 3 — Arena.

A/B feature-preference testing. The whole population is shown the SAME product, then
judges competing ideas across experiments: head_to_head (forced choice), rank (build
next vs. cut), and open (what do you want next). Each persona judges every experiment
independently (parallel). Variants are counterbalanced (shuffled per persona, shown as
A/B/C, decoded back) so position/letter never biases the result. Results break down by
the brief's segment axes — catching "wins overall but loses with beginners".

Generalized from the original: the experiments are user-supplied (a JSON file) or
auto-designed from the brief; the segment axes are the brief's audience_axes.
"""

import os
import random
import re
import string
from collections import Counter, defaultdict

from synthsociety.budget import metered_call, BudgetExceeded
from synthsociety.cities import register
from synthsociety.cities.base import City, RunContext
from synthsociety.client import (cache_block, extract_json, get_client, load_json,
                                 run_batch, save_json, text_block)
from synthsociety.cost import CostProjection
from synthsociety.persona import build_persona_system_prompt, objections_faq, persona_generation_prompt
from synthsociety import report as R
from synthsociety import wizard

PERSONA_BATCH = 25
MAX_JUDGMENT_TOKENS = 600
DEFAULT_EXPERIMENTS = 5


def _seg_axes(brief):
    return brief.get("audience_axes", []) or []


def informed_brief(brief):
    return (f"You have just been given a complete walkthrough of {brief.get('name', 'the product')}. "
            f"Here is exactly what you were shown:\n\n{brief.get('pitch', '')}\n\n{objections_faq(brief)}")


# ── Experiments: supplied file or auto-design ─────────────────────────────────

async def design_experiments(client, model, brief, meter, k=DEFAULT_EXPERIMENTS) -> list:
    system = "You design A/B feature-preference experiments for products. Concrete, decision-grade, not vague."
    user = (
        f"Product: {brief.get('name')} — {brief.get('one_liner')}\n"
        f"Category: {brief.get('category')}\nPitch: {brief.get('pitch')}\n\n"
        f"Design {k} experiments to learn which product directions this audience prefers. Mix types:\n"
        "- 3 head_to_head: exactly 2 variants each, on REAL decisions for THIS product (e.g. default "
        "tone, onboarding style, pricing/packaging approach, social philosophy, hardware vs software).\n"
        "- 1 rank: 4-5 roadmap items to prioritize.\n"
        "- 1 open: no variants — what do you want next + any questions.\n\n"
        'Return ONLY a JSON array. Each: {"id":"snake_case","type":"head_to_head|rank|open",'
        '"title":"...","question":"the question to the judge","context":"1-2 sentences of setup",'
        '"variants":[{"id":"snake_case","label":"short","desc":"one sentence"}]}. open has "variants":[].'
    )
    raw = await metered_call(client, model, system, user, meter, max_tokens=3000)
    exps = extract_json(raw)
    out = []
    for i, e in enumerate(exps):
        e.setdefault("id", f"exp_{i+1}")
        e.setdefault("type", "head_to_head")
        e.setdefault("context", "")
        e.setdefault("variants", [])
        out.append(e)
    return out


# ── Judges ────────────────────────────────────────────────────────────────────

async def gen_judges(client, model, brief, n, meter) -> list:
    import math
    judges, seg_keys = [], [a["key"] for a in _seg_axes(brief)]
    for b in range(math.ceil(n / PERSONA_BATCH)):
        start = b * PERSONA_BATCH
        count = min(PERSONA_BATCH, n - start)
        system, user = persona_generation_prompt(brief, count, start, start + count - 1)
        raw = await metered_call(client, model, system, user, meter, max_tokens=8000)
        try:
            judges.extend(extract_json(raw))
        except Exception:
            pass
    judges = judges[:n]
    for p in judges:  # flatten segment values for tally access
        seg = p.get("segment", {}) if isinstance(p.get("segment"), dict) else {}
        for k in seg_keys:
            p[k] = seg.get(k)
    return judges


def build_judge_system(persona, brief, grounding, idx) -> str:
    base = build_persona_system_prompt(persona, brief, grounding, seed_offset=idx)
    seg = persona.get("segment", {})
    seg_text = ("; ".join(f"{k}={v}" for k, v in seg.items())) if isinstance(seg, dict) and seg else ""
    return base + (
        "\n\nYou have just been given a complete walkthrough of the product and its FAQ (above) — "
        "you know what it does, costs, and how it works. A researcher will now show you specific "
        "choices about the product and ask which you'd prefer. "
        + (f"For reference, you are: {seg_text}. " if seg_text else "")
        + "Judge as exactly this person."
    )


# ── Prompts per experiment type ───────────────────────────────────────────────

def _variant_lines(order):
    return "\n".join(f"({string.ascii_uppercase[i]}) {v['label']}: {v['desc']}" for i, v in enumerate(order))


def build_user_prompt(exp, order):
    head = f"{exp.get('context', '')}\n\n{exp['question']}\n\n"
    if exp["type"] == "head_to_head":
        return (head + "Here are the two options:\n\n" + _variant_lines(order) + "\n\n"
                "Answer using EXACTLY these labels, each on its own line:\n\n"
                "CHOICE: [ONLY the single letter of the option you'd rather have — just the letter]\n"
                "STRENGTH: [one of: strong / lean / toss-up]\n"
                "WHY: [2-3 sentences — what makes you pick it, and what you don't like about the other]\n"
                "FLIP: [what would make you switch — or 'nothing']")
    if exp["type"] == "rank":
        return (head + "The options:\n\n" + _variant_lines(order) + "\n\n"
                "Answer using EXACTLY these labels, each on its own line:\n\n"
                "RANKING: [ONLY the option letters MOST to LEAST wanted, separated by '>', nothing else. "
                'e.g. "C > A > D > B". Include every letter exactly once.]\n'
                "TOP_WHY: [1-2 sentences: why your #1 is #1]\n"
                "BOTTOM_WHY: [1-2 sentences: why your last place is last — be blunt, this is the cut signal]")
    return (head + "Answer using EXACTLY these labels, each on its own line:\n\n"
            "WANT_NEXT: [the single thing you most want built or changed next — specific, concrete]\n"
            "WHY: [1-2 sentences: why that matters to you]\n"
            "QUESTIONS: [anything you'd still want to ask before committing; number them. 'None' if none.]")


# ── Parsing / decoding (ported — these carry real bug fixes) ──────────────────

def parse_labeled(raw, labels):
    out = {lab.lower(): "" for lab in labels}
    label_map = {lab.upper(): lab.lower() for lab in labels}
    current, lines = None, []

    def flush():
        if current is not None:
            out[current] = " ".join(lines).strip()

    for line in (raw or "").strip().split("\n"):
        s = line.strip()
        matched = next((label_map[k] for k in label_map if s.upper().startswith(k)), None)
        if matched:
            flush()
            current, lines = matched, []
            after = s.split(":", 1)
            if len(after) == 2 and after[1].strip():
                lines.append(after[1].strip())
        elif s and current is not None:
            lines.append(s)
    flush()
    return out


def _choice_variant(text, order):
    valid = list(string.ascii_uppercase[:len(order)])
    t = (text or "").strip()
    if not t:
        return None
    m = re.match(r"^[\(\[]?([A-Za-z])[\]\)]?(?:[\s.:)\]\-]|$)", t)
    if m and m.group(1).upper() in valid:
        return order[valid.index(m.group(1).upper())]["id"]
    for tok in re.findall(r"\b([A-Za-z])\b", t):
        if tok.upper() in valid:
            return order[valid.index(tok.upper())]["id"]
    low = t.lower()
    hits = [v for v in order if v["label"].lower() in low or str(v["id"]).lower() in low]
    return hits[0]["id"] if len(hits) == 1 else None


def _rank_variant_ids(text, order):
    valid = list(string.ascii_uppercase[:len(order)])
    norm = (text or "").replace("&gt;", ">").replace("→", ">").replace("›", ">")
    out, seen = [], set()
    for seg in re.split(r"[>,]", norm):
        s = seg.strip()
        if not s:
            continue
        head = s.split("(")[0].strip()
        letter = None
        m = re.match(r"^[\(\[]?([A-Za-z])[\]\)]?(?:[\s.:)\]\-]|$)", head)
        if m and m.group(1).upper() in valid:
            letter = m.group(1).upper()
        else:
            low = s.lower()
            lab = [v for v in order if v["label"].lower() in low]
            if len(lab) == 1:
                letter = valid[order.index(lab[0])]
        if letter and letter not in seen:
            seen.add(letter)
            out.append(order[valid.index(letter)]["id"])
    return out


def _norm_strength(text):
    t = (text or "").lower()
    if "strong" in t:
        return "strong"
    if any(w in t for w in ("toss", "either", "neither", "barely")):
        return "toss-up"
    return "lean"


def decode_judgment(exp, order, raw):
    rec = {"choice": None, "strength": "", "why": "", "flip": "", "ranking": [],
           "ranking_provided": [], "top_why": "", "bottom_why": "", "want_next": "",
           "open_why": "", "questions": [], "parse_ok": True}
    if exp["type"] == "head_to_head":
        p = parse_labeled(raw, ["CHOICE", "STRENGTH", "WHY", "FLIP"])
        rec["choice"] = _choice_variant(p["choice"], order)
        rec["strength"] = _norm_strength(p["strength"])
        rec["why"], rec["flip"] = p["why"], p["flip"]
        rec["parse_ok"] = rec["choice"] is not None
    elif exp["type"] == "rank":
        p = parse_labeled(raw, ["RANKING", "TOP_WHY", "BOTTOM_WHY"])
        ranked = _rank_variant_ids(p["ranking"], order)
        rec["ranking_provided"] = ranked
        rec["ranking"] = ranked + [v["id"] for v in order if v["id"] not in ranked]
        rec["top_why"], rec["bottom_why"] = p["top_why"], p["bottom_why"]
        rec["parse_ok"] = len(ranked) == len(order)
    else:
        p = parse_labeled(raw, ["WANT_NEXT", "WHY", "QUESTIONS"])
        rec["want_next"], rec["open_why"] = p["want_next"], p["why"]
        qt = p["questions"].strip()
        if qt and qt.lower().rstrip(".") != "none":
            qs = [q.strip() for q in re.split(r"(?:^|\s)\d+[.)]\s*", qt) if q.strip()]
            rec["questions"] = qs or [qt]
        rec["parse_ok"] = bool(rec["want_next"])
    return rec


# ── Deterministic tallies (ported, segment axes = brief.audience_axes) ────────

def _pct(n, total):
    return round(100.0 * n / total, 1) if total else 0.0


def _winner(counter):
    if not counter:
        return None, False
    top = max(counter.values())
    leaders = [k for k, v in counter.items() if v == top]
    return leaders[0], len(leaders) > 1


def analyze_h2h(exp, js, axes):
    variants = exp["variants"]
    vids = [v["id"] for v in variants]
    label = {v["id"]: v["label"] for v in variants}
    valid = [j for j in js if j.get("choice") in vids]
    n = len(valid)
    tally = Counter({v: 0 for v in vids})
    strength = Counter({"strong": 0, "lean": 0, "toss-up": 0})
    quotes = defaultdict(list)
    for j in valid:
        tally[j["choice"]] += 1
        strength[j.get("strength", "lean")] += 1
        if j.get("why"):
            quotes[j["choice"]].append({"name": j.get("name", ""), "why": j["why"]})
    winner, tie = _winner(tally)
    segments = {}
    for axis in axes:
        key, per = axis["key"], {}
        for val in axis.get("values", []):
            sub = [j for j in valid if j.get(key) == val]
            if not sub:
                continue
            c = Counter({v: 0 for v in vids})
            for j in sub:
                c[j["choice"]] += 1
            w, t = _winner(c)
            per[val] = {"n": len(sub), "winner": w, "winner_label": label.get(w, ""),
                        "win_pct": {v: _pct(c[v], len(sub)) for v in vids}}
        if per:
            segments[key] = {"label": axis["label"], "values": per}
    return {"variants": variants, "n": n, "tally": dict(tally),
            "win_pct": {v: _pct(tally[v], n) for v in vids}, "winner": winner,
            "winner_label": label.get(winner, ""), "tie": tie,
            "strength_dist": dict(strength), "segments": segments,
            "quotes": {v: quotes[v][:3] for v in vids}}


def analyze_rank(exp, js, axes):
    variants = exp["variants"]
    vids = [v["id"] for v in variants]
    label = {v["id"]: v["label"] for v in variants}
    valid = [j for j in js if j.get("ranking") and j.get("parse_ok")]
    rank_sum = {v: 0 for v in vids}
    rank_cnt = {v: 0 for v in vids}
    for j in valid:
        for idx, vid in enumerate([v for v in j["ranking"] if v in rank_sum]):
            rank_sum[vid] += idx + 1
            rank_cnt[vid] += 1
    avg = {v: round(rank_sum[v] / rank_cnt[v], 2) if rank_cnt[v] else None for v in vids}
    order = sorted(vids, key=lambda v: (avg[v] if avg[v] is not None else 99))
    return {"variants": variants, "n": len(valid), "avg_rank": avg, "order": order,
            "most_wanted_label": label.get(order[0], "") if order else "",
            "kill_label": label.get(order[-1], "") if order else "",
            "top_quotes": [{"name": j.get("name", ""), "why": j["top_why"]} for j in valid if j.get("top_why")][:5],
            "bottom_quotes": [{"name": j.get("name", ""), "why": j["bottom_why"]} for j in valid if j.get("bottom_why")][:5]}


def analyze_open(exp, js, axes):
    valid = [j for j in js if j.get("want_next")]
    return {"n": len(valid),
            "wants": [{"name": j.get("name", ""), "want": j["want_next"], "why": j.get("open_why", "")} for j in valid],
            "questions": [q for j in js for q in j.get("questions", [])]}


# ── Report ────────────────────────────────────────────────────────────────────

def build_report(path, brief, experiments, results) -> str:
    body = ["<h2>Overview</h2><div class='card'>"]
    body.append(f"<p class='kv'>{len(experiments)} experiments judged by the population</p></div>")
    flags = []

    for exp, det in zip(experiments, results):
        body.append("<div class='card'>")
        body.append(f"<h3>{R.esc(exp.get('title', ''))} <span class='tag'>{R.esc(exp['type'])}</span></h3>")
        body.append(f"<p class='kv'>{R.esc(exp.get('question', ''))}</p>")
        if exp["type"] == "head_to_head":
            body.append(f"<p>Winner: <span class='score'>{R.esc(det.get('winner_label', '?'))}</span> "
                        f"<span class='muted'>{R.esc(det.get('win_pct', {}))} (n={det.get('n', 0)}, directional)</span></p>")
            for key, ax in det.get("segments", {}).items():
                splits = "; ".join(f"{val}: {d['winner_label']}" for val, d in ax["values"].items())
                body.append(f"<p class='kv'>By {R.esc(ax['label'])}: {R.esc(splits)}</p>")
        elif exp["type"] == "rank":
            order_labels = [R.esc(next((v['label'] for v in det['variants'] if v['id'] == vid), vid))
                            for vid in det.get("order", [])]
            body.append("<p>Priority (best→worst): " + " &gt; ".join(order_labels) + "</p>")
            body.append(f"<p class='kv'>Build next: <b>{R.esc(det.get('most_wanted_label', ''))}</b> · "
                        f"cut candidate: {R.esc(det.get('kill_label', ''))}</p>")
        else:
            for q in det.get("questions", []):
                flags.append(q)
            for w in det.get("wants", [])[:8]:
                body.append(f"<blockquote>{R.esc(w['want'])} <span class='muted'>— {R.esc(w['name'])}</span></blockquote>")
        body.append("</div>")

    body.append("<div class='flagsec'>")
    body.append("<h2>⚑ Open questions — probe these with real humans</h2>")
    if flags:
        for q in flags[:40]:
            body.append(f"<div class='card flagcard'>{R.esc(q)}</div>")
    else:
        body.append("<p class='muted'>No open questions captured.</p>")
    body.append("</div>")
    return R.write_report(path, f"Arena — {brief.get('name', 'Product')}",
                          "Synthetic Society · City 3 · A/B feature preference", "".join(body))


# ── City ──────────────────────────────────────────────────────────────────────

@register
class ArenaCity(City):
    id = "arena"
    name = "Arena"
    description = "A/B feature-preference testing: which direction should we build next?"
    n_label = "judges"
    default_n = 40
    min_n = 6

    def setup_questions(self):
        return [wizard.Question(
            key="experiments_path", kind="text", default="",
            prompt="Path to an experiments JSON (blank = auto-design from your product)",
            help="A file lets you control exactly what's tested; blank auto-generates ~5 experiments")]

    def project_cost(self, n, answers, model) -> CostProjection:
        import math
        path = (answers or {}).get("experiments_path", "").strip()
        e = DEFAULT_EXPERIMENTS
        if path and os.path.isfile(path):
            try:
                e = len(load_json(path)) or DEFAULT_EXPERIMENTS
            except Exception:
                pass
        p = CostProjection(model=model)
        if not path:
            p.add("experiment design", 1, 1500, 3000)
        p.add("judge generation", math.ceil(n / PERSONA_BATCH), 1400, 6500)
        p.add("judgments", n * e, 2600, 500)
        p.add("synthesis", e + 1, 2500, 1500)
        return p

    async def run(self, ctx: RunContext) -> str:
        client = get_client()
        m, model, brief, d = ctx.meter, ctx.model, ctx.brief, ctx.out_dir
        axes = _seg_axes(brief)

        path = (ctx.answers or {}).get("experiments_path", "").strip()
        if path and os.path.isfile(path):
            print(f"\n  [1/4] Loading experiments from {path}...")
            experiments = load_json(path)
        else:
            print("\n  [1/4] Auto-designing experiments from your product...")
            k = 3 if ctx.limit else DEFAULT_EXPERIMENTS
            experiments = await design_experiments(client, model, brief, m, k=k)
        save_json(os.path.join(d, "experiments.json"), experiments)

        print(f"  [2/4] Generating {ctx.n} judges...")
        judges = await gen_judges(client, model, brief, ctx.n, m)
        save_json(os.path.join(d, "judges.json"), judges)

        print("  [3/4] Running judgments (counterbalanced, parallel)...")
        brief_block = cache_block(informed_brief(brief))
        seg_keys = [a["key"] for a in axes]

        async def judge(task):
            idx, persona, exp = task
            order = list(exp.get("variants", []))
            random.shuffle(order)
            system = [brief_block, text_block(build_judge_system(persona, brief, ctx.grounding, idx))]
            raw = await metered_call(client, model, system, build_user_prompt(exp, order),
                                     m, max_tokens=MAX_JUDGMENT_TOKENS)
            rec = {"persona_id": persona.get("id"), "name": persona.get("name", ""),
                   "experiment_id": exp["id"], "type": exp["type"]}
            for k in seg_keys:
                rec[k] = persona.get(k)
            rec.update(decode_judgment(exp, order, raw))
            return rec

        tasks = [(i, p, exp) for exp in experiments for i, p in enumerate(judges)]
        all_j = await run_batch(tasks, judge, max_concurrent=20, label="judgments")
        save_json(os.path.join(d, "judgments.json"), all_j)

        print("  [4/4] Tallying + segment splits...")
        by_exp = defaultdict(list)
        for j in all_j:
            by_exp[j["experiment_id"]].append(j)
        results = []
        for exp in experiments:
            js = by_exp.get(exp["id"], [])
            if exp["type"] == "head_to_head":
                results.append(analyze_h2h(exp, js, axes))
            elif exp["type"] == "rank":
                results.append(analyze_rank(exp, js, axes))
            else:
                results.append(analyze_open(exp, js, axes))
        save_json(os.path.join(d, "analysis.json"), {"experiments": [e["id"] for e in experiments]})

        return build_report(os.path.join(d, "report.html"), brief, experiments, results)
