"""Command-line entry points for paper intake, protocol freezing, and execution."""

import argparse
import json
import math
import sys
from pathlib import Path

from .arxiv import SourceError, arxiv_id, fetch
from .protocol import ProtocolError, read_protocol, verify_paper
from .report import evaluate
from .runner import RunnerError
from .workspace import implementation_brief, init_study, seal_study, study_status, verify_study


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


def study(args) -> int:
    if args.stage == "new":
        directory = args.directory or Path("studies") / arxiv_id(args.reference).replace("/", "-")
        result = init_study(args.reference, directory)
    elif args.stage == "seal":
        result = seal_study(args.directory, tuple(args.artifact))
    elif args.stage == "brief":
        result = implementation_brief(args.directory, expected_seal=args.expect_seal)
    elif args.stage == "verify":
        result = verify_study(args.directory, timeout=args.timeout, expected_seal=args.expect_seal)
    else:
        result = study_status(args.directory)
    sys.stdout.write(_json(result))
    return {"supported_on_fixtures": 0, "falsified_on_fixtures": 1,
            "invalid_control": 2, "error": 2}.get(result.get("comparison"), 0)


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

    managed = commands.add_parser("study", help="Create, seal and verify a paper study")
    stages = managed.add_subparsers(dest="stage", required=True)
    new = stages.add_parser("new", help="Start an unsealed study from an arXiv link")
    new.add_argument("reference")
    new.add_argument("directory", nargs="?", type=Path, help="Default: studies/<arXiv identifier>")
    seal = stages.add_parser("seal", help="Freeze the protocol, methodology and any reference artifacts")
    seal.add_argument("directory", type=Path)
    seal.add_argument("--artifact", action="append", default=[], metavar="RELATIVE_PATH",
                      help="Additional independent reference, data or plan to hash, repeatable")
    brief = stages.add_parser("brief", help="Print a candidate handoff that excludes expected outputs")
    brief.add_argument("directory", type=Path)
    brief.add_argument("--expect-seal", help="Expected SHA-256 of externally anchored preregistration")
    verify = stages.add_parser("verify", help="Run the candidate and negative control together")
    verify.add_argument("directory", type=Path)
    verify.add_argument("--expect-seal", help="Expected SHA-256 of externally anchored preregistration")
    verify.add_argument("--timeout", type=float, default=15.0, help="Seconds per case, including compilation")
    status = stages.add_parser("status", help="Find the next action for an existing study")
    status.add_argument("directory", type=Path)
    managed.set_defaults(handler=study)
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
