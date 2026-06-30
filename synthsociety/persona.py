"""Generic persona generation + system prompts (shared by all cities).

Generalized from the original fitness-specific helpers: personas carry arbitrary
segment tags derived from the product brief's `audience_axes` rather than a
hardcoded fitness_relationship. The honesty mandate (a critical opinion is worth
more than a flattering one) is preserved — it's what keeps the signal real.
"""

import json
from typing import List

from synthsociety.grounding import sample_quotes

HONESTY_MANDATE = (
    "CRITICAL: Your honest, critical, or negative opinion is far more valuable than "
    "a positive one. Answer as the specific person you are — from your real life, "
    "budget, and circumstances. If something doesn't apply to you, say so. If you're "
    "skeptical, indifferent, or would never use something, say that plainly. Do NOT "
    "give answers you think the researcher wants to hear. Do not break character."
)


def _diversity_block(brief: dict) -> str:
    axes = brief.get("audience_axes", [])
    axis_lines = ""
    if axes:
        axis_lines = "\nVary these product-relevant dimensions across the personas:\n" + "\n".join(
            f"- {a.get('label', a.get('key'))}: spread across {', '.join(a.get('values', []))}"
            for a in axes
        )
    return (
        "Ensure genuine diversity across ALL of these dimensions:\n"
        "- Age: a wide spread appropriate to the audience, not clustered\n"
        "- Gender: roughly balanced, include some non-binary\n"
        "- Location: a mix of urban and rural, and some international\n"
        "- Income: genuinely low, middle, upper-middle, and high\n"
        "- Occupation: trades, service, students, retirees, healthcare, tech, arts, unemployed\n"
        "- Personality: a heavy mix of skeptics, cynics, privacy-focused, anti-tech, "
        "indifferent, impulsive, optimists, data-driven, budget-conscious — do NOT skew "
        "positive or tech-friendly\n"
        "- Tech comfort: the full range 1-10, not skewed high\n"
        f"{axis_lines}"
    )


def persona_generation_prompt(brief: dict, count: int, start: int, end: int) -> tuple:
    """(system, user) for one persona-generation batch, tailored to the brief."""
    axes = brief.get("audience_axes", [])
    segment_fields = ""
    if axes:
        segment_fields = (
            '  "segment": { '
            + ", ".join(f'"{a["key"]}": one of [{", ".join(a.get("values", []))}]' for a in axes)
            + " },\n"
        )
    schema = (
        f"Return a JSON array of exactly {count} personas. Each must have:\n"
        "{\n"
        f'  "id": "p_{start:03d}" .. "p_{end:03d}",\n'
        '  "name": "First Last",\n'
        '  "age": integer,\n'
        '  "gender": "male" | "female" | "non-binary",\n'
        '  "location": "City, Country",\n'
        '  "occupation": "specific job title",\n'
        '  "income_level": "low" | "medium" | "high",\n'
        '  "tech_comfort": integer 1-10,\n'
        '  "personality_trait": "one specific trait",\n'
        '  "relationship": "one short phrase on their relationship to '
        f'{brief.get("category", "this product space")}",\n'
        f"{segment_fields}"
        '  "notes": "any specific, believable detail (a constraint, a past experience, a bias)"\n'
        "}\n"
        "Return ONLY the JSON array, no other text."
    )
    system = (
        "You are a synthetic persona generator. Create realistic, diverse, fictional "
        "people for product research. Make them feel like real individuals with specific, "
        "believable details. Avoid stereotypes — give unexpected combinations. IMPORTANT: "
        "not everyone should be enthusiastic, on-board, or an ideal customer. Include plenty "
        "of skeptics, people who distrust new tools, people indifferent to the problem, and "
        "people who would never be a customer. The goal is honest research, not validation."
    )
    user = (
        f"Generate {count} diverse personas for research on this product:\n\n"
        f"Product: {brief.get('name', '')} — {brief.get('one_liner', '')}\n"
        f"Category: {brief.get('category', '')}\n"
        f"Target audience: {brief.get('audience', '')}\n\n"
        f"Diversity requirements:\n{_diversity_block(brief)}\n\n"
        f"Schema and ID range:\n{schema}"
    )
    return system, user


def build_persona_system_prompt(persona: dict, brief: dict, grounding_quotes: List[dict] = None,
                                seed_offset: int = 0) -> str:
    seg = persona.get("segment", {})
    seg_text = ""
    if isinstance(seg, dict) and seg:
        seg_text = " ".join(f"Your {k.replace('_', ' ')} is {v}." for k, v in seg.items()) + " "
    base = (
        f"You are {persona.get('name', 'someone')}, {persona.get('age', '?')} years old, "
        f"working as a {persona.get('occupation', 'person')} from {persona.get('location', '')}. "
        f"{persona.get('relationship', '')}. "
        f"You are {persona.get('personality_trait', 'an ordinary person')}. "
        f"Your tech comfort level is {persona.get('tech_comfort', 5)}/10. "
        f"{seg_text}"
        + (f"{persona['notes']}. " if persona.get("notes") else "")
        + "Answer honestly from your perspective. Be specific — reference your real job, "
        "life, and concerns where relevant. Keep each answer to 2-4 sentences. Do not number "
        "your answers or add labels. "
        + HONESTY_MANDATE
    )
    if grounding_quotes:
        selected = sample_quotes(grounding_quotes, n=5, persona=persona, seed_offset=seed_offset)
        if selected:
            quote_lines = "\n".join(f'- "{q["quote"]}" [{q.get("source", "")}]' for q in selected)
            base += (
                "\n\nHere is how real people talk about this space. Use it for VOICE and "
                "vocabulary ONLY — let it inform how you naturally express yourself. Do NOT "
                "adopt these opinions as your own or repeat their conclusions; reason as "
                "yourself, from your own life and circumstances:\n" + quote_lines
            )
    return base


def persona_summary(p: dict) -> str:
    seg = p.get("segment", {})
    seg_text = (" " + "; ".join(f"{k}={v}" for k, v in seg.items()) + ".") if isinstance(seg, dict) and seg else ""
    return (
        f"{p.get('name', '?')}, {p.get('age', '?')}, {p.get('occupation', '?')} from "
        f"{p.get('location', '?')}. {p.get('relationship', '')}. "
        f"Personality: {p.get('personality_trait', '?')}. Tech comfort: {p.get('tech_comfort', '?')}/10."
        f"{seg_text}"
    )


def objections_faq(brief: dict) -> str:
    """Render the brief's known objections as the FAQ shown in product-reaction rounds.
    Personas must push PAST these to surface novel concerns (the blind-spot output)."""
    objs = brief.get("known_objections", [])
    if not objs:
        return ""
    lines = [
        "COMMON CONCERNS, ALREADY ADDRESSED",
        "",
        "These are real answers, not marketing copy. Do NOT simply repeat a concern that is "
        "already answered below — engage with the actual answer (push on it, challenge it, or "
        "accept it), then raise something NEW. The most valuable feedback is something the "
        "team has not thought of yet.",
        "",
    ]
    for o in objs:
        lines.append(f"Q: {o.get('objection', '')}")
        lines.append(f"A: {o.get('answer', '')}")
        lines.append("")
    return "\n".join(lines)
