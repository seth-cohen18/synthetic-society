"""City 2 — Forum.

Moderated focus groups that actually discuss (members speak in sequence, each
reacting to the running transcript), then a shared "classroom" where everyone sees
the product + objection FAQ + a digest of what the groups already raised, and reacts.

Generalized from the original: the groups (and their tailored, product-aware
questions) are DESIGNED from the brief rather than hardcoded. Group composition
segments the user's own audience.
"""

import argparse  # noqa: F401  (parity; unused)
import os
import random
import re

from synthsociety.budget import metered_call, BudgetExceeded
from synthsociety.cities import register
from synthsociety.cities.base import City, RunContext
from synthsociety.client import cache_block, extract_json, get_client, run_batch, save_json, text_block
from synthsociety.cost import CostProjection
from synthsociety.persona import build_persona_system_prompt, objections_faq, persona_generation_prompt
from synthsociety import report as R

GROUP_SIZE = 5
Q_PER_GROUP = 7
N_PROBES = 2
TRANSCRIPT_WINDOW = 14
MAX_TURN_TOKENS = 220

PROBE_LINES = [
    "I'm hearing some different takes here — can a couple of you react to what was just said?",
    "Let's dig into that a bit: who agrees, who doesn't, and why?",
]


def _n_groups(n: int) -> int:
    return max(2, round(n / GROUP_SIZE))


# ── Step A: design the groups + their questions from the brief ────────────────

async def design_groups(client, model, brief, n_groups, q_per_group, meter) -> list:
    system = ("You design focus-group studies. Given a product and its audience, you split "
              "the audience into distinct, meaningful focus groups and write tailored, "
              "product-aware discussion questions for each.")
    user = (
        f"Product: {brief.get('name')} — {brief.get('one_liner')}\n"
        f"Category: {brief.get('category')}\nAudience: {brief.get('audience')}\n"
        f"Segmentation axes: {brief.get('audience_axes')}\n\n"
        f"Design exactly {n_groups} focus groups that segment this audience along the most "
        "revealing lines (e.g. by a key segment axis, by attitude toward the product, or by "
        "experience level). Each group must be internally coherent but distinct from the others.\n\n"
        f"For each group also write {q_per_group} open-ended, NON-leading discussion questions "
        "TAILORED to that group's reality — the participants have already been fully walked "
        "through the product, so questions probe: would-you-actually-use-it, where it fits or "
        "doesn't in their life, trust, price/worth, what's MISSING, what feels unnecessary, and "
        "what they'd change. Never accusatory, never cross-stance.\n\n"
        'Return ONLY a JSON array: [{"id":"snake_case","display_name":"...","composition":'
        '"who is in this group, 1-2 sentences","focus":"the stance/segment this group holds",'
        '"questions":["...", ...]}]'
    )
    raw = await metered_call(client, model, system, user, meter, max_tokens=3500)
    groups = extract_json(raw)
    for i, g in enumerate(groups):
        g.setdefault("id", f"group_{i+1}")
        g.setdefault("questions", [])
    return groups


async def gen_group_personas(client, model, brief, group, size, meter) -> list:
    system, _ = persona_generation_prompt(brief, size, 0, size - 1)
    user = (
        f"Generate {size} diverse personas for ONE focus group.\n\n"
        f"Product: {brief.get('name')} — {brief.get('one_liner')}\n"
        f"Audience: {brief.get('audience')}\n\n"
        f"This group's composition: {group.get('composition')}\n"
        f"They share this stance/segment: {group.get('focus')}\n\n"
        "Make them typical, representative members of that group — vary everything else "
        "(personality, background, enthusiasm) within the common range. Avoid rare edge cases.\n\n"
        'Return ONLY a JSON array. Each persona: {"name","age","gender","location","occupation",'
        '"income_level","tech_comfort","personality_trait","relationship":"their relationship to '
        f'{brief.get("category","this")}","segment":{{}},"notes"}}'
    )
    raw = await metered_call(client, model, system, user, meter, max_tokens=6000)
    try:
        personas = extract_json(raw)
    except Exception:
        return []
    for i, p in enumerate(personas):
        p["id"] = f"f_{group['id']}_{i+1}"
        p["group_id"] = group["id"]
    return personas


