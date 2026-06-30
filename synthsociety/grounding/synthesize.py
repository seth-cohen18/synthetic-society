"""Distill a scraped real-world corpus into AUDIENCE PRIORS (never findings).

This module is the firewall that keeps grounding honest. Scraped quotes are
calibration *inputs*: we extract the recurring real objections, the sub-segments
that actually show up, and the real vocabulary, then merge them into the product
brief so the synthetic personas reason from a realistic starting point. The
personas' answers stay 100% synthetic; nothing here is ever reported as a result.

    corpus -> synthesize_audience()    -> priors dict
    priors -> merge_priors_into_brief() -> brief enriched (in place)

THE BRIGHT LINE (do not cross): scraped opinions become HURDLES personas must push
PAST (folded into known_objections), never tallies or quotes in the report's
findings. See README "Calibration — priors in, findings out".
"""

from synthsociety.client import extract_json

MAX_CORPUS_CHARS = 18_000

SYNTH_SYSTEM = (
    "You are an audience research analyst. You are given (1) a product brief and "
    "(2) a corpus of REAL quotes scraped from public discussion of this product's "
    "problem space. Distill the corpus into calibration priors for building a "
    "realistic synthetic audience. Be conservative and evidence-bound: include only "
    "what the quotes actually support; do NOT invent. These priors PRIME synthetic "
    "personas — they are NOT survey findings and must never be reported as results.\n\n"
    "Return ONLY a JSON object:\n"
    '  "audience_note": 1-2 sentences on who actually shows up in this discussion '
    "(and who is likely missing or over-represented — online voices skew vocal, "
    "tech-forward, and complaint-heavy).\n"
    '  "real_objections": 3-8 of the most RECURRING real concerns, each '
    '{"objection": short concern, "answer": honest factual rebuttal, '
    '"frequency": "high"|"medium"|"low"}. These become hurdles personas must push past.\n'
    '  "segment_hints": an object mapping an audience-axis key (from the brief) to '
    "2-5 observed values — only for axes the corpus actually speaks to.\n"
    '  "vocabulary": 6-15 real words / phrases / jargon these people use.\n'
)


def _corpus_text(corpus: list, brief: dict) -> str:
    axes = ", ".join(a.get("key", "") for a in brief.get("audience_axes", []))
    lines = [f"PRODUCT: {brief.get('name', '')} — {brief.get('one_liner', '')}",
             f"AUDIENCE AXIS KEYS: {axes}", "", "REAL QUOTES:"]
    total = sum(len(x) for x in lines)
    for q in corpus:
        line = f'- [{q.get("source", "")}] {q.get("quote", "")}'
        if total + len(line) > MAX_CORPUS_CHARS:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


async def synthesize_audience(client, model: str, corpus: list, brief: dict, on_usage=None) -> dict:
    """One model call: real corpus -> calibration priors dict. Never raises on bad
    model output (grounding is best-effort and must not break a run)."""
    from synthsociety.client import call_claude
    if not corpus:
        return {}
    user = ("Distill these real quotes into calibration priors for the audience.\n\n"
            f"<corpus>\n{_corpus_text(corpus, brief)}\n</corpus>")
    resp = await call_claude(client, model, SYNTH_SYSTEM, user, max_tokens=2000, on_usage=on_usage)
    try:
        priors = extract_json(resp)
        if not isinstance(priors, dict):
            return {}
    except Exception:
        return {}
    priors.setdefault("real_objections", [])
    priors.setdefault("segment_hints", {})
    priors.setdefault("vocabulary", [])
    priors.setdefault("audience_note", "")
    return priors


def _norm(s) -> str:
    return " ".join(str(s).lower().split())


def merge_priors_into_brief(brief: dict, priors: dict) -> dict:
    """Fold priors into the brief IN PLACE, deduped, with provenance kept for the
    report's 'calibration inputs, not findings' section.

    - real_objections -> appended to known_objections (tagged source='corpus'),
      i.e. hurdles personas must push past — NOT findings.
    - segment_hints   -> unioned into the matching audience_axes' values.
    - a summary is stored under brief['_calibration'] for the report.
    Returns the same brief object.
    """
    if not priors:
        return brief
    known = brief.setdefault("known_objections", [])
    seen = {_norm(o.get("objection", "")) for o in known if isinstance(o, dict)}
    added = []
    for o in priors.get("real_objections", []):
        if not isinstance(o, dict):
            continue
        key = _norm(o.get("objection", ""))
        if key and key not in seen:
            seen.add(key)
            known.append({"objection": o.get("objection", ""),
                          "answer": o.get("answer", ""), "source": "corpus"})
            added.append(o)

    axes = brief.get("audience_axes", [])
    by_key = {a.get("key"): a for a in axes if isinstance(a, dict)}
    folded = {}
    for key, vals in (priors.get("segment_hints") or {}).items():
        if not isinstance(vals, list):
            continue
        ax = by_key.get(key)
        if ax is None:
            continue
        existing = {_norm(v) for v in ax.get("values", [])}
        new_vals = [v for v in vals if _norm(v) not in existing]
        if new_vals:
            ax.setdefault("values", []).extend(new_vals)
            folded[key] = new_vals

    brief["_calibration"] = {
        "audience_note": priors.get("audience_note", ""),
        "objections_added": added,
        "segments_added": folded,
        "vocabulary": priors.get("vocabulary", []),
    }
    return brief


def corpus_echo(text: str, corpus: list, threshold: float = 0.6) -> bool:
    """True if `text` reads as a near-paraphrase of something already in the corpus.

    Used to DEMOTE a supposedly-'novel' objection that merely parrots a scraped
    quote — so the corpus can't silently author the blind-spot list. Cheap bag-of-
    words overlap; intentionally conservative (short strings never match)."""
    t = set(_norm(text).split())
    if len(t) < 4:
        return False
    for q in corpus:
        c = set(_norm(q.get("quote", "")).split())
        if not c:
            continue
        if len(t & c) / max(1, len(t)) >= threshold:
            return True
    return False
