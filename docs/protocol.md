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