# ── Step B: the discussion engine (ported, generalized) ───────────────────────

def informed_brief(brief) -> str:
    return (
        f"You have just been given a complete walkthrough of {brief.get('name', 'the product')}. "
        f"Here is exactly what you were shown:\n\n{brief.get('pitch', '')}\n\n"
        f"{objections_faq(brief)}\n\n"
        "You ALREADY KNOW what it does, what it costs, how it works, and the answers to the "
        "common questions. In this discussion do NOT ask about anything already covered — you "
        "know it. Focus on your genuine reaction: whether you'd actually use it, where it fits "
        "or doesn't in your real life, what's MISSING, what feels unnecessary or off, what you'd change."
    )


def member_prompt(persona, group, brief, grounding, idx) -> str:
    base = build_persona_system_prompt(persona, brief, grounding, seed_offset=idx)
    stance = (f" You genuinely hold this group's view ({group.get('focus', '')}); don't abandon "
              "it just to agree with the group, though a genuinely good point can move you.")
    return base + (
        f"\n\n{stance}\n\nYou are in a small focus group of people who have ALL just been walked "
        "through the product and its FAQ — you know what it does. A neutral moderator asks "
        "questions and you all talk it through together. Speak like a real person in a group — "
        "casual and specific, sometimes reacting directly to what another person just said (you "
        "can use their first name). Keep each turn to 2-4 sentences. Don't lecture, don't repeat "
        "a point already made unless you're pushing back or adding to it. Your honest, critical, "
        "or contrarian take is more useful than polite agreement."
    )


def render_transcript(turns, window) -> str:
    lines = []
    for t in turns[-window:]:
        who = "Moderator" if t["role"] == "moderator" else t["speaker"]
        lines.append(f"{who}: {t['text']}")
    return "\n".join(lines)


def turn_prompt(transcript_text, first) -> str:
    guide = ("You're the first to respond — answer the moderator's question honestly." if first
             else "Respond to the moderator's question and react to what others just said.")
    return (f"Here is the discussion so far:\n\n{transcript_text}\n\n{guide} Speak in 2-4 "
            "sentences, like a real person in the group. Reply with ONLY your own spoken words "
            "— no name label, no quote marks. Do NOT write a 'Moderator:' line and do NOT invent "
            "or speak for anyone else.")


def clean_turn(text, names) -> str:
    t = (text or "").strip()
    labels = ["Moderator"] + list(names) + [n.split()[0] for n in names if n]
    changed = True
    while changed:
        changed = False
        low = t.lower()
        for lab in labels:
            if low.startswith(lab.lower() + ":"):
                rest = t[len(lab) + 1:].lstrip()
                if lab.lower() == "moderator":
                    nl = rest.find("\n\n")
                    t = rest[nl + 2:].strip() if nl != -1 else rest.strip()
                else:
                    t = rest
                changed = True
                break
    return t.strip()


