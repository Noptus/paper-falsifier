"""Validate reviewable numeric claims before candidate execution."""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from .arxiv import VERSION, arxiv_id, digest

HEX = re.compile(r"[0-9a-f]{64}")
MAX_BYTES = 1024 * 1024
MAX_CASES = 64
MAX_VALUES = 1024
MAX_TOLERANCE = 0.01
MODES = {"applicability", "matched-reproduction"}


class ProtocolError(ValueError):
    """The test protocol violates the published experiment contract."""


@dataclass(frozen=True)
class Case:
    name: str
    inputs: list[float]
    expected: list[float]
    atol: float
    rtol: float


@dataclass(frozen=True)
class Protocol:
    paper: dict
    claim: str
    mode: str
    scope: str
    references: list[dict]
    cases: list[Case]
    sha256: str


def _unique(items):
    mapping = {}
    for key, value in items:
        if key in mapping:
            raise ValueError(f"duplicate JSON key {key!r}")
        mapping[key] = value
    return mapping


def _record(value, keys, location):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ProtocolError(f"{location} requires fields {', '.join(sorted(keys))}")


def _text(value, location):
    if not isinstance(value, str) or not value.strip() or len(value) > 3000:
        raise ProtocolError(f"{location} must be non-empty text (at most 3000 characters)")
    return value


def _number(value, location):
    if type(value) not in (float, int):
        raise ProtocolError(f"{location} must be finite")
    try:
        val = float(value)
    except OverflowError as exc:
        raise ProtocolError(f"{location} must be finite") from exc
    if not math.isfinite(val):
        raise ProtocolError(f"{location} must be finite")
    return val


def _vector(value, location, minimum=1):
    if not isinstance(value, list) or not minimum <= len(value) <= MAX_VALUES:
        raise ProtocolError(f"{location} must have {minimum} to {MAX_VALUES} numbers")
    return [_number(element, location) for element in value]


def read_protocol(path: Path, expected_sha: str | None = None) -> Protocol:
    if expected_sha is not None and not HEX.fullmatch(expected_sha):
        raise ProtocolError("Expected a 64-character lowercase protocol SHA-256")
    with path.open("rb") as source:
        data = source.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ProtocolError(f"{path.name} exceeds {MAX_BYTES} bytes")
    actual_sha = hashlib.sha256(data).hexdigest()
    if expected_sha is not None and actual_sha != expected_sha:
        raise ProtocolError(f"Frozen protocol hash mismatch: {actual_sha}")
    try:
        body = json.loads(data, object_pairs_hook=_unique)
    except (ValueError, RecursionError) as exc:
        raise ProtocolError(f"Invalid protocol JSON: {exc}") from exc
    _record(body, {"schema", "paper", "mode", "claim", "scope", "references", "cases"}, "Protocol")
    if type(body["schema"]) is not int or body["schema"] != 1:
        raise ProtocolError("Only protocol schema 1 is supported")
    if not isinstance(body["mode"], str) or body["mode"] not in MODES:
        raise ProtocolError("Choose applicability or matched-reproduction protocol mode")
    paper = body["paper"]
    _record(paper, {"identifier", "title", "pdf_sha256"}, "Paper")
    identifier = paper["identifier"]
    if not isinstance(identifier, str) or arxiv_id(identifier) != identifier or not VERSION.search(identifier):
        raise ProtocolError("Paper requires a pinned arXiv version")
    _text(paper["title"], "Paper title")
    if not isinstance(paper["pdf_sha256"], str) or not HEX.fullmatch(paper["pdf_sha256"]):
        raise ProtocolError("Paper requires a lowercase PDF SHA-256")
    refs = body["references"]
    if not isinstance(refs, list) or not 1 <= len(refs) <= 20:
        raise ProtocolError("Provide one to 20 source references")
    for ref in refs:
        _record(ref, {"location", "source_claim"}, "Reference")
        _text(ref["location"], "Source location")
        _text(ref["source_claim"], "Source claim")
    raw_cases = body["cases"]
    if not isinstance(raw_cases, list) or not 1 <= len(raw_cases) <= MAX_CASES:
        raise ProtocolError(f"Provide one to {MAX_CASES} cases")
    cases = []
    names = set()
    for raw in raw_cases:
        _record(raw, {"name", "inputs", "expected", "atol", "rtol"}, "Case")
        name = _text(raw["name"], "Case name")
        if name in names:
            raise ProtocolError(f"Duplicate case name {name!r}")
        names.add(name)
        atol = _number(raw["atol"], "Absolute tolerance")
        rtol = _number(raw["rtol"], "Relative tolerance")
        if not (0 <= atol <= MAX_TOLERANCE and 0 <= rtol <= MAX_TOLERANCE):
            raise ProtocolError(f"Tolerances must lie in [0, {MAX_TOLERANCE}]")
        cases.append(Case(name, _vector(raw["inputs"], "Inputs", 0),
                          _vector(raw["expected"], "Expected values"), atol, rtol))
    return Protocol(paper, _text(body["claim"], "Claim"), body["mode"],
                    _text(body["scope"], "Scope"), refs, cases, actual_sha)


def verify_paper(protocol: Protocol, path: Path) -> None:
    if digest(path) != protocol.paper["pdf_sha256"]:
        raise ProtocolError("Local paper PDF does not match the protocol's pinned source")
