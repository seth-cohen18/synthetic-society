"""YouTube transcript grounding (optional) — real spoken first-person language.

Uses `yt-dlp` (pip install yt-dlp; the yt-dlp binary must be on PATH) to search
videos and pull their English captions. No API key needed — yt-dlp performs the
search itself. Transcripts are the richest *voice* signal (how people actually
talk), complementary to comments (which carry objections). You are responsible
for complying with YouTube's Terms of Service.
"""

import glob
import os
import re
import shutil
import subprocess
import tempfile

from synthsociety.grounding import GroundingSource, register


def _parse_vtt(path: str) -> str:
    """Flatten a .vtt caption file to plain text, dropping timestamps, cue tags,
    and the consecutive duplicate lines auto-captions are full of."""
    lines = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or "-->" in line:
                    continue
                if line.upper().startswith(("WEBVTT", "KIND", "LANGUAGE", "NOTE")):
                    continue
                line = re.sub(r"<[^>]+>", "", line)  # inline <00:00:01.000> timing tags
                if line and (not lines or lines[-1] != line):
                    lines.append(line)
    except Exception:
        return ""
    return " ".join(lines)


@register
class YouTubeTranscriptSource(GroundingSource):
    name = "youtube_transcripts"
    label = "YouTube transcripts (spoken captions via yt-dlp)"
    requires = []
    needs_package = "yt-dlp"

    def is_available(self) -> bool:
        return shutil.which("yt-dlp") is not None

    def fetch(self, topics: list, limit: int = 80) -> list:
        if not shutil.which("yt-dlp"):
            return []
        out, per_topic = [], max(1, limit // max(1, len(topics)))
        for topic in topics:
            with tempfile.TemporaryDirectory() as td:
                try:
                    subprocess.run(
                        ["yt-dlp", f"ytsearch{per_topic}:{topic}",
                         "--write-auto-sub", "--sub-lang", "en", "--sub-format", "vtt",
                         "--skip-download", "--no-warnings", "--quiet",
                         "-o", os.path.join(td, "%(id)s.%(ext)s")],
                        timeout=120, check=False,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                except Exception:
                    continue  # yt-dlp missing/blocked/timeout — best-effort source
                for vtt in glob.glob(os.path.join(td, "*.vtt")):
                    text = _parse_vtt(vtt)
                    if len(text) >= 120:
                        out.append({"quote": text[:1600], "source": "youtube_transcript",
                                    "topic": topic})
                    if len(out) >= limit:
                        return out
        return out