def probe_indices(n_questions) -> set:
    if N_PROBES <= 0 or n_questions < 3:
        return set()
    return {max(1, n_questions // 3), max(2, (2 * n_questions) // 3)} - {n_questions - 1}


async def run_group(client, model, brief, group, members, questions, grounding, meter) -> dict:
    names = [m["name"] for m in members]
    brief_block = cache_block(informed_brief(brief))
    prompts = {m["id"]: cache_block(member_prompt(m, group, brief, grounding, i))
               for i, m in enumerate(members)}
    transcript, probes, complete, reached = [], probe_indices(len(questions)), True, 0

    async def speak(member, first):
        up = turn_prompt(render_transcript(transcript, TRANSCRIPT_WINDOW), first)
        text = await metered_call(client, model, [brief_block, prompts[member["id"]]], up,
                                  meter, max_tokens=MAX_TURN_TOKENS)
        transcript.append({"role": "member", "speaker": member["name"], "persona_id": member["id"],
                           "text": clean_turn(text, names)})

    try:
        for qi, q in enumerate(questions):
            reached = qi + 1
            transcript.append({"role": "moderator", "speaker": "Moderator", "text": q})
            order = members[:]
            random.shuffle(order)
            for j, m in enumerate(order):
                await speak(m, first=(j == 0))
            if qi in probes and len(members) >= 2:
                transcript.append({"role": "moderator", "speaker": "Moderator",
                                   "text": random.choice(PROBE_LINES), "probe": True})
                for m in random.sample(members, 2):
                    await speak(m, first=False)
    except BudgetExceeded:
        complete = False
    return {"group_id": group["id"], "display_name": group.get("display_name", group["id"]),
            "focus": group.get("focus", ""), "members": members, "questions": questions,
            "transcript": transcript, "complete": complete}


# ── Step C: analysis (with novelty quarantine) ────────────────────────────────

def _roster(group) -> str:
    return "\n".join(f"- {m['name']}: {m.get('age')}y {m.get('gender')}, "
                     f"{m.get('occupation')}, {m.get('personality_trait')}" for m in group["members"])


def _full_transcript(group) -> str:
    return "\n".join(f"{'MODERATOR' if t['role'] == 'moderator' else t['speaker']}: {t['text']}"
                     for t in group["transcript"])


async def analyze_group(client, model, brief, group, meter) -> dict:
    sys_blocks = [cache_block(
        "You are a qualitative UX researcher analyzing a focus-group transcript. Be precise and "
        "honest; capture genuine disagreement and skepticism — do not smooth it into consensus.\n\n"
        "REFERENCE — concerns ALREADY addressed in the product FAQ. An objection is NOT novel if "
        "it is covered below:\n" + objections_faq(brief))]
    user = (
        f"FOCUS GROUP: {group['display_name']} (focus: {group.get('focus', '')})\n\n"
        f"MEMBERS:\n{_roster(group)}\n\nTRANSCRIPT:\n{_full_transcript(group)}\n\n"
        "Analyze. Return ONLY JSON: {"
        '"summary":"3-5 sentences","consensus":["..."],"tensions":["..."],'
        '"standout_quotes":[{"speaker":"First name","quote":"verbatim"}],'
        '"surfaced_objections":["short phrases"],"surfaced_questions":["..."],'
        '"novel_objections":["concerns NOT already in the FAQ reference — blind-spot candidates"]}'
    )
    try:
        raw = await metered_call(client, model, sys_blocks, user, meter, max_tokens=3000)
        data = extract_json(raw)
    except BudgetExceeded:
        raise
    except Exception:
        data = {"summary": "(could not parse analysis)", "consensus": [], "tensions": [],
                "standout_quotes": [], "surfaced_objections": [], "surfaced_questions": [],
                "novel_objections": []}
    data["group_id"] = group["group_id"]
    data["display_name"] = group["display_name"]
    return data


# ── Step D: classroom ─────────────────────────────────────────────────────────

def _dedup(items):
    seen, out = set(), []
    for x in items:
        k = x.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(x.strip())
    return out


def build_presentation(brief, analyses) -> str:
    raised = _dedup([o for a in analyses for o in a.get("surfaced_objections", [])]
                    + [q for a in analyses for q in a.get("surfaced_questions", [])])
    digest = "── ALREADY RAISED IN THE FOCUS GROUPS (known — do NOT just repeat) ──\n" + \
             "\n".join(f"  • {x}" for x in raised[:40])
    return (f"PRESENTATION — what you're shown:\n\n{brief.get('pitch', '')}\n\n"
            f"{objections_faq(brief)}\n\n{digest}")


def classroom_prompt() -> str:
    return (
        "You've now seen the presentation, the answers to common concerns, and the list of "
        "already-raised points. Respond honestly; engage with what was presented (challenge an "
        "answer if it doesn't satisfy you) but do NOT just repeat something already on the list. "
        "The most useful thing is a NEW reaction or concern.\n\n"
        "Answer each, using these exact labels:\n\n"
        "FIRST REACTION:\n[honest gut reaction — 2-4 sentences]\n\n"
        "INTERESTED:\n[Yes, No, or Maybe — and why, 1-2 sentences]\n\n"
        "QUESTIONS:\n[new questions NOT already on the list. Numbered, or 'None.']\n\n"
        "NOVEL CONCERN:\n[the single angle you did NOT see on the already-raised list — the thing "
        "the team probably hasn't considered, rooted in your specific life. 'None.' if you have none]\n\n"
        "LIKELIHOOD:\n[1-10 how likely you'd actually try it, then your single biggest remaining barrier]"
    )


def _parse_classroom(raw) -> dict:
    out = {"first_reaction": "", "interested_raw": "", "questions": [], "novel_concern": "", "lk": ""}
    keymap = {"FIRST REACTION": "first_reaction", "INTERESTED": "interested_raw",
              "QUESTIONS": "questions", "NOVEL CONCERN": "novel_concern", "LIKELIHOOD": "lk"}
    cur, buf = None, []

    def flush():
        if not cur:
            return
        if cur == "questions":
            out["questions"] = [c for ln in buf
                                for c in [ln.lstrip("0123456789.-) ").strip()]
                                if c and c.lower() != "none"]
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
    lk = out.pop("lk", "")
    m = re.search(r"\b(10|[1-9])\b", lk)
    out["likelihood_score"] = int(m.group(1)) if m else None
    out["main_barrier"] = lk
    ir = out.pop("interested_raw", "")
    fw = ir.strip().split()[0].lower().strip(".,:") if ir.strip() else ""
    out["interest"] = ("yes" if fw.startswith("y") else "no" if fw.startswith("n")
                       else "maybe" if fw.startswith("m") else "unclear")
    nc = out.get("novel_concern", "").strip()
    out["novel_concern"] = "" if nc.lower().rstrip(".") in ("none", "") else nc
    return out


async def run_classroom(client, model, brief, personas, analyses, meter) -> list:
    presentation = cache_block(build_presentation(brief, analyses))

    async def react(persona):
        system = [presentation, text_block(build_persona_system_prompt(persona, brief) +
                  "\n\nYou just sat through a presentation of the product with a handout answering "
                  "common concerns and a list of points prior groups already raised. React honestly "
                  "from your own perspective — skepticism and dealbreakers are more useful than praise.")]
        try:
            raw = await metered_call(client, model, system, classroom_prompt(), meter, max_tokens=900)
        except BudgetExceeded:
            return None
        p = _parse_classroom(raw)
        return {"persona_id": persona["id"], "name": persona["name"], "group_id": persona.get("group_id"),
                "first_reaction": p["first_reaction"], "interest": p["interest"],
                "questions": p["questions"], "novel_concern": p["novel_concern"],
                "likelihood_score": p["likelihood_score"], "main_barrier": p["main_barrier"]}

    res = await run_batch(personas, react, max_concurrent=8, label="classroom")
    return [r for r in res if r]


# ── Report ────────────────────────────────────────────────────────────────────

def build_report(path, brief, analyses, classroom) -> str:
    body = []
    scores = [r["likelihood_score"] for r in classroom if r.get("likelihood_score")]
    avg = sum(scores) / len(scores) if scores else 0
    interest = {k: sum(1 for r in classroom if r.get("interest") == k) for k in ("yes", "maybe", "no")}

    body.append("<h2>Overview</h2><div class='card'>")
    body.append(f"<p class='kv'>{len(analyses)} focus groups · {len(classroom)} classroom reactions</p>")
    if scores:
        body.append(f"<p>Mean likelihood (ordinal 1-10): <span class='score'>{avg:.1f}</span> "
                    f"<span class='muted'>— directional only</span></p>")
    body.append(f"<p class='kv'>Interest: {interest['yes']} yes · {interest['maybe']} maybe · {interest['no']} no</p>")
    body.append("</div>")

    body.append("<h2>Focus groups</h2>")
    for a in analyses:
        body.append("<div class='card'>")
        body.append(f"<h3>{R.esc(a.get('display_name', ''))}</h3>")
        body.append(f"<p>{R.esc(a.get('summary', ''))}</p>")
        if a.get("consensus"):
            body.append("<p class='kv'>Consensus:</p><ul>" +
                        "".join(f"<li>{R.esc(x)}</li>" for x in a["consensus"]) + "</ul>")
        if a.get("tensions"):
            body.append("<p class='kv'>Tensions:</p><ul>" +
                        "".join(f"<li>{R.esc(x)}</li>" for x in a["tensions"]) + "</ul>")
        for q in a.get("standout_quotes", [])[:3]:
            body.append(f"<blockquote>{R.esc(q.get('quote', ''))} <span class='muted'>— "
                        f"{R.esc(q.get('speaker', ''))}</span></blockquote>")
        body.append("</div>")

    flags = _dedup([o for a in analyses for o in a.get("novel_objections", [])]
                   + [r["novel_concern"] for r in classroom if r.get("novel_concern")])
    body.append("<div class='flagsec'>")
    body.append("<h2>⚑ Novel objections &amp; concerns — probe these with real humans</h2>")
    if flags:
        body.append("<p class='muted'>Not covered by the FAQ — your blind-spot candidates.</p>")
        for f in flags:
            body.append(f"<div class='card flagcard'>{R.esc(f)}</div>")
    else:
        body.append("<p class='muted'>No novel concerns surfaced — try a larger run.</p>")
    body.append("</div>")

    return R.write_report(path, f"Forum — {brief.get('name', 'Product')}",
                          "Synthetic Society · City 2 · focus groups + classroom", "".join(body))


# ── City ──────────────────────────────────────────────────────────────────────

@register
class ForumCity(City):
    id = "forum"
    name = "Forum"
    description = "focus groups that discuss + a shared classroom: what do groups say together?"
    n_label = "participants"
    default_n = 30
    min_n = 6

    def project_cost(self, n, answers, model) -> CostProjection:
        g = _n_groups(n)
        size = max(2, n // g)
        turns = g * (size * Q_PER_GROUP + N_PROBES * 2)
        p = CostProjection(model=model)
        p.add("group + question design", 1, 1500, 3000)
        p.add("persona generation", g, 1400, 5000)
        p.add("discussion turns", turns, 1800, 220)
        p.add("group analysis", g + 1, 4000, 2500)
        p.add("classroom reactions", g * size, 3500, 800)
        return p

    async def run(self, ctx: RunContext) -> str:
        client = get_client()
        m, model, brief, d = ctx.meter, ctx.model, ctx.brief, ctx.out_dir
        g = 2 if ctx.limit else _n_groups(ctx.n)
        size = 3 if ctx.limit else max(2, ctx.n // g)
        q_per = 4 if ctx.limit else Q_PER_GROUP

        print(f"\n  [1/5] Designing {g} focus groups + questions...")
        groups = await design_groups(client, model, brief, g, q_per, m)
        save_json(os.path.join(d, "groups.json"), groups)

        print(f"  [2/5] Generating {g}x{size} participants...")
        all_personas, transcripts = [], []
        for grp in groups:
            members = await gen_group_personas(client, model, brief, grp, size, m)
            all_personas.extend(members)
            grp["_members"] = members
        save_json(os.path.join(d, "personas.json"), all_personas)

        print("  [3/5] Running focus-group discussions...")
        for grp in groups:
            qs = grp.get("questions", [])[:q_per] or [f"What's your honest take on {brief.get('name')}?"]
            t = await run_group(client, model, brief, grp, grp.get("_members", []), qs, ctx.grounding, m)
            transcripts.append(t)
        save_json(os.path.join(d, "transcripts.json"), transcripts)

        print("  [4/5] Analyzing groups (novelty quarantine)...")
        analyses = []
        for t in transcripts:
            analyses.append(await analyze_group(client, model, brief, t, m))
        save_json(os.path.join(d, "analysis.json"), analyses)

        print("  [5/5] Classroom reactions...")
        classroom = await run_classroom(client, model, brief, all_personas, analyses, m)
        save_json(os.path.join(d, "classroom.json"), classroom)

        return build_report(os.path.join(d, "report.html"), brief, analyses, classroom)
