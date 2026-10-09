"""Think Tank: the endless search must survive any idea it produces."""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tradingbotty.thinktank import FormulaError, mutate, parse, random_formula, show, size  # noqa: E402


def _long(rng):
    """A valid formula near the size limit: random parts added up while they fit."""
    f = show(random_formula(rng, 2))
    for _ in range(12):
        try:
            g = f"{f} + {show(random_formula(rng, 2))}"
            if size(parse(g)) > 26:
                break
            f = g
        except FormulaError:
            break
    return f


def test_evolution_never_breaks_a_batch_when_a_crossing_grows_too_big():
    """09/10 on the server: "Search batch failed: formula too big". A crossing of two long formulas grew past the
    size limit and the check meant to catch it called the parser that raised. Now the child keeps its parent's."""
    rng = random.Random(1)
    for i in range(400):
        a = {"name": "A", "score": _long(rng), "top": 3, "hold": 1}
        b = {"name": "B", "score": _long(rng)}
        child = mutate(random.Random(i), a, b, i)
        assert size(parse(child["score"])) <= 30
