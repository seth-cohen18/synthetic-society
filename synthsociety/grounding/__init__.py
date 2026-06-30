"""Pluggable grounding sources.

Grounding = real-world text scraped from where the audience actually talks. It is
used two ways: (1) quotes injected into persona prompts for VOICE, and (2) a corpus
distilled by `synthesize.py` into audience PRIORS (real objections + segments)
merged into the brief — so personas mirror a real audience, not just sound like one.
Conclusions stay synthetic; scraped opinions are never findings (see synthesize.py).
It is OPT-IN and degrades gracefully: the pipeline runs fine with no grounding at all.

Every source returns a flat list of quote dicts:
    {"quote": str, "source": str, "topic": str}

Each source declares which env vars it needs; a source that isn't configured
reports itself unavailable rather than crashing. The user brings their own
credentials and accepts each provider's Terms of Service.
"""

import hashlib
import os
import random
from typing import Optional


class GroundingSource:
    name = "base"
    label = "Base"
    requires: list = []          # env var names this source needs
    needs_package: Optional[str] = None  # pip package needed, if any

    def missing_requirements(self) -> list:
        return [v for v in self.requires if not os.getenv(v)]

    def is_available(self) -> bool:
        return not self.missing_requirements()

    def fetch(self, topics: list, limit: int = 80) -> list:
        """Return up to `limit` quote dicts relevant to the given topic seeds."""
        raise NotImplementedError


# ── Registry ──────────────────────────────────────────────────────────────────
_SOURCES = {}


def register(cls):
    _SOURCES[cls.name] = cls
    return cls


def get_source(name: str) -> GroundingSource:
    if name not in _SOURCES:
        raise KeyError(f"Unknown grounding source: {name}. Known: {list(_SOURCES)}")
    return _SOURCES[name]()


def list_sources() -> list:
    return [cls() for cls in _SOURCES.values()]


# ── Quote sampling (used by persona builders) ─────────────────────────────────

def sample_quotes(pool: list, n: int = 5, persona: dict = None, seed_offset: int = 0) -> list:
    """Pick n quotes for a persona. If the persona carries 'interests'/'topics'
    that overlap a quote's topic, those are preferred; otherwise sample broadly.

    Selection is reproducible across runs: the RNG is seeded from a SHA-256 digest
    of the persona name + seed_offset, NOT Python's built-in hash() — hash() salts
    strings per process (PYTHONHASHSEED), which would reshuffle every persona's
    quotes on each run."""
    if not pool:
        return []
    affinity = set()
    if persona:
        for key in ("topics", "interests", "tags"):
            v = persona.get(key)
            if isinstance(v, list):
                affinity.update(str(x).lower() for x in v)
    relevant = [q for q in pool if str(q.get("topic", "")).lower() in affinity] if affinity else []
    chosen_pool = relevant if len(relevant) >= n else pool
    name = persona.get("name", "") if persona else ""
    digest = hashlib.sha256(f"{name}|{seed_offset}".encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    return rng.sample(chosen_pool, min(n, len(chosen_pool)))


# Import source modules so they self-register. Kept at the bottom to avoid
# circular imports; failures (e.g. an optional dep) must not break the package.
from synthsociety.grounding import quotes_file  # noqa: E402,F401
for _mod in ("reddit", "youtube", "youtube_transcripts", "web", "news", "apify"):
    try:
        __import__(f"synthsociety.grounding.{_mod}")
    except Exception:
        pass
