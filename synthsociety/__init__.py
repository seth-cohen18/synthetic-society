"""Synthetic Society — a simulated population of AI personas for product research.

A flight simulator, not the flight: outputs are directional/ordinal signal for
finding blind spots, never evidence of real demand. Simulate first, talk to real
humans second.
"""

__version__ = "0.1.0"

# Windows consoles default to cp1252, which crashes on the Unicode characters used
# in console output (arrows, bullets, em-dashes, ⚑). Force UTF-8 on stdout/stderr
# for every process on first import of the package, before any print(). File writes
# already specify encoding="utf-8" separately.
import sys as _sys

for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
