"""Generate a split-conformal rank protocol from simple exact arithmetic."""

import json
from fractions import Fraction
from math import ceil


def fixture(name, residuals, numerator, denominator, predictions):
    rank = ceil(Fraction(len(residuals) + 1) * Fraction(numerator, denominator))
    bounded = rank <= len(residuals)
    intervals = []
    radius = sorted(residuals)[rank - 1] if bounded else 0
    for prediction in predictions:
        intervals.extend([prediction - radius, prediction + radius] if bounded else [0, 0])
    return {"name": name, "inputs": [len(residuals), numerator, denominator, len(predictions),
                                      *residuals, *predictions],
            "expected": [int(bounded), rank, *intervals], "atol": 1e-12, "rtol": 1e-12}


def protocol():
    return {
        "schema": 1,
        "paper": {
            "identifier": "2107.07511v6",
            "title": "A Gentle Introduction to Conformal Prediction and Distribution-Free Uncertainty Quantification",
            "pdf_sha256": "c69aa191d8363c25b36685db4adb7e6980e55ee39892fd3626206c16e1e0efa1",
        },
        "mode": "applicability",
        "claim": "For the synthetic absolute-residual fixtures, return the finite-sample corrected split-conformal order statistic and the corresponding symmetric prediction intervals.",
        "scope": "This isolates the rank calculation for absolute-error scores and held-out predictions. An unattainable finite rank produces the full real line, encoded as [found=0, rank, zero endpoint placeholders]. The benchmark does not estimate population coverage or test exchangeability.",
        "references": [
            {"location": "§1.1 steps 3–4, Eq. (2), and Theorem 1 (PDF pages 5–6)",
             "source_claim": "The calibration threshold uses the finite-sample-adjusted quantile. Form the prediction set of outputs whose scores do not exceed that threshold."},
        ],
        "cases": [
            fixture("rank-correction-vs-empirical-quantile", [4, 1, 3, 2], 1, 2, [10, -0.25]),
            fixture("permutation-invariant", [2, 4, 1, 3], 1, 2, [10, -0.25]),
            fixture("ties-and-multiple-predictions", [0.5, 3, 0.5, 1, 0.5], 2, 5, [-9, 0, 2]),
            fixture("rank-at-upper-finite-boundary", [1, 8, 3, 2], 4, 5, [0, -3]),
            fixture("rank-n-plus-one-is-unbounded", [0.1, 0.2, 0.3, 0.4], 9, 10, [2, 3]),
            fixture("zero-radius", [0, 0, 5, 0], 1, 2, [7.125, -5.125]),
            fixture("tiny-calibration-set", [2.25], 1, 2, [0.5]),
        ],
    }


if __name__ == "__main__":
    print(json.dumps(protocol(), ensure_ascii=False, allow_nan=False, indent=2))
