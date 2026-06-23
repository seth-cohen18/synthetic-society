"""City 1 — Census.

A broad two-round survey across a diverse persona pool.
  Round 1: open pain-point questions, NO product mention.
  Cluster: group by emotional reality / core barrier (not by surface demographics).
  Round 2: cluster reps see a personalized product intro + the objection FAQ, then
           react, answer a probe, raise remaining questions, and rate likelihood.
The high-value output is the questions/objections that are NOT in the FAQ — the
blind spots to take into real interviews.
"""

import asyncio
import math
import os
import re

from synthsociety.budget import metered_call
from synthsociety.cities import register
from synthsociety.cities.base import City, RunContext
from synthsociety.client import extract_json, get_client, run_batch, save_json
from synthsociety.cost import CostProjection
from synthsociety.persona import (
    build_persona_system_prompt, objections_faq, persona_generation_prompt, persona_summary,
)
from synthsociety import report as R

PERSONA_BATCH = 25
MIN_CLUSTERS, MAX_CLUSTERS = 5, 10


def _n_clusters(n: int) -> int:
    return min(MAX_CLUSTERS, max(2 if n < 10 else MIN_CLUSTERS, n // 6))


# ── Pipeline steps ────────────────────────────────────────────────────────────

async def gen_round1_questions(client, model, brief, meter) -> list:
    system = "You design open, unbiased qualitative research questions."
    user = (
        f"Design 6 open-ended interview questions to understand the life, habits, and "
        f"PAIN POINTS of people in this space — with NO mention of any product or solution.\n\n"
        f"Category: {brief.get('category', '')}\n"
        f"Audience: {brief.get('audience', '')}\n\n"
        "The questions must NOT presuppose the person wants a solution, must not lead, and "
        "must surface the emotional reality and real barriers. Return ONLY a JSON array of "
        "6 question strings."
    )
    raw = await metered_call(client, model, system, user, meter, max_tokens=800)
    qs = extract_json(raw)
    return [q for q in qs if isinstance(q, str)][:8] or [
        f"Tell me about your experience with {brief.get('category', 'this area')}.",
    ]


async def gen_personas(client, model, brief, n, meter) -> list:
    personas = []
    batches = math.ceil(n / PERSONA_BATCH)
    for b in range(batches):
        start = b * PERSONA_BATCH
        count = min(PERSONA_BATCH, n - start)
        system, user = persona_generation_prompt(brief, count, start, start + count - 1)
        raw = await metered_call(client, model, system, user, meter, max_tokens=8000)
        try:
            personas.extend(extract_json(raw))
        except Exception:
            pass
    return personas[:n]


async def run_round1(client, model, brief, personas, questions, grounding, meter) -> list:
    q_text = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
    user = (f"Please answer each of these questions:\n\n{q_text}\n\n"
            "Answer each directly in order, separated by blank lines. No labels, no numbering.")

    async def worker(task):
        idx, persona = task
        system = build_persona_system_prompt(persona, brief, grounding, seed_offset=idx)
        raw = await metered_call(client, model, system, user, meter, max_tokens=800)
        answers = [a.strip() for a in raw.strip().split("\n\n") if a.strip()]
        rmap = {f"q{i + 1}": (answers[i] if i < len(answers) else "") for i in range(len(questions))}
        return {"persona_id": persona.get("id", f"p_{idx:03d}"),
                "name": persona.get("name", ""), "responses": rmap}

    tasks = list(enumerate(personas))
    return await run_batch(tasks, worker, max_concurrent=20, label="round 1")


def _fmt_for_analysis(personas, responses) -> str:
    pmap = {p.get("id"): p for p in personas}
    lines = []
    for r in responses:
        p = pmap.get(r["persona_id"], {})
        lines.append(f"--- {r['persona_id']} ---")
        lines.append(f"Background: {persona_summary(p)}")
        for k, a in r["responses"].items():
            lines.append(f"{k}: {a}")
        lines.append("")
    return "\n".join(lines)


async def cluster(client, model, brief, personas, responses, meter) -> list:
    n = len(responses)
    lo = max(2, min(_n_clusters(n), n // 2 or 2))
    hi = max(lo + 1, min(MAX_CLUSTERS, n - 1)) if n > 2 else lo
    formatted = _fmt_for_analysis(personas, responses)
    system = "You are a qualitative research analyst specializing in user research synthesis."
    user = (
        f"Group these survey responses into {lo}-{hi} clusters by similarity of attitudes, "
        "concerns, and overall perspective. Cluster on the EMOTIONAL REALITY and CORE BARRIER "
        "people share — NOT on surface demographics. People with different backgrounds who share "
        "the same underlying motivation or barrier belong together.\n\n"
        "For each cluster: a theme name capturing the emotional reality; the member persona ids; "
        "1 representative id (most authentically captures the cluster); and importance 1-10 "
        "(how many share it + emotional intensity + relevance to the product).\n\n"
        f"Responses:\n{formatted}\n\n"
        'Return ONLY a JSON array: [{"cluster_id":1,"theme":"...","representative_id":"p_XXX",'
        '"member_ids":["p_XXX"],"importance_score":8}]. Every persona in exactly one cluster.'
    )
    raw = await metered_call(client, model, system, user, meter, max_tokens=4000)
    clusters = extract_json(raw)
    pmap = {p.get("id"): p for p in personas}
    for c in clusters:
        c["size"] = len(c.get("member_ids", []))
        c["rep"] = pmap.get(c.get("representative_id"), {})
    clusters.sort(key=lambda c: c.get("importance_score", 0), reverse=True)
    return clusters


async def gen_followups(client, model, brief, clusters, resp_map, questions, meter) -> dict:
    faq_topics = "; ".join(o.get("objection", "") for o in brief.get("known_objections", []))
    pitch = brief.get("pitch", brief.get("one_liner", ""))

    async def worker(task):
        c = task
        rep = c.get("rep", {})
        r1 = resp_map.get(c.get("representative_id"), {})
        r1_text = "\n".join(f"Q: {questions[i]}\nA: {r1.get(f'q{i + 1}', '')}"
                            for i in range(len(questions)))
        system = "You are a user research specialist preparing personalized interview materials."
        user = (
            f"A cluster of {c.get('size')} people shares this theme: \"{c.get('theme')}\"\n\n"
            f"Representative:\n{persona_summary(rep)}\n\n"
            f"Their Round 1 answers (before seeing any product):\n{r1_text}\n\n"
            f"Core product facts (do NOT add/exaggerate beyond these):\n\"{pitch}\"\n\n"
            "Produce: (1) PERSONALIZED_INTRO — rewrite the product description to LEAD with the "
            "angle most relevant to this person's pain points; all core facts present, no invented "
            "features, 2-4 sentences. (2) PROBE_QUESTION — one open question tying their key Round 1 "
            "pain point to the product, NOT leading, NOT about surface concerns already in the FAQ "
            f"({faq_topics or 'n/a'}); targets deeper motivation/identity/habit.\n\n"
            'Return ONLY JSON: {"personalized_intro":"...","probe_question":"..."}'
        )
        raw = await metered_call(client, model, system, user, meter, max_tokens=500)
        try:
            d = extract_json(raw)
        except Exception:
            d = {}
        return c.get("representative_id"), {
            "cluster_id": c.get("cluster_id"), "cluster_theme": c.get("theme"),
            "cluster_size": c.get("size"),
            "personalized_intro": d.get("personalized_intro", pitch),
            "probe_question": (d.get("probe_question") or "").strip(),
        }

    pairs = await run_batch(clusters, worker, max_concurrent=20, label="followups")
    return {rid: data for rid, data in pairs}


def _parse_round2(raw: str) -> dict:
    out = {"first_reaction": "", "probe_answer": "", "questions": [], "likelihood_raw": ""}
    cur, buf = None, []
    keymap = {"FIRST REACTION": "first_reaction", "FOLLOW-UP": "probe_answer",
              "QUESTIONS": "questions", "LIKELIHOOD": "likelihood_raw"}

    def flush():
        if not cur:
            return
        if cur == "questions":
            qs = []
            for ln in buf:
                c = ln.lstrip("0123456789.-) ").strip()
                if c and c.lower() != "none":
                    qs.append(c)
            out["questions"] = qs
        else:
            out[cur] = " ".join(buf).strip()

    for line in raw.strip().split("\n"):
        s = line.strip()
        hit = next((v for k, v in keymap.items() if s.upper().startswith(k)), None)
        if hit:
            flush()
            cur, buf = hit, []
        elif s and cur:
            buf.append(s)
    flush()
    txt = out.pop("likelihood_raw", "")
    m = re.search(r"\b(10|[1-9])\b", txt)
    out["likelihood_score"] = int(m.group(1)) if m else None
    out["main_objection"] = txt
    return out


async def run_round2(client, model, brief, clusters, followups, personas, resp_map, questions, meter) -> list:
    pmap = {p.get("id"): p for p in personas}
    faq = objections_faq(brief)

    async def worker(task):
        rid = task
        persona = pmap.get(rid, {})
        fu = followups.get(rid, {})
        r1 = resp_map.get(rid, {})
        r1_text = "\n".join(f"Q: {questions[i]}\nA: {r1.get(f'q{i + 1}', '')}"
                            for i in range(len(questions)))
        system = build_persona_system_prompt(persona, brief) + \
            f"\n\nFor context, your earlier survey answers were:\n{r1_text}"
        user = (
            f"{fu.get('personalized_intro', '')}\n\n{faq}\n\n"
            "You've now read the product description and the answers to common concerns. Respond "
            "from your genuine perspective. If the answers don't fully convince you, say exactly why "
            "and push. Do NOT just repeat a concern already answered above — engage with the answer "
            "first. The most valuable feedback is something the team hasn't thought of.\n\n"
            "Answer in order with these exact labels:\n\n"
            "FIRST REACTION:\n[honest reaction to the product AND the answers — 2-4 sentences]\n\n"
            f"FOLLOW-UP:\n[answer this: {fu.get('probe_question', '')} — 2-4 sentences]\n\n"
            "QUESTIONS:\n[remaining questions/concerns NOT already in the FAQ; challenge any answer "
            "that didn't satisfy you. Numbered list, or 'None.']\n\n"
            "LIKELIHOOD:\n[1-10 how likely you are to try it given everything; state the number, then "
            "the single most important remaining barrier NOT already in the FAQ]"
        )
        raw = await metered_call(client, model, system, user, meter, max_tokens=900)
        p = _parse_round2(raw)
        return {"persona_id": rid, "name": persona.get("name", ""),
                "cluster_theme": fu.get("cluster_theme"), "cluster_size": fu.get("cluster_size"),
                "first_reaction": p["first_reaction"], "probe_answer": p["probe_answer"],
                "questions_about_product": p["questions"],
                "likelihood_score": p["likelihood_score"], "main_objection": p["main_objection"]}

    rids = list(followups.keys())
    res = await run_batch(rids, worker, max_concurrent=20, label="round 2")
    return [r for r in res if r]


# ── Report ────────────────────────────────────────────────────────────────────

def build_report(path, brief, clusters, round2, n_personas) -> str:
    scores = [r["likelihood_score"] for r in round2 if r.get("likelihood_score")]
    avg = (sum(scores) / len(scores)) if scores else 0
    body = []

    body.append("<h2>Overview</h2><div class='card'>")
    body.append(f"<p class='kv'>{n_personas} personas surveyed · {len(clusters)} clusters · "
                f"{len(round2)} product reactions</p>")
    if scores:
        body.append(f"<p>Mean likelihood (ordinal, 1-10): <span class='score'>{avg:.1f}</span> "
                    f"<span class='muted'>— directional only</span></p>")
    body.append("</div>")

    body.append("<h2>Round 1 — clusters (emotional reality & core barriers)</h2>")
    for c in clusters:
        body.append("<div class='card'>")
        body.append(f"<h3>{R.esc(c.get('theme', ''))} "
                    f"<span class='tag'>importance {R.esc(c.get('importance_score', '?'))}/10</span> "
                    f"<span class='tag'>{R.esc(c.get('size', 0))} people</span></h3>")
        rep = c.get("rep", {})
        if rep:
            body.append(f"<p class='kv'>rep: {R.esc(persona_summary(rep))}</p>")
        body.append("</div>")

    flags = []
    body.append("<h2>Round 2 — reactions to the product</h2>")
    for r in round2:
        body.append("<div class='card'>")
        body.append(f"<h3>{R.esc(r.get('cluster_theme', ''))} "
                    f"<span class='tag'>likelihood {R.esc(r.get('likelihood_score', '?'))}/10</span></h3>")
        if r.get("first_reaction"):
            body.append(f"<blockquote>{R.esc(r['first_reaction'])}</blockquote>")
        if r.get("main_objection"):
            body.append(f"<p class='kv'>Main remaining barrier: {R.esc(r['main_objection'])}</p>")
        body.append("</div>")
        for q in r.get("questions_about_product", []):
            flags.append((r.get("cluster_theme", ""), q))

    body.append("<div class='flagsec'>")
    body.append("<h2>⚑ Novel objections &amp; questions — probe these with real humans</h2>")
    if flags:
        body.append("<p class='muted'>Raised AFTER the objection FAQ, so not already answered. "
                    "These are your blind-spot candidates.</p>")
        for theme, q in flags:
            body.append(f"<div class='card flagcard'><span class='tag'>{R.esc(theme)}</span> "
                        f"{R.esc(q)}</div>")
    else:
        body.append("<p class='muted'>No novel questions surfaced — either coverage was strong "
                    "or the population was too small. Try a larger run.</p>")
    body.append("</div>")

    return R.write_report(path, f"Census — {brief.get('name', 'Product')}",
                          "Synthetic Society · City 1 · two-round survey", "".join(body))


# ── City ──────────────────────────────────────────────────────────────────────

@register
class CensusCity(City):
    id = "census"
    name = "Census"
    description = "broad two-round survey: who are my users, their pain, and would they want this?"
    n_label = "personas"
    default_n = 30
    min_n = 6

    def project_cost(self, n, answers, model) -> CostProjection:
        c = _n_clusters(n)
        p = CostProjection(model=model)
        p.add("round-1 question design", 1, 1500, 600)
        p.add("persona generation", math.ceil(n / PERSONA_BATCH), 1400, 6500)
        p.add("round-1 survey", n, 900, 700)
        p.add("outlier + cluster analysis", 2, max(800, n * 130), 3000)
        p.add("personalized followups", c, 1600, 350)
        p.add("round-2 reactions", c, 2600, 800)
        return p

    async def run(self, ctx: RunContext) -> str:
        client = get_client()
        m, model, brief = ctx.meter, ctx.model, ctx.brief
        d = ctx.out_dir

        print("\n  [1/6] Designing Round 1 questions...")
        questions = await gen_round1_questions(client, model, brief, m)
        save_json(os.path.join(d, "questions.json"), questions)

        print(f"  [2/6] Generating {ctx.n} personas...")
        personas = await gen_personas(client, model, brief, ctx.n, m)
        save_json(os.path.join(d, "personas.json"), personas)

        print("  [3/6] Round 1 survey...")
        r1 = await run_round1(client, model, brief, personas, questions, ctx.grounding, m)
        save_json(os.path.join(d, "round1.json"), r1)
        resp_map = {r["persona_id"]: r["responses"] for r in r1}

        print("  [4/6] Clustering on emotional reality...")
        clusters = await cluster(client, model, brief, personas, r1, m)
        save_json(os.path.join(d, "clusters.json"), clusters)

        print("  [5/6] Personalized Round 2 materials...")
        followups = await gen_followups(client, model, brief, clusters, resp_map, questions, m)

        print("  [6/6] Round 2 reactions...")
        round2 = await run_round2(client, model, brief, clusters, followups,
                                  personas, resp_map, questions, m)
        save_json(os.path.join(d, "round2.json"), round2)

        return build_report(os.path.join(d, "report.html"), brief, clusters, round2, len(personas))
