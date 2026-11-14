"""Evaluate a frozen protocol and create a machine-readable evidence record."""

import hashlib
import math
from pathlib import Path

from .protocol import Protocol
from .runner import RunnerError, execute_candidate, read_candidate


def evaluate(protocol: Protocol, candidate: Path, *, timeout: float) -> dict:
    snapshot = read_candidate(candidate)
    candidate_hash = hashlib.sha256(snapshot.data).hexdigest()
    records = []
    for case in protocol.cases:
        try:
            actual = execute_candidate(snapshot, case.inputs, len(case.expected), timeout=timeout)
        except RunnerError as exc:
            records.append({"name": case.name, "status": "error", "error": str(exc)})
            break
        mismatches = [
            index
            for index, (value, expected) in enumerate(zip(actual, case.expected))
            if not math.isclose(value, expected, abs_tol=case.atol, rel_tol=case.rtol)
        ]
        complete = len(actual) == len(case.expected)
        records.append({
            "name": case.name,
            "status": "pass" if complete and not mismatches else "fail",
            "expected_count": len(case.expected),
            "actual_count": len(actual),
            "mismatched_indices": mismatches,
            "actual": actual,
        })
    return {
        "schema": 1,
        "status": _status(records),
        "mode": protocol.mode,
        "paper": protocol.paper,
        "claim": protocol.claim,
        "scope": protocol.scope,
        "references": protocol.references,
        "protocol_sha256": protocol.sha256,
        "candidate_sha256": candidate_hash,
        "case_count": len(protocol.cases),
        "results": records,
    }



def _status(records: list[dict]) -> str:
    if any(record["status"] == "error" for record in records):
        return "error"
    if all(record["status"] == "pass" for record in records):
        return "pass"
    return "fail"
