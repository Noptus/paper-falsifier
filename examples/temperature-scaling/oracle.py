"""Generate the committed protocol with an independent Python math oracle."""

import json
import math
from pathlib import Path

TEMPERATURES = (0.5, 1, 2, 4, 8)
PDF_SHA = "cb654a65acb785ed4387d6c2db1301d065075dddc2d363676836a2f2a14bc50d"


def probabilities(row, temperature):
    peak = max(row)
    exponentials = [math.exp((logit - peak) / temperature) for logit in row]
    normalization = math.fsum(exponentials)
    return [term / normalization for term in exponentials]


def negative_log_likelihood(fit, temperature):
    losses = []
    for row, label in fit:
        peak = max(row)
        log_sum_exp = math.log(math.fsum(math.exp((logit - peak) / temperature) for logit in row))
        losses.append(log_sum_exp + (peak - row[label]) / temperature)
    return math.fsum(losses) / len(losses)


def case(name, fit, test):
    best = min(TEMPERATURES, key=lambda t: negative_log_likelihood(fit, t))
    class_count = len(fit[0][0])
    inputs = [len(fit), len(test), class_count]
    for row, label in fit:
        inputs.extend([*row, label])
    for row in test:
        inputs.extend(row)
    predictions = [value for row in test for value in probabilities(row, best)]
    for i, row in enumerate(test):
        prediction = predictions[i * class_count:(i + 1) * class_count]
        assert math.isclose(math.fsum(prediction), 1.0)
        assert row.index(max(row)) == prediction.index(max(prediction))
    return {"name": name, "inputs": inputs, "expected": [best, *predictions],
            "atol": 1e-10, "rtol": 1e-8}


def protocol():
    fit = [
        ([6.0, 0.0, -3.0], 0), ([6.0, 0.0, -3.0], 0),
        ([6.0, 0.0, -3.0], 0), ([0.0, 6.0, -3.0], 1),
        ([0.0, 6.0, -3.0], 1), ([6.0, 0.0, -3.0], 1),
        ([0.0, -3.0, 6.0], 2), ([0.0, -3.0, 6.0], 2),
    ]
    test = [[6.0, 0.0, -3.0], [1.25, -6.0, 4.5], [-3.0, 1.0, 1.0]]
    correct = [([1.0, 0.0], 0), ([0.0, 1.0], 1)] * 3
    flat = [([2.0, 2.0, 2.0], i % 3) for i in range(6)]
    controls = [
        case("overconfident-multiclass", fit, test),
        case("row-shifts-invariant",
             [([value + 8192.0 for value in row], label) for row, label in fit],
             [[value - 8192.0 for value in row] for row in test]),
        case("confident-correct-fit", correct, [[1.0, 0.0], [-1.0, 1.5]]),
        case("equal-fit-loss-tie-break", flat, [[1.0, 1.0, 1.0], [3.0, -1.0, 0.0]]),
        case("test-split-does-not-change-fit", fit, [[0.5, -1.0, 6.0]]),
        case("four-class-calibration",
             [([4.0, 0.0, -0.5, -2.0], class_label) for class_label in (0, 0, 0, 1, 0, 1)],
             [[3.0, 2.0, 1.0, 0.0], [-7.0, 6.0, -5.0, -2.0]]),
    ]
    assert controls[0]["expected"][0] > 1
    assert controls[0]["expected"] == controls[1]["expected"]
    assert controls[0]["expected"][0] == controls[4]["expected"][0]
    return {
        "schema": 1,
        "paper": {
            "identifier": "1706.04599v2",
            "title": "On Calibration of Modern Neural Networks",
            "pdf_sha256": PDF_SHA,
        },
        "mode": "applicability",
        "claim": "For the synthetic fixtures, select the NLL-optimal positive temperature on the fixed validation grid and produce the expected held-out softmax probabilities without changing predicted labels.",
        "scope": "Synthetic logits and an explicitly fixed temperature grid [0.5, 1, 2, 4, 8]. This checks numerical behavior and split discipline. It uses neither the original trained models nor their datasets, and cannot estimate out-of-sample calibration gains.",
        "references": [
            {"location": "§4.2, Temperature scaling, Eq. (9) (PDF page 6)",
             "source_claim": "A scalar positive temperature divides all logits. Fit it on validation negative log likelihood and apply it on test logits. Positive scaling preserves the predicted class."},
        ],
        "cases": controls,
    }


if __name__ == "__main__":
    print(json.dumps(protocol(), ensure_ascii=False, allow_nan=False, indent=2))
