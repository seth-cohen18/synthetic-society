"""Understand the user's product from their own files / URL / pasted text.

This is the generalization keystone. In the original (single-product) pipeline the
pitch, the objection FAQ, and the target audience were hardcoded. Here they are
*derived* from whatever the user points us at, producing one structured "product
brief" that every city consumes:

    {
      "name", "one_liner", "category",
      "pitch",                # factual description shown to every persona
      "audience",             # prose description of who this is for
      "audience_axes": [ {"key","label","values":[...]} ],   # segmentation dimensions
      "known_objections": [ {"objection","answer"} ],        # the FAQ personas must push past
      "grounding_topics": [ ... ]                            # suggested search seeds
    }

The user reviews and edits this before any personas are built (Gate 1).
"""

import glob
import os
import re
import urllib.request

from synthsociety.client import extract_json

MAX_SOURCE_CHARS = 24_000   # cap raw product text fed to the model

# File types worth reading from a product folder, in priority order.
_READABLE = (".md", ".markdown", ".txt", ".rst", ".html", ".json", ".yaml", ".yml")
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", "runs"}


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = re.sub(r"\s+", " ", html)
    return html.strip()


def read_product_source(source: str) -> str:
    """Turn a folder / file / URL / raw string into product text (capped).

    - http(s) URL  -> fetched and de-tagged (best effort, no extra deps)
    - a directory  -> readable files concatenated (README first)
    - a file path  -> its text
    - anything else -> treated as the product description itself (pasted text)
    """
    source = source.strip()

    if source.lower().startswith(("http://", "https://")):
        try:
            req = urllib.request.Request(source, headers={"User-Agent": "synthetic-society/0.1"})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode("utf-8", errors="replace")
        except Exception as e:
            print(f"  Could not fetch that URL ({e}). Paste the text or point at a file instead.")
            return ""
        text = _strip_html(raw)[:MAX_SOURCE_CHARS]
        # A plain GET can't execute JavaScript, so a JS-rendered SPA comes back as an
        # almost-empty shell. Warn rather than silently build a brief from nothing.
        if len(text) < 400:
            print("  WARNING: extracted very little text from this URL — it is likely a "
                  "JavaScript-rendered page. Paste the product description or point at a "
                  "file/folder for a reliable brief.")
        return text

    if os.path.isdir(source):
        files = []
        for path in glob.glob(os.path.join(source, "**", "*"), recursive=True):
            if not os.path.isfile(path):
                continue
            if any(part in _SKIP_DIRS for part in path.split(os.sep)):
                continue
            if os.path.splitext(path)[1].lower() in _READABLE:
                files.append(path)
        # README-ish files first, then the rest
        files.sort(key=lambda p: (0 if "readme" in os.path.basename(p).lower() else 1, p))
        chunks, total = [], 0
        for path in files:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read().strip()
            except Exception:
                continue
            if not text:
                continue
            rel = os.path.relpath(path, source)
            block = f"\n\n===== {rel} =====\n{text}"
            chunks.append(block)
            total += len(block)
            if total >= MAX_SOURCE_CHARS:
                break
        return "".join(chunks)[:MAX_SOURCE_CHARS]

    if os.path.isfile(source):
        with open(source, "r", encoding="utf-8", errors="replace") as f:
            return f.read()[:MAX_SOURCE_CHARS]

    # Not a path or URL — treat the string itself as the product description.
    return source[:MAX_SOURCE_CHARS]


UNDERSTAND_SYSTEM = (
    "You are a product research analyst. You are given raw material about a product "
    "(docs, a landing page, or a description). Distill it into a precise, factual "
    "research brief. Be accurate and neutral — do NOT inflate claims or invent "
    "features. If something isn't in the material, don't assert it.\n\n"
    "Return ONLY a JSON object with these keys:\n"
    '  "name": short product name.\n'
    '  "one_liner": one sentence on what it is.\n'
    '  "category": e.g. "consumer fitness app", "B2B analytics SaaS".\n'
    '  "pitch": 1-2 short paragraphs of the FACTUAL pitch shown to every persona — '
    "what it does, how it works, pricing/plans if known. Plain, non-marketing tone.\n"
    '  "audience": a prose description of who this product is for.\n'
    '  "audience_axes": 2-4 segmentation dimensions that matter for THIS product, '
    'each {"key": snake_case, "label": display, "values": [3-5 distinct values]}. '
    'These are how results get broken down (e.g. experience level, willingness to pay).\n'
    '  "known_objections": 4-8 of the most likely objections a real skeptic would '
    'raise, each {"objection": the concern, "answer": the honest, factual rebuttal '
    "from the material}. These are the FAQ personas must push PAST to surface novel "
    "concerns. If the material lacks an answer, give the best honest answer and keep it short.\n"
    '  "grounding_topics": 4-8 short search seeds (topics, subreddits, or phrases) where '
    "real people discuss this product's problem space — used to optionally scrape real "
    "text that calibrates persona voice, objections, and segments.\n"
)


async def build_understanding(client, model: str, raw_text: str, on_usage=None) -> dict:
    """One model call: raw product text -> structured brief dict."""
    from synthsociety.client import call_claude
    user = (
        "Here is the raw material about the product. Produce the research brief JSON.\n\n"
        f"<product_material>\n{raw_text}\n</product_material>"
    )
    resp = await call_claude(client, model, UNDERSTAND_SYSTEM, user,
                             max_tokens=2500, on_usage=on_usage)
    try:
        brief = extract_json(resp)
        if not isinstance(brief, dict):
            raise ValueError("model did not return a JSON object")
    except Exception as e:
        snippet = (resp or "").strip()[:200].replace("\n", " ")
        raise RuntimeError(
            f"Could not parse a research brief from the model response ({e}). "
            f"Response began: {snippet!r}. Re-run, or give a clearer product source."
        ) from e
    # Light normalization so downstream cities can rely on the shape.
    brief.setdefault("audience_axes", [])
    brief.setdefault("known_objections", [])
    brief.setdefault("grounding_topics", [])
    return brief


def render_brief(brief: dict) -> str:
    """Human-readable rendering of the brief for the Gate 1 review screen."""
    out = [
        f"  Product:    {brief.get('name', '?')}",
        f"  One-liner:  {brief.get('one_liner', '?')}",
        f"  Category:   {brief.get('category', '?')}",
        "",
        "  Pitch (shown to every persona):",
    ]
    for line in brief.get("pitch", "").splitlines():
        out.append(f"    {line}")
    out += ["", f"  Audience:   {brief.get('audience', '?')}", ""]
    axes = brief.get("audience_axes", [])
    if axes:
        out.append("  Segmentation axes:")
        for a in axes:
            out.append(f"    - {a.get('label', a.get('key'))}: {', '.join(a.get('values', []))}")
        out.append("")
    objs = brief.get("known_objections", [])
    if objs:
        out.append(f"  Known objections FAQ ({len(objs)}):")
        for o in objs:
            out.append(f"    - {o.get('objection', '')}")
        out.append("")
    topics = brief.get("grounding_topics", [])
    if topics:
        out.append(f"  Grounding seeds: {', '.join(topics)}")
    return "\n".join(out)
