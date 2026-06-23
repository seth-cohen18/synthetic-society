"""Reddit grounding (optional). Bring your own app credentials.

Create a "script" app at https://www.reddit.com/prefs/apps and set:
    REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT
Requires `pip install praw`. You are responsible for complying with Reddit's API
Terms of Service. Note: scraping the public .json endpoints from a residential IP
is frequently blocked — the official API (what praw uses) is the reliable path.
"""

import os

from synthsociety.grounding import GroundingSource, register


@register
class RedditSource(GroundingSource):
    name = "reddit"
    label = "Reddit (your API app credentials)"
    requires = ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"]
    needs_package = "praw"

    def is_available(self) -> bool:
        if self.missing_requirements():
            return False
        try:
            import praw  # noqa: F401
        except ImportError:
            return False
        return True

    def fetch(self, topics: list, limit: int = 80) -> list:
        try:
            import praw
        except ImportError:
            return []
        reddit = praw.Reddit(
            client_id=os.getenv("REDDIT_CLIENT_ID"),
            client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
            user_agent=os.getenv("REDDIT_USER_AGENT", "synthetic-society/0.1"),
        )
        reddit.read_only = True
        out, per_topic = [], max(4, limit // max(1, len(topics)))
        for topic in topics:
            try:
                results = reddit.subreddit("all").search(topic, sort="relevance", limit=per_topic)
                for sub in results:
                    text = (sub.selftext or sub.title or "").strip()
                    if 60 <= len(text) <= 1600:
                        out.append({
                            "quote": text[:1600],
                            "source": f"r/{sub.subreddit.display_name}",
                            "topic": topic,
                        })
                    if len(out) >= limit:
                        return out
            except Exception:
                continue  # one bad topic shouldn't kill the whole fetch
        return out
