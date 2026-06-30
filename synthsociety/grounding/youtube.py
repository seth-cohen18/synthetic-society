"""YouTube grounding (optional) — real comments on relevant videos.

Set YOUTUBE_API_KEY (enable "YouTube Data API v3" in Google Cloud). Uses the REST
API directly over urllib, so no extra pip package is required. You are responsible
for complying with YouTube's API Terms of Service.

Flow: search videos for each topic -> pull top-relevance comment threads as quotes.
"""

import json
import os
import urllib.parse
import urllib.request

from synthsociety.grounding import GroundingSource, register

_API = "https://www.googleapis.com/youtube/v3"


def _get(path: str, params: dict) -> dict:
    url = f"{_API}/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "synthetic-society/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


@register
class YouTubeSource(GroundingSource):
    name = "youtube"
    label = "YouTube comments (YouTube Data API key)"
    requires = ["YOUTUBE_API_KEY"]

    def fetch(self, topics: list, limit: int = 80) -> list:
        key = os.getenv("YOUTUBE_API_KEY")
        if not key:
            return []
        out, per_topic = [], max(4, limit // max(1, len(topics)))
        warned = False
        for topic in topics:
            try:
                search = _get("search", {
                    "key": key, "q": topic, "part": "id",
                    "type": "video", "maxResults": 3, "relevanceLanguage": "en",
                })
                video_ids = [it["id"]["videoId"] for it in search.get("items", [])
                             if it.get("id", {}).get("videoId")]
            except Exception as e:
                if not warned:  # surface a bad key / quota instead of silent "0 quotes"
                    print(f"  (youtube) search failed for '{topic}': {e}. (bad YOUTUBE_API_KEY or quota?)")
                    warned = True
                continue
            for vid in video_ids:
                try:
                    threads = _get("commentThreads", {
                        "key": key, "videoId": vid, "part": "snippet",
                        "order": "relevance", "maxResults": per_topic, "textFormat": "plainText",
                    })
                except Exception:
                    continue
                for it in threads.get("items", []):
                    sn = it["snippet"]["topLevelComment"]["snippet"]
                    text = (sn.get("textDisplay") or "").strip()
                    if 60 <= len(text) <= 1600:
                        out.append({"quote": text[:1600], "source": "youtube", "topic": topic})
                    if len(out) >= limit:
                        return out
        return out
