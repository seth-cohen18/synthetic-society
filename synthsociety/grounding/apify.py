"""Apify grounding (optional) — heavy-duty real scraping via your own Apify token.

Set APIFY_TOKEN (https://console.apify.com/account/integrations). By default this
runs Apify's general "RAG Web Browser" actor to search the web and return clean
page text. Override APIFY_ACTOR to point at ANY Apify scraper — e.g. a Reddit,
X/Twitter, or forum scraper — for deeper real-world signal. Talks to the Apify
REST API directly over urllib (no SDK, no MCP). You are responsible for your Apify
usage and each target site's Terms of Service.

Env:
  APIFY_TOKEN       (required)  your Apify API token
  APIFY_ACTOR       (optional)  actor id, default "apify/rag-web-browser"
  APIFY_INPUT_KEY   (optional)  the actor input field that takes the search term,
                                default "query" (some actors use "searchTerms"/"keyword")
"""

import json
import os
import urllib.parse
import urllib.request

from synthsociety.grounding import GroundingSource, register

_DEFAULT_ACTOR = "apify/rag-web-browser"
_TEXT_KEYS = ("text", "markdown", "content", "body", "comment", "commentText",
              "title", "fullText", "post", "message")


def _run_actor(actor: str, token: str, payload: dict, timeout: int = 180) -> list:
    url = (f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
           f"?token={urllib.parse.quote(token)}&timeout={timeout}")
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "synthetic-society/0.1"})
    with urllib.request.urlopen(req, timeout=timeout + 15) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def _extract_text(item) -> str:
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return ""
    for k in _TEXT_KEYS:
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v
    md = item.get("metadata")
    if isinstance(md, dict):
        for k in ("description", "title"):
            v = md.get(k)
            if isinstance(v, str) and v.strip():
                return v
    return ""


@register
class ApifySource(GroundingSource):
    name = "apify"
    label = "Apify scraper (your APIFY_TOKEN — heavy web/Reddit/X scraping)"
    requires = ["APIFY_TOKEN"]

    def fetch(self, topics: list, limit: int = 80) -> list:
        token = os.getenv("APIFY_TOKEN")
        if not token:
            return []
        actor = os.getenv("APIFY_ACTOR", _DEFAULT_ACTOR).replace("/", "~")
        input_key = os.getenv("APIFY_INPUT_KEY", "query")
        per_topic = max(1, limit // max(1, len(topics)))
        out, warned = [], False
        for topic in topics:
            # Common search-actor input shape; users with a different actor set
            # APIFY_INPUT_KEY to match their actor's schema.
            payload = {input_key: topic, "maxResults": per_topic, "maxItems": per_topic}
            try:
                items = _run_actor(actor, token, payload)
            except Exception as e:
                if not warned:
                    print(f"  (apify) actor '{actor}' failed: {e}. "
                          "Check APIFY_TOKEN / APIFY_ACTOR / APIFY_INPUT_KEY.")
                    warned = True
                continue
            for it in (items or []):
                text = _extract_text(it).strip()
                if len(text) >= 80:
                    out.append({"quote": text[:1600], "source": "apify", "topic": topic})
                if len(out) >= limit:
                    return out
        return out
