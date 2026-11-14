# Paper Falsifier

Give a coding agent an arXiv link. Get a pinned citation, a scoped numerical hypothesis, a new implementation, and a report comparing it with independent controls.

The agent drafts the experiment and writes a new candidate. The verifier gives it numeric inputs through WebAssembly and checks its outputs against a frozen protocol. A deliberately faulty candidate tests whether the protocol catches a meaningful defect.

## Try it

Requires Python 3.11 or newer. With [uv](https://docs.astral.sh/uv/), run these commands from the repository root. You can also install with `python -m pip install -e .` in a virtual environment and omit `uv run`.

```bash
uv sync --locked
uv run paper-falsifier intake https://arxiv.org/abs/1706.04599 --out .local/guo
example=examples/temperature-scaling
protocol_sha=$(uv run paper-falsifier freeze "$example/protocol.json" --paper .local/guo/paper.pdf)
uv run paper-falsifier run "$example/protocol.json" "$example/candidate.wat" \
  --expect-sha "$protocol_sha" --paper .local/guo/paper.pdf
```

Repeat with `$example/negative-control.wat`. It should report mismatches and exit 1. The example evaluates six synthetic cases for temperature scaling under a fixed temperature grid. It does not estimate calibration gains on the paper’s datasets.

## Reproduced examples

| Paper | Scoped test | Evidence |
| --- | --- | --- |
| [Guo et al., ICML 2017](https://arxiv.org/abs/1706.04599v2) | [Fixed-grid temperature scaling on synthetic logits](examples/temperature-scaling/) | [Candidate](examples/temperature-scaling/result.json) · [Negative control](examples/temperature-scaling/negative-result.json) |
| [Angelopoulos and Bates, 2022 preprint](https://arxiv.org/abs/2107.07511v6) | [Split-conformal rank correction for precomputed calibration residuals](examples/split-conformal/) | [Candidate](examples/split-conformal/result.json) · [Negative control](examples/split-conformal/negative-result.json) |

Each example has its own replay commands, frozen protocol, independent Python oracle, original WebAssembly guest, deliberate defect and pinned source metadata. Large third-party files are retrieved on demand.

## Give the skill an arXiv link

Codex can discover the [Paper Falsifier skill](.agents/skills/paper-falsifier/SKILL.md). In Claude Code, invoke `/paper-falsifier`. For another project, copy the skill directory to `.agents/skills` for Codex or `.claude/skills` for Claude Code and install the verifier from this checkout.

> Use Paper Falsifier on https://arxiv.org/abs/1706.04599. Test whether scalar temperature scaling preserves predicted labels and lowers overconfidence on a synthetic classifier. Return a runnable candidate, a frozen protocol, a negative control and a report.

The skill targets benign numerical ML and mathematical methods that fit the guest ABI. A matched reproduction needs the source paper’s data, splits, methods and metric. An applicability test uses a stated narrower design and explains what the result supports.

## How it works

1. **Intake.** Resolve a versioned arXiv record and hash the PDF bytes. Record title, authors, source URLs and retrieval time.
2. **Freeze.** Define the claim, source locations, expected values and tolerances before authoring the candidate. Keep the oracle separate and store the hash in a reviewed record.
3. **Implement.** Write an original .wat or .wasm numerical guest. Supply only its ABI and method description when delegating implementation separately.
4. **Verify.** Run the candidate and a deliberately faulty control. The report binds the protocol and candidate by SHA-256 and indicates whether a local PDF matched its recorded source hash.

Guests have zero imports, no WASI, and no filesystem, socket or process capability. Wasmtime runs each case in a fresh worker with an empty environment, bounded linear memory, instruction fuel and a wall-clock timeout. Keep the runtime updated and use an additional unprivileged container for third-party modules at scale.

See the [protocol and guest ABI](docs/protocol.md).

## Development

```bash
uv run --locked python -m unittest discover -s tests -v
uv run --locked python examples/temperature-scaling/oracle.py
```

Source paper: C. Guo, G. Pleiss, Y. Sun and K. Q. Weinberger, *On Calibration of Modern Neural Networks*, ICML 2017, §4.2. [Versioned arXiv record](https://arxiv.org/abs/1706.04599v2).

MIT licensed. Paper texts and linked resources retain their own licenses.
