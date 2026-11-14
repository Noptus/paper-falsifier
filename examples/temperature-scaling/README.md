# Temperature scaling

Guo, Pleiss, Sun and Weinberger fit a single positive temperature by minimizing validation negative log likelihood, then divide test logits by it. Positive scaling preserves the argmax. The source is [arXiv:1706.04599v2](https://arxiv.org/abs/1706.04599v2), §4.2, Eq. (9), PDF page 6.

This experiment fixes the temperature grid at 0.5, 1, 2, 4 and 8. It uses synthetic logits for fitting and unseen logits for inference. The Python oracle uses log-sum-exp with standard-library exponentials. Candidate code instead evaluates a bounded product of labeled probabilities with a local exponential approximation. The controls cover overconfidence, constant shifts, ties, fit/test separation, and two through four classes.

Run from the repository root.

```bash
uv sync --locked
example=examples/temperature-scaling
uv run --locked paper-falsifier intake 1706.04599v2 --out .local/temperature-demo
sha=$(cat "$example/protocol.sha256")
test "$sha" = "$(uv run --locked paper-falsifier freeze "$example/protocol.json" --paper .local/temperature-demo/paper.pdf)"
uv run --locked paper-falsifier run "$example/protocol.json" "$example/candidate.wat" \
  --expect-sha "$sha" --paper .local/temperature-demo/paper.pdf
code=0
uv run --locked paper-falsifier run "$example/protocol.json" "$example/negative-control.wat" \
  --expect-sha "$sha" --paper .local/temperature-demo/paper.pdf || code=$?
test "$code" = 1
```

The [candidate report](result.json) passed six synthetic cases. The [negative control](negative-result.json) substitutes uniform predictions at a fixed temperature and failed all six. The results support numerical behavior on these fixtures. Measuring empirical calibration gains would require held-out labeled datasets and trained model outputs.
