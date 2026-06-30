# Synthetic Society

**Pressure-test your product against a simulated population of AI personas — before you spend weeks on real interviews.**

You bring an Anthropic API key and point it at your product. It reads what your
product is, builds a diverse population of personas, and runs one of three
research "cities" against them — a survey, a set of focus groups, or an A/B
feature-preference arena. You get a structured HTML report and, most importantly,
a list of **objections you hadn't thought of** to take into real conversations.

> ### ⚠️ Read this first — what this is and isn't
> This is a **flight simulator, not the flight.** Synthetic personas are useful
> for finding blind spots, surfacing objections, and pressure-testing direction
> *cheaply and fast*. They are **not** evidence of real demand. Treat every output
> as **directional / ordinal only** ("this concern came up a lot", "beginners
> reacted worse than advanced users") — never as a number you can take to an
> investor. **The correct workflow is: simulate first, then talk to real humans
> second.** This tool is designed to make those real conversations sharper, not
> to replace them.

---

## The three cities

Each "city" is a different research method. You pick one per run.

| City | Method | Answers the question |
|---|---|---|
| **Census** | Broad two-round survey across a wide persona pool | *Who are my users, what's their real pain, and would they want this?* |
| **Forum** | Moderated focus groups that discuss and react to each other, then a shared "classroom" reaction | *What do groups say when they talk it through together? What problems hit a majority vs. a niche?* |
| **Arena** | A/B feature-preference testing — the whole population judges competing ideas | *Given the product, which direction should we build next?* |

All three share the same backbone: an **honesty mandate** (personas are told a
critical opinion is more valuable than a flattering one), a **novelty quarantine**
(objections you didn't already have an answer for are flagged as your blind
spots), and a **hard budget cap**.

---

## Quickstart

```bash
git clone https://github.com/<you>/synthetic-society.git
cd synthetic-society
pip install -r requirements.txt

cp .env.example .env          # then paste your Anthropic API key into .env

python run.py                 # interactive — it asks you everything from here
```

That's it. `run.py` is an interactive launcher: it asks which city you want, reads
your product, derives the audience, shows you a **cost estimate**, lets you choose
how many personas to spend on, asks for your confirmation, then runs.

---

## How a run works

```
  python run.py
        │
        ▼
  1. Pick a city            ── Census / Forum / Arena
        │
        ▼
  2. Understand your product ── point it at a folder of docs, a file, or a URL.
        │                       It distills a pitch + an objection FAQ + your
        │                       target audience.   ── you review & edit  [GATE 1]
        ▼
  3. (Optional) Grounding    ── scrape real text (Reddit / YouTube / web / Apify)
        │                       and distill it into audience priors — real voice,
        │                       objections, and segments. Off by default; BYO keys.
        ▼
  4. Cost estimate           ── recommended # of personas + the $ it will cost
        │                       for THIS city, and you can dial it down to spend
        │                       less.                ── you confirm        [GATE 2]
        ▼
  5. Run                      ── personas generated → city method runs → report
        │
        ▼
  6. Report                   ── runs/<city>/<timestamp>/report.html
                                 + the ⚑ "novel objections" list to take to humans
```

**Two confirmation gates** are deliberate: you approve *what the tool understood
about your product* before personas are built, and you approve *the spend* before
any paid run. Nothing costs money until Gate 2.

---

## Cost & the budget cap

You pay Anthropic directly for your own usage. Every run:

- **Estimates** the cost up front, per city, based on the number of personas.
- **Recommends** a sensible default population size, but lets you choose fewer to
  spend less (with the estimate updating live).
- Enforces a **hard budget cap** you set. If a run would exceed it, it stops
  cleanly, saves partial output, and tells you how to resume. You cannot get a
  surprise bill.

Rough ballpark on Claude Sonnet: a full run is typically a few dollars. A
`--limit` smoke test is cents.

---

## Grounding & calibration (optional)

By default, personas are generated from your product understanding alone. You can
*optionally* scrape real-world text so the synthetic personas mirror a **real
audience** — their voice, their recurring objections, and the segments that
actually show up:

| Source | What it pulls | What you need |
|---|---|---|
| **Quotes file** | Quotes you provide as JSON | nothing (always works) |
| **Reddit** | Posts **and top comments** from relevant subreddits (upvote-scored) | your Reddit API app creds · `pip install praw` |
| **YouTube comments** | Top comments on relevant videos | a YouTube Data API key |
| **YouTube transcripts** | Spoken captions from relevant videos (richest *voice*) | `pip install yt-dlp` (no key) |
| **Web articles** | Full article text on the topic (not just headlines) | `pip install trafilatura` (no key) |
| **News** | Recent headlines on the topic (low-signal fallback) | — |
| **Apify** | Heavy scraping — web, Reddit, X/Twitter, forums, any site | your own `APIFY_TOKEN` (REST API, no SDK) |

### Calibration — priors in, findings out

Grounding does **not** turn this into a web scraper that reports real data. The
scraped corpus is distilled (one ~1-cent call) into **calibration priors** merged
into the brief *before* personas are built:

- real recurring **objections** → folded into the FAQ as hurdles personas must push **past**,
- observed **segments** → widen the simulated population,
- real **vocabulary / voice** → injected into persona prompts (voice only — "don't adopt their opinions").

The personas' answers stay **100% synthetic**. Scraped opinions are never counted,
ranked, or quoted as results — the report shows them only in a separate, explicitly
labeled **"Calibration inputs — real, NOT findings"** box. That bright line is what
keeps "flight simulator, not the flight" true even with real data flowing in.

Grounding is **opt-in and pluggable**. Each source is the user's responsibility:
you supply any credentials and accept that provider's Terms of Service. The
pipeline degrades gracefully — if a source is off, missing its package, or fails
(e.g. a bad key), it says so out loud and the run still works.

---

## For contributors — architecture

```
synthetic-society/
  run.py                  # interactive launcher (the only entry point users touch)
  synthsociety/
    client.py             # Anthropic client (BYO key), async batch caller, JSON helpers
    budget.py             # cost meter + hard cap (cache-aware), persisted across steps
    cost.py               # per-city cost models → up-front $ estimate
    understand.py         # reads product files/URL → pitch + objection FAQ + audience
    wizard.py             # the interactive question flow
    grounding/            # pluggable scraping sources + corpus->priors distillation
                          #   quotes_file · reddit · youtube · youtube_transcripts
                          #   web · news · apify · synthesize (calibration firewall)
    cities/
      base.py             # City interface: setup_questions / estimate / run
      census/             # City 1
      forum/              # City 2
      arena/              # City 3
    report/               # shared HTML report helpers
  examples/               # example product inputs you can run against
  runs/                   # ← all generated output (git-ignored)
```

**Adding a city** = drop a new package under `cities/` implementing the `City`
interface (id, name, `setup_questions()`, `estimate_cost()`, `run()`) and register
it. The launcher discovers it automatically; nothing else changes.

---

## License

MIT — see [LICENSE](LICENSE). Built on the methodology behind
[Stream AI](https://streamaiworkout.com)'s internal research suite.
