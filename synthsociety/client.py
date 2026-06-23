"""Anthropic client + async batch caller + JSON helpers.

Bring your own key: set ANTHROPIC_API_KEY in your .env (or environment). Nothing
here is product-specific — persona/domain logic lives in the cities.
"""

import asyncio
import json
import os
import sys
import time
from typing import Any, Callable, Optional, Union

import anthropic

try:
    from dotenv import load_dotenv as _load_dotenv
    # Load a .env from the repo root (two levels up: synthsociety/ -> repo/)
    _load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except ImportError:
    pass


def get_client() -> anthropic.AsyncAnthropic:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        sys.exit(
            "\n  ERROR: ANTHROPIC_API_KEY is not set.\n"
            "  Copy .env.example to .env and paste your key from "
            "https://console.anthropic.com/\n"
        )
    return anthropic.AsyncAnthropic(api_key=key)


# ── JSON I/O ────────────────────────────────────────────────────────────────

def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  Saved -> {path}")


def extract_json(text: str) -> Any:
    """Strip markdown code fences and parse JSON from a model response.

    Uses raw_decode so trailing prose after valid JSON is ignored (the model
    sometimes appends a sentence after the object)."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        end = -1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[1:end])
    obj, _ = json.JSONDecoder().raw_decode(text.strip())
    return obj


# ── Prompt-caching content blocks ─────────────────────────────────────────────
# Mark a large, repeated system block for prompt caching (~0.1x reads after the
# first write within the 5-min TTL). Use for blocks reused across many calls — a
# shared product brief, a persona's own system prompt across its turns. No-op
# below the model's cache minimum.

def cache_block(text: str) -> dict:
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


# ── The call ──────────────────────────────────────────────────────────────────

async def call_claude(
    client: anthropic.AsyncAnthropic,
    model: str,
    system: Union[str, list],
    user: str,
    max_tokens: int = 1024,
    retries: int = 4,
    on_usage: Optional[Callable] = None,
) -> str:
    """Call Claude, return the text. `system` may be a string or a list of content
    blocks (use cache_block/text_block for caching). on_usage(resp.usage) is invoked
    after a successful call — the budget meter uses it to track real spend."""
    for attempt in range(retries):
        try:
            resp = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            if on_usage is not None:
                on_usage(resp.usage)
            return resp.content[0].text
        except anthropic.RateLimitError:
            wait = 2 ** attempt * 5
            print(f"\n  Rate limited — waiting {wait}s (attempt {attempt + 1}/{retries})")
            await asyncio.sleep(wait)
        except anthropic.APIStatusError:
            if attempt == retries - 1:
                raise
            await asyncio.sleep(2 ** attempt)
    return ""


async def run_batch(
    tasks: list,
    worker_fn: Callable,
    max_concurrent: int = 20,
    label: str = "items",
) -> list:
    """Run worker_fn(task) for every task with bounded concurrency. Results come
    back in input order. Prints a live progress/ETA line."""
    semaphore = asyncio.Semaphore(max_concurrent)
    results: list = [None] * len(tasks)
    completed = 0
    total = len(tasks)
    start = time.time()

    async def run_one(i: int, task):
        nonlocal completed
        async with semaphore:
            results[i] = await worker_fn(task)
            completed += 1
            elapsed = time.time() - start
            rate = completed / elapsed if elapsed > 0 else 0
            eta = (total - completed) / rate if rate > 0 else 0
            print(f"\r  {label}: {completed}/{total}  ({rate:.1f}/s  ETA {eta:.0f}s)    ",
                  end="", flush=True)

    await asyncio.gather(*[run_one(i, t) for i, t in enumerate(tasks)])
    print()
    return results
