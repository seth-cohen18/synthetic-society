"""The City contract.

A city is one research method. To add one, subclass City, implement the three
methods, and register it (see cities/__init__.py). The launcher discovers it and
nothing else changes.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from synthsociety.budget import CostMeter
    from synthsociety.cost import CostProjection
    from synthsociety.wizard import Question


@dataclass
class RunContext:
    """Everything a city needs to run, assembled by the launcher after the wizard."""
    brief: dict                      # product understanding (see understand.py)
    grounding: list                  # quote pool (may be empty)
    n: int                           # population size the user chose
    model: str
    out_dir: str                     # runs/<city>/<timestamp>/  — all output goes here
    meter: "CostMeter"               # shared budget meter (the hard cap)
    answers: dict = field(default_factory=dict)   # city-specific wizard answers
    limit: bool = False              # smoke-test mode (small slice)


class City:
    id: str = "base"
    name: str = "Base City"
    description: str = ""
    n_label: str = "personas"        # what the population unit is called
    default_n: int = 30              # recommended population size
    min_n: int = 5

    def setup_questions(self) -> list:
        """City-specific wizard questions (asked in addition to the shared ones:
        product source, grounding, population size, budget). Return [] if none."""
        return []

    def project_cost(self, n: int, answers: dict, model: str) -> "CostProjection":
        """Project the API workload for `n` of this city's units -> CostProjection."""
        raise NotImplementedError

    async def run(self, ctx: RunContext) -> str:
        """Execute the full pipeline. Write artifacts into ctx.out_dir, respect
        ctx.meter (use synthsociety.budget.metered_call), and return the path to
        the generated HTML report. On BudgetExceeded, save partial output and
        re-raise — the launcher reports where it stopped."""
        raise NotImplementedError
