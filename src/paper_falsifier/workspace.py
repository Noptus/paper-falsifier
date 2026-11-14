"""Create, seal and verify a managed numeric paper study."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import platform
import tempfile

from . import arxiv, report
from .protocol import HEX, Protocol, ProtocolError, read_protocol


PAPER_PDF = "paper.pdf"
PAPER_JSON = "paper.json"
PROTOCOL = "protocol.json"
METHODOLOGY = "methodology.md"
PREREGISTRATION = "preregistration.json"
CANDIDATE = "candidate.wat"
CONTROL = "negative-control.wat"
GUESTS = (CANDIDATE, CONTROL)
RUNS = "runs"
NOTE_LIMIT = 256 * 1024
RECEIPT_LIMIT = 8 * 1024 * 1024


class StudyError(ValueError):
    """A study is missing required evidence or fails an integrity check."""


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise StudyError(f"Expected a regular study file: {path.name}")
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise StudyError(f"{path.name} exceeds {limit} bytes")
    return data


def _new_file(path: Path, data: bytes) -> None:
    """Publish fully written bytes only if the final pathname is unused."""
    with tempfile.NamedTemporaryFile(dir=path.parent) as temp:
        temp.write(data)
        temp.flush()
        os.fsync(temp.fileno())
        os.link(temp.name, path)


def _read_protocol(root: Path, expected: str | None = None) -> Protocol:
    path = root / PROTOCOL
    if path.is_symlink() or not path.is_file():
        raise StudyError(f"Expected a regular study file: {PROTOCOL}")
    try:
        return read_protocol(path, expected)
    except ProtocolError as exc:
        raise StudyError(f"protocol is not ready: {exc}") from exc


def init_study(reference: str, root: Path) -> dict:
    """Fetch a pinned paper and create an unsealed local study draft."""
    root = Path(root)
    if root.exists() or root.is_symlink():
        raise FileExistsError(f"Study directory already exists: {root}")
    paper, pdf = arxiv.fetch(reference)
    paper_record = paper.record()
    root.mkdir(parents=True, exist_ok=False)
    draft = {
        "schema": 1,
        "paper": {key: paper_record[key] for key in ("identifier", "title", "pdf_sha256")},
        "mode": "applicability",
        "claim": "TODO: one testable numeric statement",
        "scope": "TODO: population, assumptions and boundaries",
        "references": [{"location": "TODO: section, equation or PDF page", "source_claim": "TODO: precise claim"}],
        "cases": [{"name": "TODO-case", "inputs": [], "expected": [0], "atol": 0, "rtol": 0}],
    }
    guidance = f"""# Study of {paper.identifier}

{paper.title}

1. Write the claim, scope, citation locations and acceptance vectors in `{PROTOCOL}`.
2. Document the independent reference, assumptions, controls and decision rule in `{METHODOLOGY}`.
3. Run `paper-falsifier study seal <this-directory>` before writing the guests.
4. Print the implementation-only handoff with `paper-falsifier study brief <this-directory>`.
5. Add `{CANDIDATE}` and `{CONTROL}` (or their `.wasm` counterparts), then run `paper-falsifier study verify <this-directory>`.

