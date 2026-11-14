"""Command-line entry points for paper intake, protocol freezing, and execution."""

import argparse
import json
import math
import sys
from pathlib import Path

from .arxiv import SourceError, fetch
from .protocol import ProtocolError, read_protocol, verify_paper
from .report import evaluate
from .runner import RunnerError


def _json(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2) + "\n"


def intake(args) -> int:
    paper, content = fetch(args.reference)
    folder = args.out
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "paper.pdf").write_bytes(content)
    (folder / "paper.json").write_text(_json(paper.record()), encoding="utf-8")
    print(f"{paper.identifier} {paper.pdf_sha256}  {paper.title}")
    return 0


def freeze(args) -> int:
    protocol = read_protocol(args.protocol)
    if args.paper:
        verify_paper(protocol, args.paper)
    print(protocol.sha256)
    return 0


def run(args) -> int:
    if not math.isfinite(args.timeout) or not 0.1 <= args.timeout <= 30:
        raise ValueError("Per-case timeout must be between 0.1 and 30 seconds")
    protocol = read_protocol(args.protocol, args.expect_sha)
    if args.paper:
        verify_paper(protocol, args.paper)
    report = evaluate(protocol, args.candidate, timeout=args.timeout)
    report["source_pdf_verified"] = args.paper is not None
    rendered = _json(report)
    if args.report:
        with args.report.open("x", encoding="utf-8") as target:
            target.write(rendered)
    sys.stdout.write(rendered)
    return {"pass": 0, "fail": 1, "error": 2}[report["status"]]


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="paper-falsifier",
        description="Test a scoped numerical research claim against independent acceptance vectors.",
    )
    commands = root.add_subparsers(dest="command", required=True)
    source = commands.add_parser("intake", help="Download and hash a versioned arXiv PDF")
    source.add_argument("reference")
    source.add_argument("--out", type=Path, required=True, help="New output directory")
    source.set_defaults(handler=intake)

    lock = commands.add_parser("freeze", help="Validate and hash a protocol")
    lock.add_argument("protocol", type=Path)
    lock.add_argument("--paper", type=Path, help="Verify the locally retrieved PDF")
    lock.set_defaults(handler=freeze)

    test = commands.add_parser("run", help="Evaluate a numerical guest against a reviewed protocol")
    test.add_argument("protocol", type=Path)
    test.add_argument("candidate", type=Path, help="WebAssembly text (.wat) or bytes (.wasm)")
    test.add_argument("--expect-sha", required=True, help="SHA-256 produced by freeze")
    test.add_argument("--paper", type=Path, help="Verify the locally retrieved PDF")
    test.add_argument("--report", type=Path, help="Write JSON to a new report path")
    test.add_argument("--timeout", type=float, default=15.0, help="Seconds per case, including compilation (default: 15)")
    test.set_defaults(handler=run)
    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.handler(args)
    except (SourceError, ProtocolError, RunnerError, OSError, ValueError) as exc:
        print(f"paper-falsifier: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
