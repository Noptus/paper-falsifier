# Paper Falsifier

Give a coding agent an arXiv link. Get a pinned citation, a scoped numerical hypothesis, a new implementation, and a report comparing it with independent controls.

The agent drafts the experiment and writes a new candidate. The verifier gives it numeric inputs through WebAssembly and checks its outputs against a frozen protocol. A deliberately faulty candidate tests whether the protocol catches a meaningful defect.

## First run

Requires Python 3.11 or newer. With [uv](https://docs.astral.sh/uv/), run these commands from the repository root. You can also install with `python -m pip install -e .` in a virtual environment and omit `uv run`.

```bash
uv sync --locked
```

Then start a managed study with one Claude slash command, `/paper-falsifier <arXiv-link>`, or directly:

```bash
uv run paper-falsifier study new https://arxiv.org/abs/2201.09350
uv run paper-falsifier study status studies/2201.09350
```

The default workspace is `studies/<arXiv identifier>`. Use an explicit second argument to choose a different destination. Complete the claim and reference vectors in `protocol.json` and describe your design in `methodology.md`.

- **seal** — `paper-falsifier study seal <dir>` freezes `protocol.json` and `methodology.md` into a local preregistration. Add `--artifact <relative-path>`, repeatable, to bind an independent reference, dataset or plan by SHA-256.
- **brief** — `paper-falsifier study brief <dir>` prints the claim, scope, citations and guest ABI for the author and withholds every expected vector and the oracle.
- **verify** — `paper-falsifier study verify <dir>` runs `candidate.wat` and `negative-control.wat` against the sealed inputs and writes an append-only receipt. Compiled `.wasm` guests using the same ABI are accepted.
- **status** — `paper-falsifier study status <dir>` names the next action at any point.

Pass `--expect-seal <digest>` to `brief` and `verify` to bind the action to the sealed digest. To establish timing independently, publish or timestamp the seal digest before implementation. Pass that externally recorded digest when handing off and verifying.

## Intake and manual control

For a single protocol without the managed layout, call the stages directly.

```bash
uv run paper-falsifier intake https://arxiv.org/abs/1706.04599 --out .local/guo
example=examples/temperature-scaling
protocol_sha=$(uv run paper-falsifier freeze "$example/protocol.json" --paper .local/guo/paper.pdf)
uv run paper-falsifier run "$example/protocol.json" "$example/candidate.wat" \
  --expect-sha "$protocol_sha" --paper .local/guo/paper.pdf
```

Repeat with `$example/negative-control.wat`. It should report mismatches and exit 1. The example evaluates six synthetic cases for temperature scaling under a fixed temperature grid. It does not estimate calibration gains on the paper’s datasets.

## Reproduce

| Paper | Scoped test | Evidence |
| --- | --- | --- |
| [Guo et al., ICML 2017](https://arxiv.org/abs/1706.04599v2) | [Fixed-grid temperature scaling on synthetic logits](examples/temperature-scaling/) | [Candidate](examples/temperature-scaling/result.json) · [Negative control](examples/temperature-scaling/negative-result.json) |
| [Angelopoulos and Bates, 2022 preprint](https://arxiv.org/abs/2107.07511v6) | [Split-conformal rank correction for precomputed calibration residuals](examples/split-conformal/) | [Candidate](examples/split-conformal/result.json) · [Negative control](examples/split-conformal/negative-result.json) |
| [Wang, 2022 preprint](https://arxiv.org/abs/2201.09350v3) | [BH and BY false-discovery control on a registered all-null simulation](examples/fdr-dependence/) | [Simulation](examples/fdr-dependence/simulation-result.json) · [Guest verification](examples/fdr-dependence/verification.json) |

Each example has its own replay commands, frozen protocol, independent Python oracle, original WebAssembly guest, deliberate defect and pinned source metadata. Large third-party files are retrieved on demand.

## Give the skill an arXiv link

Codex can discover the [Paper Falsifier skill](.agents/skills/paper-falsifier/SKILL.md). In Claude Code, invoke `/paper-falsifier`. For another project, copy the skill directory to `.agents/skills` for Codex or `.claude/skills` for Claude Code and install the verifier from this checkout.

> Use Paper Falsifier on https://arxiv.org/abs/1706.04599. Test whether scalar temperature scaling preserves predicted labels and lowers overconfidence on a synthetic classifier. Return a runnable candidate, a frozen protocol, a negative control and a report.

The skill targets benign numerical ML and mathematical methods that fit the guest ABI. A matched reproduction needs the source paper’s data, splits, methods and metric. An applicability test uses a stated narrower design and explains what the result supports.

## A grounded test

A useful study answers a short list of methodological questions.

- Does the scoped estimand match the paper’s actual claim, or does it cover a narrower population?
- Do the parameters, data and random seeds in the protocol match the ones the claim describes?
- What rival explanations could produce a pass, and does the design rule them out?
- Does the negative control or mutation exercise a defect the candidate must detect?
- Are the intervals statistically appropriate, and is a negative conclusion reported with the same care as a positive one?

Expected values come from an existing scientist tool such as SciPy, R or scikit-learn, or from trusted author code reviewed in a separate workflow. Those tools and that reviewed code produce the independent outputs. Paper Falsifier reads the paper as bytes and runs the sealed guests inside Wasmtime. It executes no author package or script linked by the paper.

When a model or experiment changes after seeing test outcomes, keep the original result. Any confirmatory rerun needs a fresh unseen holdout. The public fixtures in this repository are for reproduction.

Guests have zero imports, no WASI, and no filesystem, socket or process capability. Wasmtime runs each case in a fresh worker with an empty environment, bounded linear memory, instruction fuel and a wall-clock timeout. Keep the runtime updated and use an additional unprivileged container for third-party modules at scale.

See the [protocol and guest ABI](docs/protocol.md).

## Development

```bash
uv run --locked python -m unittest discover -s tests -v
uv run --locked python examples/temperature-scaling/oracle.py
```

MIT licensed. Paper texts and linked resources retain their own licenses.
