# Split-conformal rank correction

Angelopoulos and Bates describe the finite-sample adjustment for the calibration quantile in [arXiv:2107.07511v6](https://arxiv.org/abs/2107.07511v6), §1.1, steps 3–4, Eq. (2) and Theorem 1, PDF pages 5–6. This example uses absolute-error scores for symmetric intervals. It selects the one-based rank `ceil((n+1)*p/q)` for `n` calibration scores and target coverage `p/q`.

A rank of `n+1` has no finite residual threshold and produces an unbounded prediction set. This example encodes it with a `found=0` flag and zero endpoint placeholders. The oracle uses exact rational arithmetic and Python sorting. The candidate independently implements integer ceiling and insertion sort in WebAssembly.

Run from the repository root.

```bash
uv sync --locked
example=examples/split-conformal
uv run --locked paper-falsifier intake 2107.07511v6 --out .local/conformal-demo
sha=$(cat "$example/protocol.sha256")
test "$sha" = "$(uv run --locked paper-falsifier freeze "$example/protocol.json" --paper .local/conformal-demo/paper.pdf)"
uv run --locked paper-falsifier run "$example/protocol.json" "$example/candidate.wat" \
  --expect-sha "$sha" --paper .local/conformal-demo/paper.pdf
code=0
uv run --locked paper-falsifier run "$example/protocol.json" "$example/negative-control.wat" \
  --expect-sha "$sha" --paper .local/conformal-demo/paper.pdf || code=$?
test "$code" = 1
```

The [candidate report](result.json) passed seven cases, including unsorted scores, ties, zero radius and an unbounded set. The [negative control](negative-result.json) uses `ceil(n*p/q)` and failed five cases. The cases check the threshold and interval arithmetic. A claim about marginal coverage requires a study that addresses the sampling assumptions behind the theorem.
