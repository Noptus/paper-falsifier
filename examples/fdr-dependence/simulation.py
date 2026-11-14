"""Reproduce the registered all-null false-discovery experiment using SciPy."""

from argparse import ArgumentParser
from hashlib import sha256
from itertools import product
import json
from pathlib import Path
from statistics import harmonic_mean

import numpy as np
import scipy
from scipy.stats import binomtest, false_discovery_control


ROOT = Path(__file__).resolve().parent


def preregistration():
    for line in (ROOT / "preregistration.sha256").read_text().splitlines():
        expected, name = line.split(maxsplit=1)
        if sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Frozen artifact changed: {name}")
    path = ROOT / "study-plan.json"
    raw = path.read_bytes()
    plan = json.loads(raw)
    for module in (np, scipy):
        name = module.__name__
        if module.__version__ != plan["reference"][name]:
            raise ValueError(f"Run this study with its pinned {name} version")
    return plan, sha256(raw).hexdigest()


def analytic_rates(regime, level, harmonic, near_factor):
    if regime == "independent":
        return level, level / harmonic
    if regime == "near_bh_bound":
        return near_factor * level * harmonic, 0.0
    return near_factor * level, near_factor * level


def pvalue_rows(rng, regime, size, level, harmonic, near_factor, batch):
    if regime == "independent":
        return rng.random((batch, size))
    low_scale = near_factor * level
    if regime == "near_by_bound":
        low_scale /= harmonic
    ranks = np.arange(1, size + 1)
    masses = low_scale / ranks
    event_probability = np.concatenate(([1 - masses.sum()], masses))
    number_low = rng.choice(size + 1, size=batch, p=event_probability)
    shifts = rng.integers(0, size, size=batch)
    is_low = (np.arange(size) - shifts[:, None]) % size < number_low[:, None]
    return np.where(is_low, low_scale * number_low[:, None] / size, 1.0)


def estimate_cell(plan, family_size, level, regime, seed, conf):
    rng = np.random.default_rng(seed)
    size = family_size
    harmonic = size / harmonic_mean(range(1, size + 1))
    # harmonic_mean(1...m) = m / sum(1/k)
    assert harmonic > 1
    trial_count = plan["replicates_per_cell"]
    factor = plan["near_boundary_factor"]
    counts = {method: 0 for method in plan["procedures"]}
    for start in range(0, trial_count, 4096):
        batch = min(4096, trial_count - start)
        rows = pvalue_rows(rng, regime, size, level, harmonic, factor, batch)
        for method in counts:
            adjusted = false_discovery_control(rows, method=method, axis=1)
            counts[method] += int(np.any(adjusted <= level, axis=1).sum())
    rates = analytic_rates(regime, level, harmonic, factor)
    procs = {}
    for method, true_rate in zip(plan["procedures"], rates, strict=True):
        observed = counts[method]
        interval = binomtest(observed, trial_count).proportion_ci(conf, method="exact")
        procs[method] = {
            "rejections": observed,
            "estimated_rate": observed / trial_count,
            "analytic_rate": true_rate,
            "simultaneous_interval": [interval.low, interval.high],
        }
    return {"regime": regime, "family_size": size, "nominal_level": level, "procedures": procs}


def registered_decision(cells):
    primary = [row for row in cells if row["regime"] == "near_bh_bound"]
    comparisons = []
    for row in primary:
        level = row["nominal_level"]
        bh_low, bh_high = row["procedures"]["bh"]["simultaneous_interval"]
        by_low, by_high = row["procedures"]["by"]["simultaneous_interval"]
        if bh_low > level and by_high < level:
            cell_result = "supports"
        elif bh_high <= level or by_low >= level:
            cell_result = "falsifies"
        else:
            cell_result = "inconclusive"
        comparisons.append({"nominal_level": level, "family_size": row["family_size"],
                            "decision": cell_result})
    decisions = {row["decision"] for row in comparisons}
    if "falsifies" in decisions:
        status = "falsified_in_registered_simulation"
    elif decisions == {"supports"}:
        status = "supported_in_registered_simulation"
    else:
        status = "inconclusive_in_registered_simulation"
    return status, comparisons


def run():
    plan, plan_hash = preregistration()
    settings = list(product(plan["family_sizes"], plan["nominal_levels"], plan["regimes"]))
    family_size = len(settings) * len(plan["procedures"])
    confidence = 1 - plan["family_alpha"] / family_size
    seeds = np.random.SeedSequence(plan["seed"]).spawn(len(settings))
    cells = [estimate_cell(plan, *config, seed, confidence)
             for config, seed in zip(settings, seeds, strict=True)]
    status, comparisons = registered_decision(cells)
    return {
        "schema": 1,
        "paper": plan["paper"],
        "status": status,
        "plan_sha256": plan_hash,
        "simulation_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "reference": plan["reference"],
        "seed": plan["seed"],
        "independent_pvalue_families": len(settings) * plan["replicates_per_cell"],
        "shared_scenarios_for_bh_and_by": True,
        "replicates_per_cell": plan["replicates_per_cell"],
        "family_confidence": 1 - plan["family_alpha"],
        "simultaneous_interval_count": family_size,
        "interval_method": plan["interval"],
        "primary_comparisons": comparisons,
        "cells": cells,
    }


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write evidence to a new file")
    args = parser.parse_args()
    result = json.dumps(run(), indent=2, allow_nan=False) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(result)
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
