"""Bring-your-own-quotes grounding source — the default that always works.

Point it at a JSON file of quotes you collected however you like. Accepted shapes:

    [ {"quote": "...", "source": "r/fitness", "topic": "form"}, ... ]
    [ "a plain string quote", "another", ... ]              # source/topic optional
    { "quotes": [ ... ] }                                    # wrapped

Set GROUNDING_QUOTES_FILE to the path, or the wizard will ask for it.
"""

import json
import os

from synthsociety.grounding import GroundingSource, register


@register
class QuotesFileSource(GroundingSource):
    name = "quotes_file"
    label = "Quotes file (JSON you provide)"
    requires = []  # no creds — the path is supplied at runtime

    def fetch(self, topics: list, limit: int = 80) -> list:
        path = os.getenv("GROUNDING_QUOTES_FILE", "").strip()
        if not path or not os.path.isfile(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return []
        if isinstance(data, dict):
            data = data.get("quotes", [])
        out = []
        for item in data:
            if isinstance(item, str):
                out.append({"quote": item, "source": "user", "topic": ""})
            elif isinstance(item, dict) and item.get("quote"):
                out.append({
                    "quote": item["quote"],
                    "source": item.get("source", item.get("subreddit", "user")),
                    "topic": item.get("topic", ""),
                })
            if len(out) >= limit:
                break
        return out
