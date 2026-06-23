"""Budget guard — a hard USD cap across a whole run.

Because a run executes its steps as separate subprocesses, cumulative spend is
persisted to a spend file so the cap spans every step. Each API call records its
real token usage (resp.usage); dollar cost is computed at the model's rates.

When cumulative spend reaches the cap, metered_call() raises BudgetExceeded BEFORE
launching the next call. In-flight calls (<= max_concurrent) finish, so the true
stop lands at ~cap + a few cents. Steps catch BudgetExceeded, save partial output,
and exit(BUDGET_EXIT_CODE); the orchestrator reports where it got to and stops.
"""

import json
import os

# ── Model token rates (USD per token): (input, output) per 1M ────────────────
# Add models here as needed. Used by both the live meter and the up-front
# estimator (cost.py) so estimates and the cap use the same numbers.
MODEL_RATES = {
    "claude-opus-4-8":       (15.0 / 1e6, 75.0 / 1e6),
    "claude-sonnet-4-6":     (3.0 / 1e6, 15.0 / 1e6),
    "claude-haiku-4-5-20251001": (1.0 / 1e6, 5.0 / 1e6),
}
DEFAULT_MODEL = "claude-sonnet-4-6"


def rates_for(model: str):
    """(input_rate, output_rate) per token for a model id, defaulting to Sonnet."""
    return MODEL_RATES.get(model, MODEL_RATES[DEFAULT_MODEL])


# Distinct exit code steps use to signal "stopped on budget" (vs a real error)
BUDGET_EXIT_CODE = 2


class BudgetExceeded(Exception):
    def __init__(self, spent: float, budget: float, calls: int):
        self.spent, self.budget, self.calls = spent, budget, calls
        super().__init__(f"Budget ${budget:.2f} reached: spent ${spent:.4f} over {calls} calls")


class CostMeter:
    """Tracks real spend across a run and enforces a hard cap.

    model: used to look up rates for the *uncached* portion. Cache creation is
    billed at 1.25x input and cache reads at 0.10x input (Anthropic pricing), so
    the meter stays accurate whether or not prompt caching is used.
    """

    def __init__(self, budget_usd: float, spend_file: str, model: str = DEFAULT_MODEL):
        self.budget = budget_usd
        self.spend_file = spend_file
        self.in_rate, self.out_rate = rates_for(model)
        self.spent = 0.0
        self.calls = 0
        self.in_tokens = 0
        self.out_tokens = 0
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.spend_file):
            try:
                with open(self.spend_file, "r", encoding="utf-8") as f:
                    d = json.load(f)
                self.spent = float(d.get("spent", 0.0))
                self.calls = int(d.get("calls", 0))
                self.in_tokens = int(d.get("in_tokens", 0))
                self.out_tokens = int(d.get("out_tokens", 0))
            except Exception:
                pass  # corrupt/partial file — start fresh rather than crash

    def _persist(self) -> None:
        os.makedirs(os.path.dirname(self.spend_file), exist_ok=True)
        with open(self.spend_file, "w", encoding="utf-8") as f:
            json.dump({
                "spent": round(self.spent, 6), "calls": self.calls,
                "in_tokens": self.in_tokens, "out_tokens": self.out_tokens,
                "budget": self.budget,
            }, f, indent=2)

    def reset(self) -> None:
        self.spent = self.calls = self.in_tokens = self.out_tokens = 0
        self.spent = 0.0
        self._persist()

    @property
    def tripped(self) -> bool:
        return self.spent >= self.budget

    def check(self) -> None:
        """Raise if the cap is already reached — prevents launching a new call."""
        if self.tripped:
            raise BudgetExceeded(self.spent, self.budget, self.calls)

    def record(self, usage) -> None:
        """on_usage callback for call_claude — runs after each successful call."""
        it = int(getattr(usage, "input_tokens", 0) or 0)
        ot = int(getattr(usage, "output_tokens", 0) or 0)
        cc = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        cr = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        self.in_tokens += it + cc + cr
        self.out_tokens += ot
        self.spent += (it * self.in_rate
                       + cc * self.in_rate * 1.25
                       + cr * self.in_rate * 0.10
                       + ot * self.out_rate)
        self.calls += 1
        self._persist()


async def metered_call(client, model, system, user, meter: CostMeter, max_tokens: int = 1024):
    """Budget-checked wrapper around call_claude: checks the cap before the call,
    records real usage after."""
    from synthsociety.client import call_claude
    meter.check()
    return await call_claude(client, model, system, user,
                             max_tokens=max_tokens, on_usage=meter.record)


def report_stop(step_name: str, meter: CostMeter, resume_hint: str, detail: str = "") -> None:
    """Print a clear 'where it got to' summary when the budget cap trips."""
    print("\n" + "=" * 60)
    print("  BUDGET STOP — hard cap reached")
    print("=" * 60)
    print(f"  Step reached:  {step_name}")
    if detail:
        print(f"  Got to:        {detail}")
    print(f"  Spent:         ${meter.spent:.2f}  (cap ${meter.budget:.2f})")
    print(f"  API calls:     {meter.calls:,}  ({meter.in_tokens:,} in / {meter.out_tokens:,} out)")
    print("  Partial output for completed steps has been saved.")
    print(f"  To finish: {resume_hint}")
    print("=" * 60 + "\n")
