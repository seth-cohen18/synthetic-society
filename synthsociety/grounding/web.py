"""Web-article grounding (optional) — full article text, not just headlines.

Discovers article URLs via Google News RSS (no key), then extracts clean body
text with `trafilatura` (pip install trafilatura). Falls back to the RSS snippet
if trafilatura isn't installed, so the source still returns *something*. Public
pages only; you are responsible for complying with each site's Terms of Service.
"""

import re
import urllib.parse
import urllib.request

from synthsociety.grounding import GroundingSource, register

_RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def _rss_links(xml: str, cap: int) -> list:
    out = []
    for m in re.finditer(r"(?s)<item>(.*?)</item>", xml):
        block = m.group(1)
        link = re.search(r"<link>(.*?)</link>", block)
        desc = re.search(r"(?s)<description>(.*?)</description>", block)
        url = link.group(1).strip() if link else ""
        snippet = re.sub(r"<[^>]+>", " ", desc.group(1)) if desc else ""
        snippet = re.sub(r"\s+", " ", snippet).strip()
        if url:
            out.append((url, snippet))
        if len(out) >= cap:
            break
    return out


@register
class WebSource(GroundingSource):
    name = "web"
    label = "Web articles (full text via trafilatura)"
    requires = []
    needs_package = "trafilatura"

    def fetch(self, topics: list, limit: int = 80) -> list:
        try:
            import trafilatura  # noqa: F401
            have_traf = True
        except ImportError:
            have_traf = False
        out, per_topic = [], max(2, limit // max(1, len(topics)))
        warned = False
        for topic in topics:
            try:
                url = _RSS.format(q=urllib.parse.quote(topic))
                req = urllib.request.Request(url, headers={"User-Agent": "synthetic-society/0.1"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    xml = r.read().decode("utf-8", errors="replace")
            except Exception as e:
                if not warned:
                    print(f"  (web) article discovery failed for '{topic}': {e}")
                    warned = True
                continue
            for link_url, snippet in _rss_links(xml, per_topic):
                text = ""
                if have_traf:
                    try:
                        downloaded = trafilatura.fetch_url(link_url)
                        if downloaded:
                            text = trafilatura.extract(downloaded, include_comments=False) or ""
                    except Exception:
                        text = ""
                text = (text or snippet).strip()
                if len(text) >= 120:
                    out.append({"quote": text[:1600], "source": "web", "topic": topic})
                if len(out) >= limit:
                    return out
        return out
