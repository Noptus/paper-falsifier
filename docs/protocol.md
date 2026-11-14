# Protocol and guest ABI

## Protocol

A UTF-8 JSON object with these exact keys.

| Key | Meaning |
| --- | --- |
| schema | Integer 1 |
| paper | Object with identifier (explicit arXiv version), title, and pdf_sha256 (lowercase SHA-256 of the downloaded PDF) |
| mode | applicability or matched-reproduction |
| claim | One testable statement |
| scope | Population, assumptions and material gaps |
| references | Objects with location and source_claim |
| cases | Objects with unique name, numeric inputs, numeric expected, atol, and rtol |

The protocol is limited to 1 MiB, up to 64 cases and 1,024 entries per vector. Expected vectors have at least one entry. All numbers are finite. Both tolerances must fall in [0, 0.01]. Output length must match exactly and each value must be close under Python’s math.isclose. Set domain-specific acceptance thresholds and derive answers independently.

A candidate sees one case's inputs and output capacity. It receives no expected vectors, case names, source paper, other cases, protocol bytes or report. For serious evaluation, freeze and withhold some cases until the candidate is locked. The shipped vectors are public for transparency.

## Guest

- WebAssembly core module in text form (.wat) or binary form (.wasm), up to 128 KiB.
- Zero imports. Declare one page of memory, for example (memory (export "memory") 1 1).
- Export exactly memory and solve. Its signature is (i32, i32, i32, i32) -> i32.
- The arguments are input byte offset (zero), input element count, output byte offset (immediately after the input), and maximum output count. Elements are little-endian IEEE 754 binary64.
- Write results to the output region and return the number written. A negative count, count beyond capacity, trap, nonfinite value or timeout fails the run. Each case starts with fresh memory.

See the [temperature-scaling guest](../examples/temperature-scaling/candidate.wat).

## Report and exit codes

The CLI emits JSON with the paper identity, declared mode, references, candidate and protocol hashes, source PDF verification status and each completed case. An execution error stops the loop. A numeric mismatch continues so the report shows all tested cases. Mismatch indices are zero-based.

- 0 means all expected outputs passed.
- 1 means a numeric acceptance failure.
- 2 means an invalid input, unreadable file, guest/worker error, timeout or another CLI error. A failed negative control is informative only when it reports numeric mismatches (exit 1).

The protocol hash covers file bytes, so whitespace edits change it. When publishing, pair the report with its independently recorded hash and use --paper to verify the source bytes.

On busy machines, use `--timeout` to raise the per-case wall clock cap up to 30 seconds. The default is 15 seconds, including worker startup and compilation.

## Managed study

A managed study keeps the protocol, methodology, source PDF, guests and receipts in one directory and moves them through four stages.

| Stage | Command | Effect |
| --- | --- | --- |
| new | `study new <link> <dir>` | Fetch and pin the paper and write draft `protocol.json` and `methodology.md` |
| seal | `study seal <dir>` | Hash the protocol, methodology and any artifacts into an immutable `preregistration.json` |
| brief | `study brief <dir>` | Emit the author handoff with claim, scope, citations and ABI and without expected vectors |
| verify | `study verify <dir>` | Run `candidate.wat` and `negative-control.wat` and append a run receipt |

`study status <dir>` reports the current stage and the next action.

Seal refuses to proceed while `protocol.json` holds a TODO placeholder, while the methodology note is empty or still has a TODO, or while either guest already occupies its reserved path. Add `--artifact <relative-path>`, repeatable up to twelve distinct entries, to bind an independent reference, dataset or plan by SHA-256. Each artifact path stays inside the study and avoids the reserved names. The seal is written once and is immutable through this workflow, so a protocol revision uses a fresh directory.

The seal digest proves that the on-disk protocol, methodology and artifacts match what was sealed. An externally timestamped preregistration proves that the plan predated the result. To gain the second property, anchor the seal digest to an independent timestamp before implementation. Pass `--expect-seal <digest>` to `brief` and `verify` to bind the action to that anchored digest.

The brief withholds every expected vector and the oracle so the author stays blind to the outcome. Keep candidate authoring in a separate context. A fix or refit invalidates the sealed holdout, so open a fresh study on a new unseen holdout, disclose the change independently and reseal.

Verify compares the candidate against the control and grades the control strength. A control is useful only when it differs from the candidate, returns the expected output shape and fails through a genuine numeric mismatch. Receipts are append-only and named by a content digest, so a reproduced run with identical outputs coexists with an earlier one and a differing reproduction is rejected.

- `supported_on_fixtures` (exit 0) means the candidate passed and the control failed meaningfully.
- `falsified_on_fixtures` (exit 1) means the candidate failed against the frozen vectors.
- `invalid_control` or `error` (exit 2) means the control was too weak or a run could not complete.

A supported study is evidence for the stated population and assumptions on its fixtures. A simulation study supports the registered construction and is never a proof of a general theorem.

A managed study accepts one guest for each of `candidate` and `negative-control`, each with a `.wat` or `.wasm` suffix. Use an existing compiler for trusted local numeric code, then verify the resulting module against the same zero-import ABI.