For externally verifiable preregistration, anchor `{PREREGISTRATION}` to an independent timestamp before implementation. A local date is an audit aid. The tool will not execute the PDF or any code linked by it.
"""
    _new_file(root / PAPER_PDF, pdf)
    _new_file(root / PAPER_JSON, _json_bytes(paper_record))
    _new_file(root / PROTOCOL, _json_bytes(draft))
    _new_file(root / METHODOLOGY, b"# Methodology\n\nTODO: Estimand, independent oracle, controlled comparison, assumptions and limitations.\n")
    _new_file(root / "README.md", guidance.encode())
    (root / RUNS).mkdir()
    return {"stage": "drafting", "root": str(root), "paper": paper_record,
            "next": f"complete {PROTOCOL} and {METHODOLOGY}"}


def _evidence(root: Path, protocol: Protocol) -> dict:
    source = _read(root / PAPER_JSON, arxiv.MAX_ABSTRACT)
    fields = json.loads(source)
    if not isinstance(fields, dict) or not all(fields.get(key) == value for key, value in protocol.paper.items()):
        raise StudyError("Protocol paper identity does not match the local metadata")
    if _digest(_read(root / PAPER_PDF, arxiv.MAX_PDF)) != protocol.paper["pdf_sha256"]:
        raise StudyError("Local PDF does not match the pinned source")
    note = _read(root / METHODOLOGY, NOTE_LIMIT).decode("utf-8")
    if not note.strip() or "TODO" in note:
        raise StudyError("Complete the methodology note before sealing")
    return {"paper_metadata_sha256": _digest(source), "methodology_sha256": _digest(note.encode("utf-8")),
            "protocol_sha256": protocol.sha256, "paper": protocol.paper}


def _guest_files(root: Path, name: str) -> list[Path]:
    wat = root / name
    return [path for path in (wat, wat.with_suffix(".wasm")) if path.exists() or path.is_symlink()]


def _guest(root: Path, name: str) -> Path:
    paths = _guest_files(root, name)
    if len(paths) != 1:
        raise StudyError(f"Provide exactly one format for {Path(name).stem}: .wat or .wasm")
    return paths[0]


def _artifact(root: Path, name: str) -> str:
    if not isinstance(name, str):
        raise StudyError("Expected a relative artifact pathname")
    path = Path(name)
    reserved = {PAPER_PDF, PAPER_JSON, PROTOCOL, METHODOLOGY, PREREGISTRATION, RUNS,
                *GUESTS, *(Path(name).with_suffix(".wasm").name for name in GUESTS)}
    if path.is_absolute() or not path.parts or ".." in path.parts or path.parts[0] in reserved:
        raise StudyError(f"Expected a separate path within the study: {name}")
    full = root / path
    if not full.resolve().is_relative_to(root.resolve()):
        raise StudyError(f"Artifact escapes the study directory: {name}")
    return _digest(_read(full, arxiv.MAX_PDF))


def seal_study(root: Path, artifacts: tuple[str, ...] = ()) -> dict:
    """Write an exclusive local lock before either guest occupies its reserved path."""
    root = Path(root)
    protocol = _read_protocol(root)
    text = [protocol.claim, protocol.scope, *(c.name for c in protocol.cases),
            *(value for item in protocol.references for value in item.values())]
    if any("TODO" in item for item in text):
        raise StudyError("Protocol still contains TODO placeholders")
    for name in GUESTS:
        if paths := _guest_files(root, name):
            raise StudyError(f"Found {paths[0].name}. Seal the protocol before writing any guest")
    if len(artifacts) > 12 or len(artifacts) != len(set(artifacts)):
        raise StudyError("Provide at most 12 distinct study artifacts")
    record = {
        "schema": 1,
        "stage": "sealed",
        "registered_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **_evidence(root, protocol),
        "claim": protocol.claim,
        "scope": protocol.scope,
        "mode": protocol.mode,
        "artifacts": {name: _artifact(root, name) for name in sorted(artifacts)},
    }
    raw = _json_bytes(record)
    try:
        _new_file(root / PREREGISTRATION, raw)
    except FileExistsError as exc:
        raise StudyError("Study is already sealed and the lock is immutable through this workflow; use a new directory for a protocol revision") from exc
    return {**record, "seal_sha256": _digest(raw)}


def _check_seal(root: Path, expected: str | None) -> tuple[dict, Protocol, str]:
    raw = _read(root / PREREGISTRATION, NOTE_LIMIT)
    sha = _digest(raw)
    if expected is not None and (not HEX.fullmatch(expected) or expected != sha):
        raise StudyError("Preregistration SHA-256 does not match the expected external digest")
    record = json.loads(raw)
    if not isinstance(record, dict) or type(record.get("schema")) is not int or record["schema"] != 1:
        raise StudyError("Unsupported preregistration schema")
    if not isinstance(record.get("protocol_sha256"), str) or not HEX.fullmatch(record["protocol_sha256"]):
        raise StudyError("Missing sealed protocol digest")
    protocol = _read_protocol(root, record["protocol_sha256"])
    expected_metadata = {"claim": protocol.claim, "scope": protocol.scope, "mode": protocol.mode,
                         **_evidence(root, protocol)}
    if any(record.get(key) != value for key, value in expected_metadata.items()):
        raise StudyError("Preregistration metadata or methodology changed since sealing")
    artifacts = record.get("artifacts")
    if not isinstance(artifacts, dict) or len(artifacts) > 12:
        raise StudyError("Invalid sealed artifact manifest")
    for name, expected_sha in artifacts.items():
        if _artifact(root, name) != expected_sha:
            raise StudyError(f"Registered artifact changed: {name}")
    return record, protocol, sha


def implementation_brief(root: Path, expected_seal: str | None = None) -> dict:
    """Return data a separate implementer needs without acceptance vectors."""
    sealed, protocol, sha = _check_seal(Path(root), expected_seal)
    return {
        "paper": protocol.paper,
        "mode": protocol.mode,
        "claim": protocol.claim,
        "scope": protocol.scope,
        "references": protocol.references,
        "seal_sha256": sha,
        "protocol_sha256": sealed["protocol_sha256"],
        "candidate": CANDIDATE,
        "control": CONTROL,
        "abi": "Import-free Wasm: memory (one 64 KiB page) and solve(i32,i32,i32,i32)->i32. Contiguous little-endian f64 inputs and outputs. Parameters are input byte offset, input count, output byte offset and output capacity. Return output count.",
        "implementation_note": "Supply the researcher-defined input/output layout separately. Keep expected vectors and oracle out of the candidate's context.",
    }


def _control_strength(candidate: dict, control: dict) -> tuple[str, str]:
    if "error" in (candidate["status"], control["status"]):
        return "error", "unusable"
    if candidate["candidate_sha256"] == control["candidate_sha256"]:
        return "invalid_control", "identical_to_candidate"
    cases = control["results"]
    if any(case.get("actual_count") != case.get("expected_count") for case in cases):
        return "invalid_control", "invalid_output_shape"
    if not any(case["status"] == "fail" and case.get("mismatched_indices") for case in cases):
        return "invalid_control", "no_numeric_mismatch"
    comparison = "supported_on_fixtures" if candidate["status"] == "pass" else "falsified_on_fixtures"
    return comparison, "strong"


def verify_study(root: Path, *, timeout: float, expected_seal: str | None = None) -> dict:
    """Run the independently specified candidate and control, then append a receipt."""
    if not math.isfinite(timeout) or not 0.1 <= timeout <= 30:
        raise StudyError("Per-case timeout must be between 0.1 and 30 seconds")
    root = Path(root)
    _, protocol, seal_sha = _check_seal(root, expected_seal)
    candidate_path, control_path = (_guest(root, name) for name in GUESTS)
    candidate = report.evaluate(protocol, candidate_path, timeout=timeout)
    control = report.evaluate(protocol, control_path, timeout=timeout)
    comparison, strength = _control_strength(candidate, control)
    receipt = {
        "schema": 1,
        "comparison": comparison,
        "control_strength": strength,
        "timeout": float(timeout),
        "seal_sha256": seal_sha,
        "protocol_sha256": protocol.sha256,
        "runtime": {"python": platform.python_version(), "platform": platform.system(),
                    "machine": platform.machine(), "wasmtime": version("wasmtime")},
        "candidate": candidate,
        "control": control,
    }
    # Include outputs in the ID so a later successful retry can coexist with an earlier timeout.
    receipt["run_id"] = _digest(_json_bytes(receipt))
    runs = root / RUNS
    runs.mkdir(exist_ok=True)
    if runs.is_symlink():
        raise StudyError("Run receipts must be stored in this study")
    target = runs / f"{receipt['run_id']}.json"
    raw = _json_bytes(receipt)
    try:
        _new_file(target, raw)
    except FileExistsError as exc:
        if _read(target, RECEIPT_LIMIT) != raw:
            raise StudyError("Existing run receipt differs from the reproduced result") from exc
    return {**receipt, "receipt_path": str(target)}


def study_status(root: Path) -> dict:
    """Explain the next action and list any append-only receipts."""
    root = Path(root)
    if not root.is_dir():
        return {"stage": "absent", "root": str(root), "next": "run paper-falsifier study new"}
    runs = sorted(file.name for file in (root / RUNS).glob("*.json"))
    if not all((root / name).is_file() for name in (PAPER_PDF, PAPER_JSON, PROTOCOL, METHODOLOGY)):
        return {"stage": "incomplete", "root": str(root), "runs": runs, "next": "restore missing source or draft files"}
    if not (root / PREREGISTRATION).is_file():
        return {"stage": "drafting", "root": str(root), "runs": runs,
                "next": f"complete {PROTOCOL} and {METHODOLOGY}, then run paper-falsifier study seal"}
    missing = [file for file in GUESTS if not _guest_files(root, file)]
    if missing:
        return {"stage": "implementing", "root": str(root), "runs": runs,
                "next": f"generate the candidate brief and write {' and '.join(missing)}"}
    return {"stage": "verifiable", "root": str(root), "runs": runs,
            "next": "run paper-falsifier study verify" if not runs else "inspect an existing run or reproduce it"}
