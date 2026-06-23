"""Up-front cost estimation.

A city projects its API workload as a CostProjection (how many calls, and the
average input/output tokens per call type). This module turns that into a dollar
estimate at the chosen model's rates — shown to the user BEFORE any spend, so they
can dial the population size up or down. Estimates are deliberately a touch
conservative; the live budget cap (budget.py) is the real safety net.
"""

from dataclasses import dataclass, field

from synthsociety.budget import rates_for


@dataclass
class CallGroup:
    """One kind of API call in a city's workload."""
    label: str           # e.g. "persona generation", "survey round 1"
    count: int           # how many calls of this kind
    avg_in: int          # avg input tokens per call
    avg_out: int         # avg output tokens per call

    def usd(self, model: str) -> float:
        in_rate, out_rate = rates_for(model)
        return self.count * (self.avg_in * in_rate + self.avg_out * out_rate)


@dataclass
class CostProjection:
    """A city's full projected workload for a given population size."""
    model: str
    groups: list = field(default_factory=list)

    def add(self, label: str, count: int, avg_in: int, avg_out: int) -> "CostProjection":
        self.groups.append(CallGroup(label, count, avg_in, avg_out))
        return self

    @property
    def total_calls(self) -> int:
        return sum(g.count for g in self.groups)

    @property
    def total_usd(self) -> float:
        return sum(g.usd(self.model) for g in self.groups)

    def format(self) -> str:
        lines = ["  Estimated cost breakdown:"]
        for g in self.groups:
            lines.append(f"    {g.label:<34} {g.count:>4} calls   ~${g.usd(self.model):.2f}")
        lines.append("    " + "-" * 50)
        lines.append(f"    {'TOTAL':<34} {self.total_calls:>4} calls   ~${self.total_usd:.2f}")
        lines.append(f"\n  Model: {self.model}")
        lines.append("  (Estimate only — the run enforces your hard budget cap regardless.)")
        return "\n".join(lines)


def suggest_budget_cap(estimate_usd: float) -> float:
    """A safe default hard cap: ~1.5x the estimate, rounded up to the next dollar,
    minimum $2. Gives headroom for variance without risking a surprise bill."""
    import math
    return max(2.0, float(math.ceil(estimate_usd * 1.5)))
