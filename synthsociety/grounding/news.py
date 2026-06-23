"""News grounding (optional) — recent headlines/snippets on the topic space.

Uses Google News' public RSS, fetched over urllib (no key, no extra package).
Best-effort and lower-signal than first-person sources — headlines calibrate
topical vocabulary, not personal voice. You are responsible for complying with
the source's terms.
"""

import re
import urllib.parse
import urllib.request

from synthsociety.grounding import GroundingSource, register

_RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def _items(xml: str) -> list:
    out = []
    for m in re.finditer(r"(?s)<item>(.*?)</item>", xml):
        block = m.group(1)
        t = re.search(r"<title>(.*?)</title>", block)
        d = re.search(r"(?s)<description>(.*?)</description>", block)
        title = re.sub(r"<[^>]+>", " ", t.group(1)) if t else ""
        desc = re.sub(r"<[^>]+>", " ", d.group(1)) if d else ""
        text = re.sub(r"\s+", " ", f"{title}. {desc}").strip()
        if text:
            out.append(text)
    return out


@register
class NewsSource(GroundingSource):
    name = "news"
    label = "News headlines (Google News RSS, no key)"
    requires = []

    def fetch(self, topics: list, limit: int = 80) -> list:
        out, per_topic = [], max(3, limit // max(1, len(topics)))
        for topic in topics:
            try:
                url = _RSS.format(q=urllib.parse.quote(topic))
                req = urllib.request.Request(url, headers={"User-Agent": "synthetic-society/0.1"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    xml = r.read().decode("utf-8", errors="replace")
            except Exception:
                continue
            for text in _items(xml)[:per_topic]:
                out.append({"quote": text[:600], "source": "news", "topic": topic})
                if len(out) >= limit:
                    return out
        return out
