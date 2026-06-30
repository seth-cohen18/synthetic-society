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
        try:
            reddit = praw.Reddit(
                client_id=os.getenv("REDDIT_CLIENT_ID"),
                client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
                user_agent=os.getenv("REDDIT_USER_AGENT", "synthetic-society/0.1"),
            )
            reddit.read_only = True
        except Exception as e:
            # Surface bad credentials instead of silently returning [] (which would
            # look like "no data" rather than "your REDDIT_* creds are wrong").
            print(f"  (reddit) could not initialize client: {e}. Check your REDDIT_* credentials.")
            return []
        out, per_topic = [], max(4, limit // max(1, len(topics)))
        warned = False
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
                            "score": int(getattr(sub, "score", 0) or 0),
                        })
                    if len(out) >= limit:
                        return out
                    # A couple of top comments add real first-person voice + objections.
                    try:
                        sub.comments.replace_more(limit=0)
                        for c in sub.comments[:3]:
                            ctext = (getattr(c, "body", "") or "").strip()
                            if 60 <= len(ctext) <= 1600:
                                out.append({
                                    "quote": ctext[:1600],
                                    "source": f"r/{sub.subreddit.display_name} (comment)",
                                    "topic": topic,
                                    "score": int(getattr(c, "score", 0) or 0),
                                })
                            if len(out) >= limit:
                                return out
                    except Exception:
                        pass  # comments are a bonus; submission text already captured
            except Exception as e:
                if not warned:  # report the first failure (auth/rate limit), then stay quiet
                    print(f"  (reddit) search failed for '{topic}': {e}. (bad credentials or rate limit?)")
                    warned = True
                continue
        return out
