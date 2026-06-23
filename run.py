#!/usr/bin/env python3
"""Synthetic Society — interactive launcher.

    python run.py                 # full interactive run
    python run.py --limit         # cheap smoke test (small population)
    python run.py --city arena    # skip the city picker

Bring your own Anthropic key in .env. Nothing costs money until you confirm the
estimate (Gate 2). A single ~1-cent call happens earlier to read your product.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import webbrowser
from datetime import datetime

import synthsociety  # noqa: F401  (sets UTF-8 on Windows consoles)
from synthsociety import wizard
from synthsociety.budget import CostMeter, BudgetExceeded, DEFAULT_MODEL, report_stop
from synthsociety.client import get_client, load_json, save_json
from synthsociety.cities import get_city, list_cities
from synthsociety.cities.base import RunContext
from synthsociety.cost import suggest_budget_cap
from synthsociety.grounding import get_source, list_sources, GroundingSource
from synthsociety.understand import build_understanding, read_product_source, render_brief

ROOT = os.path.dirname(os.path.abspath(__file__))
RUNS_DIR = os.path.join(ROOT, "runs")

DISCLAIMER = (
    "  Reminder: this is a flight simulator, not the flight. Treat results as\n"
    "  DIRECTIONAL signal for finding blind spots — never as evidence of real\n"
    "  demand. Simulate first; then talk to real humans."
)


# ── Gate 1: understand the product ───────────────────────────────────────────

def open_in_editor(data: dict) -> dict:
    """Write the brief to a temp JSON file, open it in the OS default editor, wait
    for the user, then reload. Falls back to returning data unchanged on failure."""
    fd, path = tempfile.mkstemp(suffix="_brief.json", text=True)
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\n  Opening the brief for editing:\n    {path}")
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", path])
        else:
            subprocess.run(["xdg-open", path])
    except Exception:
        print("  (Could not auto-open — edit the file above in any editor.)")
    input("  Edit, SAVE, then press Enter here to reload... ")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  Could not parse your edits ({e}); keeping the previous version.")
        return data


def understand_gate(client, model: str) -> dict:
    wizard.section("Step 1 — Understand your product")
    print("  Point me at your product. I'll read it and draft a research brief.")
    print("  Accepts: a folder path, a single file, a URL, or just paste a description.")
    source = wizard.ask_text("Product source")
    raw = read_product_source(source)
    if not raw.strip():
        sys.exit("  Nothing readable found at that source. Try a different path/URL/text.")
    print("\n  Reading + drafting the brief (one ~1-cent call)...")
    brief = asyncio.run(build_understanding(client, model, raw))

    while True:
        print("\n" + "=" * 62)
        print(render_brief(brief))
        print("=" * 62)
        choice = wizard.ask_choice(
            "Use this brief?",
            [("use", "Use it — looks right"),
             ("edit", "Edit it by hand (opens the JSON)"),
             ("abort", "Abort")],
        )
        if choice == "use":
            return brief
        if choice == "edit":
            brief = open_in_editor(brief)
        else:
            sys.exit("  Aborted before any real spend.")


# ── Grounding ─────────────────────────────────────────────────────────────────

def grounding_step(brief: dict) -> list:
    wizard.section("Step 2 — Grounding (optional)")
    print("  Real quotes make personas talk like actual people. Optional — skip for")
    print("  a faster, zero-dependency run.")
    sources = list_sources()
    options = [("none", "No grounding (skip)")]
    for s in sources:
        status = "ready" if s.is_available() else f"needs {', '.join(s.requires) or 'a quotes file'}"
        if s.needs_package and not s.is_available():
            status += f" + pip install {s.needs_package}"
        options.append((s.name, f"{s.label}  [{status}]"))
    pick = wizard.ask_choice("Grounding source", options)
    if pick == "none":
        return []

    src: GroundingSource = get_source(pick)
    if pick == "quotes_file" and not os.getenv("GROUNDING_QUOTES_FILE"):
        path = wizard.ask_text("Path to your quotes JSON file")
        os.environ["GROUNDING_QUOTES_FILE"] = path
    if not src.is_available():
        print("  That source isn't configured — continuing with NO grounding.")
        return []

    topics = brief.get("grounding_topics", []) or [brief.get("category", "")]
    print(f"  Fetching quotes for: {', '.join(topics)} ...")
    try:
        pool = src.fetch(topics, limit=80)
    except Exception as e:
        print(f"  Grounding fetch failed ({e}) — continuing with NO grounding.")
        return []
    print(f"  Collected {len(pool)} quotes.")
    return pool


# ── Gate 2: cost + population size ────────────────────────────────────────────

def cost_gate(city, answers: dict, model: str, limit: bool) -> tuple:
    wizard.section(f"Step 4 — Population size & cost ({city.n_label})")
    n = city.min_n if limit else city.default_n
    if not limit:
        print(f"  Recommended: {city.default_n} {city.n_label}.")
        n = wizard.ask_int(f"How many {city.n_label}?", city.default_n, lo=city.min_n, hi=2000)
    proj = city.project_cost(n, answers, model)
    print("\n" + proj.format())
    cap = suggest_budget_cap(proj.total_usd)
    print(f"\n  Suggested hard budget cap: ${cap:.2f} (run stops cleanly if exceeded).")
    cap = float(wizard.ask_int("Set your hard budget cap (USD)", int(cap), lo=1, hi=10000))
    print()
    print(DISCLAIMER)
    print()
    if not wizard.ask_bool(f"Proceed? ~${proj.total_usd:.2f} est., ${cap:.2f} hard cap", default=True):
        sys.exit("  Cancelled — no spend.")
    return n, cap


def main():
    ap = argparse.ArgumentParser(description="Synthetic Society — synthetic research cities")
    ap.add_argument("--limit", action="store_true", help="Cheap smoke test (smallest population)")
    ap.add_argument("--city", help="Skip the city picker (census | forum | arena)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Model id (default {DEFAULT_MODEL})")
    args = ap.parse_args()

    wizard.banner("SYNTHETIC SOCIETY",
                  "Pressure-test your product against a simulated population.")
    client = get_client()  # exits with a clear message if ANTHROPIC_API_KEY is unset

    # Pick a city
    cities = list_cities()
    if not cities:
        sys.exit("  No cities registered. (Is the install complete?)")
    if args.city:
        city = get_city(args.city)
    else:
        wizard.section("Choose a research city")
        cid = wizard.ask_choice(
            "Which city?",
            [(c.id, f"{c.name} — {c.description}") for c in cities],
        )
        city = get_city(cid)

    brief = understand_gate(client, args.model)        # Gate 1
    grounding = grounding_step(brief)

    wizard.section(f"Step 3 — {city.name} setup")
    answers = {}
    for q in city.setup_questions():
        answers[q.key] = wizard.ask(q)

    n, cap = cost_gate(city, answers, args.model, args.limit)   # Gate 2

    # Assemble run context
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(RUNS_DIR, city.id, stamp)
    os.makedirs(out_dir, exist_ok=True)
    save_json(os.path.join(out_dir, "brief.json"), brief)
    if grounding:
        save_json(os.path.join(out_dir, "grounding.json"), grounding)
    meter = CostMeter(cap, os.path.join(out_dir, "_spend.json"), model=args.model)
    ctx = RunContext(brief=brief, grounding=grounding, n=n, model=args.model,
                     out_dir=out_dir, meter=meter, answers=answers, limit=args.limit)

    wizard.banner(f"Running {city.name}", f"{n} {city.n_label} · cap ${cap:.2f} · {out_dir}")
    try:
        report = asyncio.run(city.run(ctx))
    except BudgetExceeded:
        report_stop(city.name, meter, resume_hint=f"re-run and raise the cap (output in {out_dir})")
        sys.exit(2)

    print("\n" + "=" * 62)
    print(f"  DONE — spent ${meter.spent:.2f} over {meter.calls} calls")
    print(f"  Report: {report}")
    print("=" * 62)
    print("\n" + DISCLAIMER + "\n")
    try:
        webbrowser.open(f"file://{os.path.abspath(report)}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
