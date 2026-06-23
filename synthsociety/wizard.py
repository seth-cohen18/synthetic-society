"""Interactive prompt helpers for the launcher.

Plain stdin/stdout — no TUI dependency. Each helper loops until it gets valid
input so a typo never crashes a run mid-setup.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple


@dataclass
class Question:
    key: str
    prompt: str
    kind: str = "text"            # text | int | bool | choice | multichoice
    choices: List[Tuple[str, str]] = field(default_factory=list)  # (value, label)
    default: Any = None
    help: str = ""
    validate: Optional[Callable[[Any], bool]] = None


def _hr():
    print("  " + "-" * 58)


def ask_text(prompt: str, default: Optional[str] = None, allow_empty: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        val = input(f"  {prompt}{suffix}\n  > ").strip()
        if not val and default is not None:
            return default
        if val or allow_empty:
            return val
        print("  (required)")


def ask_int(prompt: str, default: int, lo: int = 1, hi: int = 100000) -> int:
    while True:
        raw = input(f"  {prompt} [{default}]\n  > ").strip()
        if not raw:
            return default
        try:
            v = int(raw)
        except ValueError:
            print("  Enter a whole number.")
            continue
        if lo <= v <= hi:
            return v
        print(f"  Enter a number between {lo} and {hi}.")


def ask_bool(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    while True:
        raw = input(f"  {prompt} [{d}]\n  > ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  Enter y or n.")


def ask_choice(prompt: str, choices: List[Tuple[str, str]], default_index: int = 0) -> str:
    """choices: list of (value, label). Returns the chosen value."""
    print(f"  {prompt}")
    for i, (_, label) in enumerate(choices):
        marker = "*" if i == default_index else " "
        print(f"   {marker} {i + 1}. {label}")
    while True:
        raw = input(f"  > [{default_index + 1}] ").strip()
        if not raw:
            return choices[default_index][0]
        try:
            i = int(raw) - 1
        except ValueError:
            print("  Enter the number of your choice.")
            continue
        if 0 <= i < len(choices):
            return choices[i][0]
        print(f"  Enter 1-{len(choices)}.")


def ask_multichoice(prompt: str, choices: List[Tuple[str, str]]) -> List[str]:
    """Comma-separated multi-select. Returns list of chosen values (may be empty)."""
    print(f"  {prompt}  (comma-separated, or blank for none)")
    for i, (_, label) in enumerate(choices):
        print(f"     {i + 1}. {label}")
    while True:
        raw = input("  > ").strip()
        if not raw:
            return []
        try:
            idxs = [int(x) - 1 for x in raw.replace(" ", "").split(",") if x]
        except ValueError:
            print("  Enter numbers separated by commas.")
            continue
        if all(0 <= i < len(choices) for i in idxs):
            return [choices[i][0] for i in idxs]
        print(f"  Each number must be 1-{len(choices)}.")


def ask(q: Question) -> Any:
    if q.help:
        print(f"  ({q.help})")
    if q.kind == "int":
        return ask_int(q.prompt, q.default or 1)
    if q.kind == "bool":
        return ask_bool(q.prompt, bool(q.default))
    if q.kind == "choice":
        di = 0
        if q.default is not None:
            di = next((i for i, (v, _) in enumerate(q.choices) if v == q.default), 0)
        return ask_choice(q.prompt, q.choices, di)
    if q.kind == "multichoice":
        return ask_multichoice(q.prompt, q.choices)
    return ask_text(q.prompt, q.default, allow_empty=(q.default is not None))


def banner(title: str, subtitle: str = ""):
    print("\n" + "=" * 62)
    print(f"  {title}")
    if subtitle:
        print(f"  {subtitle}")
    print("=" * 62)


def section(title: str):
    print()
    _hr()
    print(f"  {title}")
    _hr()
