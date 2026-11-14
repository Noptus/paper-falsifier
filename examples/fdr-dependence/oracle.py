"""Author-maintained SciPy reference for the exact small acceptance vectors."""

import json
import random
from pathlib import Path

from scipy.stats import false_discovery_control


ROOT = Path(__file__).resolve().parent


def fixture_inputs():
    yield "singleton-exact-threshold", 0.05, [0.05]
    yield "no-rejections", 0.05, [0.8, 1.0, 0.36, 0.42]
    yield "all-rejections-both-procedures", 0.05, [0.001, 0.002, 0.0]
    yield "ties-bh-but-not-by", 0.05, [0.022, 0.022, 1.0, 1.0]
    yield "last-step-accept-despite-first-miss", 0.05, [0.9, 0.023, 0.021, 0.8]
    yield "reversed-unordered", 0.05, [0.9, 0.005, 0.001, 0.8, 0.04, 0.035]
    yield "minimum-zero", 0.05, [0.8, 0.0, 0.33]
    yield "edge-p-one", 0.95, [1.0, 1.0, 0.99, 1.0]
    rng = random.Random(120930)
    for size in (4, 16, 64):
        for index in range(4):
            pvals = [rng.uniform(0, 0.12) if (n + index) % 3 == 0 else rng.random()
                     for n in range(size)]
            rng.shuffle(pvals)
            yield f"seeded-{size}-{index + 1}", 0.1, pvals


def protocol():
    source = json.loads((ROOT / "source.json").read_text())
    plan = json.loads((ROOT / "study-plan.json").read_text())
    cases = []
    for name, alpha, pvalues in fixture_inputs():
        counts = [int((false_discovery_control(pvalues, method=m) <= alpha).sum())
                  for m in plan["procedures"]]
        cases.append({"name": name, "inputs": [alpha, *pvalues], "expected": counts,
                      "atol": 0, "rtol": 0})
    return {
        "schema": 1,
        "paper": {key: source[key] for key in ("identifier", "title", "pdf_sha256")},
        "mode": "applicability",
        "claim": "BH and BY step-up implementations reproduce independent reference rejection counts on the frozen p-value fixtures.",
        "scope": "Fixtures cover unsorted vectors, ties, zeros, exact equality, step-up behavior and seed-generated data. A separate registered simulation measures FDR under three specified all-null distributions.",
        "references": [
            {"location": "§2, equation (2) and Theorem 1",
             "source_claim": "The BH step-up rule selects the maximal rank satisfying the linear threshold."},
            {"location": "§5, Theorem 4 and Corollary 2",
             "source_claim": "The arbitrary-dependence bound inflates by the harmonic factor, yielding the BY correction."},
        ],
        "cases": cases,
    }


if __name__ == "__main__":
    print(json.dumps(protocol(), indent=2, allow_nan=False))
