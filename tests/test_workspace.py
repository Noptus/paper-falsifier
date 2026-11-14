"""Managed study lifecycle: init, seal, verify, status."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from paper_falsifier import workspace
from paper_falsifier.arxiv import Paper
from paper_falsifier.workspace import (
    StudyError,
    implementation_brief,
    init_study,
    seal_study,
    study_status,
    verify_study,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "temperature-scaling"

# A real PDF whose bytes we fully control so hashes line up deterministically.
PDF_BYTES = b"%PDF-1.4 synthetic study source\n"


def fake_paper(pdf: bytes = PDF_BYTES) -> Paper:
    import hashlib
    return Paper(
        identifier="1706.04599v2",
        title="On Calibration of Modern Neural Networks",
        authors=("Guo, Chuan", "Pleiss, Geoff"),
        abstract_url="https://arxiv.org/abs/1706.04599v2",
        pdf_url="https://arxiv.org/pdf/1706.04599v2",
        pdf_sha256=hashlib.sha256(pdf).hexdigest(),
        retrieved_at="2026-11-14T21:33:39Z",
    )


class TempStudy(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.root = Path(self._tmp) / "study"

    def init(self, pdf: bytes = PDF_BYTES) -> dict:
        with patch.object(workspace.arxiv, "fetch", return_value=(fake_paper(pdf), pdf)):
            return init_study("1706.04599", self.root)

    def complete_protocol(self) -> None:
        """Replace the draft with a valid, scoped schema-1 protocol."""
        data = json.loads((EXAMPLE / "protocol.json").read_text())
        (self.root / "protocol.json").write_text(json.dumps(data, indent=2))

    def write_methodology(self, text: str = "Oracle built independently via log-sum-exp.\n") -> None:
        (self.root / "methodology.md").write_text(text)

    def ready_to_seal(self) -> None:
        self.init()
        self.complete_protocol()
        self.write_methodology()


class InitTests(TempStudy):
    def test_init_lays_out_workspace_without_executing(self):
        with patch.object(workspace.report, "evaluate", side_effect=AssertionError("must not execute")):
            result = self.init()
        self.assertEqual(result["stage"], "drafting")
        self.assertTrue((self.root / "paper.pdf").is_file())
        self.assertTrue((self.root / "paper.json").is_file())
        self.assertTrue((self.root / "runs").is_dir())
        self.assertEqual((self.root / "paper.pdf").read_bytes(), PDF_BYTES)

    def test_draft_has_todo_and_valid_source_ids_but_no_hypothesis(self):
        self.init()
        draft = json.loads((self.root / "protocol.json").read_text())
        self.assertEqual(draft["schema"], 1)
        self.assertEqual(draft["paper"]["identifier"], "1706.04599v2")
        self.assertIn("TODO", draft["claim"])
        self.assertIn("TODO", draft["scope"])
        # Blank cases: inputs empty, template name, not yet runnable.
        self.assertEqual(draft["cases"][0]["inputs"], [])

    def test_readme_bakes_guest_paths(self):
        self.init()
        readme = (self.root / "README.md").read_text()
        self.assertIn("candidate.wat", readme)
        self.assertIn("negative-control.wat", readme)

    def test_init_refuses_existing_directory(self):
        self.root.mkdir(parents=True)
        with patch.object(workspace.arxiv, "fetch", return_value=(fake_paper(), PDF_BYTES)):
            with self.assertRaises(FileExistsError):
                init_study("1706.04599", self.root)


class SealTests(TempStudy):
    def test_seal_requires_completed_protocol(self):
        self.init()
        self.write_methodology()
        with self.assertRaisesRegex(StudyError, "protocol is not ready|TODO"):
            seal_study(self.root)

    def test_seal_rejects_todo_claim(self):
        self.init()
        # Valid cases but claim/scope still templated.
        data = json.loads((EXAMPLE / "protocol.json").read_text())
        data["claim"] = "TODO fill me"
        (self.root / "protocol.json").write_text(json.dumps(data))
        self.write_methodology()
        with self.assertRaisesRegex(StudyError, "TODO"):
            seal_study(self.root)

    def test_seal_requires_matching_local_pdf(self):
        self.init()
        self.complete_protocol()  # pins the real Guo PDF hash, not our synthetic one
        self.write_methodology()
        with self.assertRaisesRegex(StudyError, "match"):
            seal_study(self.root)

    def test_seal_refuses_when_guest_already_exists(self):
        self.ready_to_seal_with_matching_pdf()
        (self.root / "candidate.wat").write_text("(module)")
        with self.assertRaisesRegex(StudyError, "before writing any guest"):
            seal_study(self.root)

    def test_seal_writes_immutable_preregistration(self):
        self.ready_to_seal_with_matching_pdf()
        record = seal_study(self.root)
        self.assertEqual(record["stage"], "sealed")
        self.assertTrue((self.root / "preregistration.json").is_file())
        self.assertIn("methodology_sha256", record)
        with self.assertRaisesRegex(StudyError, "immutable"):
            seal_study(self.root)

    def test_registered_reference_is_content_bound(self):
        self.ready_to_seal_with_matching_pdf()
        ref = self.root / "reference.json"
        ref.write_text("[]")
        lock = seal_study(self.root, ("reference.json",))
        self.assertEqual(lock["artifacts"]["reference.json"], workspace.arxiv.digest(ref))
        ref.write_text("[1]")
        with self.assertRaisesRegex(StudyError, "Registered artifact changed"):
            implementation_brief(self.root)

    def test_external_paths_are_rejected(self):
        self.ready_to_seal_with_matching_pdf()
        with self.assertRaisesRegex(StudyError, "within the study"):
            seal_study(self.root, ("../outside.txt",))

    # --- helper that pins the protocol to our synthetic PDF so seal can pass ---
    def ready_to_seal_with_matching_pdf(self):
        self.init()
        data = json.loads((EXAMPLE / "protocol.json").read_text())
        data["paper"]["pdf_sha256"] = fake_paper().pdf_sha256
        (self.root / "protocol.json").write_text(json.dumps(data, indent=2))
        self.write_methodology()


class VerifyTests(TempStudy):
    def seal_ok(self):
        self.init()
        data = json.loads((EXAMPLE / "protocol.json").read_text())
        data["paper"]["pdf_sha256"] = fake_paper().pdf_sha256
        (self.root / "protocol.json").write_text(json.dumps(data, indent=2))
        self.write_methodology()
        seal_study(self.root)
        (self.root / "candidate.wat").write_text("(module candidate)")
        (self.root / "negative-control.wat").write_text("(module control)")

    def fake_report(self, status, candidate_sha, results):
        return {
            "status": status,
            "candidate_sha256": candidate_sha,
            "results": results,
        }

    def test_strong_control_yields_supported(self):
        self.seal_ok()
        reports = [
            self.fake_report("pass", "aaa", [{"name": "c", "status": "pass", "mismatched_indices": []}]),
            self.fake_report("fail", "bbb", [{"name": "c", "status": "fail", "mismatched_indices": [0]}]),
        ]
        with patch.object(workspace.report, "evaluate", side_effect=reports):
            receipt = verify_study(self.root, timeout=5)
        self.assertEqual(receipt["comparison"], "supported_on_fixtures")
        self.assertEqual(receipt["control_strength"], "strong")
        self.assertTrue(Path(receipt["receipt_path"]).is_file())

    def test_candidate_fail_with_strong_control_is_falsified(self):
        self.seal_ok()
        reports = [
            self.fake_report("fail", "aaa", [{"name": "c", "status": "fail", "mismatched_indices": [1]}]),
            self.fake_report("fail", "bbb", [{"name": "c", "status": "fail", "mismatched_indices": [0]}]),
        ]
        with patch.object(workspace.report, "evaluate", side_effect=reports):
            receipt = verify_study(self.root, timeout=5)
        self.assertEqual(receipt["comparison"], "falsified_on_fixtures")

    def test_control_error_is_not_evidence(self):
        self.seal_ok()
        reports = [
            self.fake_report("pass", "aaa", [{"name": "c", "status": "pass", "mismatched_indices": []}]),
            self.fake_report("error", "bbb", [{"name": "c", "status": "error", "error": "timeout: worker"}]),
        ]
        with patch.object(workspace.report, "evaluate", side_effect=reports):
            receipt = verify_study(self.root, timeout=5)
        self.assertEqual(receipt["comparison"], "error")

    def test_control_without_numeric_mismatch_is_invalid(self):
        self.seal_ok()
        reports = [
            self.fake_report("pass", "aaa", [{"name": "c", "status": "pass", "mismatched_indices": []}]),
            self.fake_report("fail", "bbb", [{"name": "c", "status": "fail", "mismatched_indices": []}]),
        ]
        with patch.object(workspace.report, "evaluate", side_effect=reports):
            receipt = verify_study(self.root, timeout=5)
        self.assertEqual(receipt["comparison"], "invalid_control")
        self.assertEqual(receipt["control_strength"], "no_numeric_mismatch")

    def test_identical_candidate_and_control_is_invalid(self):
        self.seal_ok()
        reports = [
            self.fake_report("pass", "same", [{"name": "c", "status": "pass", "mismatched_indices": []}]),
            self.fake_report("fail", "same", [{"name": "c", "status": "fail", "mismatched_indices": [0]}]),
        ]
        with patch.object(workspace.report, "evaluate", side_effect=reports):
            receipt = verify_study(self.root, timeout=5)
        self.assertEqual(receipt["comparison"], "invalid_control")
        self.assertEqual(receipt["control_strength"], "identical_to_candidate")

    def test_verify_fails_closed_on_protocol_tampering(self):
        self.seal_ok()
        # Mutate the sealed protocol after sealing.
        p = self.root / "protocol.json"
        p.write_text(p.read_text() + "\n")
        with patch.object(workspace.report, "evaluate", side_effect=AssertionError("must not execute")):
            with self.assertRaises(StudyError):
                verify_study(self.root, timeout=5)

    def test_verify_fails_closed_on_methodology_tampering(self):
        self.seal_ok()
        (self.root / "methodology.md").write_text("tampered\n")
        with patch.object(workspace.report, "evaluate", side_effect=AssertionError("must not execute")):
            with self.assertRaisesRegex(StudyError, "methodology"):
                verify_study(self.root, timeout=5)

    def test_run_receipt_is_deterministic_and_append_only(self):
        self.seal_ok()
        reports = [
            self.fake_report("pass", "aaa", [{"name": "c", "status": "pass", "mismatched_indices": []}]),
            self.fake_report("fail", "bbb", [{"name": "c", "status": "fail", "mismatched_indices": [0]}]),
        ]
        with patch.object(workspace.report, "evaluate", side_effect=reports):
            first = verify_study(self.root, timeout=5)
        # Same inputs -> same id, idempotent rerun with no rollback needed.
        with patch.object(workspace.report, "evaluate", side_effect=list(reports)):
            second = verify_study(self.root, timeout=5)
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(len(list((self.root / "runs").glob("*.json"))), 1)


    def test_brief_omits_vectors_and_authenticates_expected_lock(self):
        self.seal_ok()
        lock_path = self.root / "preregistration.json"
        lock = workspace.arxiv.digest(lock_path)
        brief = implementation_brief(self.root, expected_seal=lock)
        self.assertNotIn("expected", str(brief.keys()))
        self.assertNotIn("cases", brief)
        self.assertEqual(brief["seal_sha256"], lock)
        with self.assertRaisesRegex(StudyError, "external digest"):
            implementation_brief(self.root, expected_seal="a" * 64)

    def test_paper_metadata_tampering_is_rejected_without_running_guests(self):
        self.seal_ok()
        metadata = self.root / "paper.json"
        metadata.write_text(metadata.read_text() + "\n")
        with patch.object(workspace.report, "evaluate", side_effect=AssertionError("must not execute")):
            with self.assertRaisesRegex(StudyError, "changed since sealing"):
                verify_study(self.root, timeout=5)

    def test_control_wrong_shape_is_invalid_even_if_numbers_differ(self):
        self.seal_ok()
        reports = [
            self.fake_report("pass", "aaa", []),
            self.fake_report("fail", "bbb", [{"status": "fail", "mismatched_indices": [0],
                                               "actual_count": 1, "expected_count": 2}]),
        ]
        with patch.object(workspace.report, "evaluate", side_effect=reports):
            result = verify_study(self.root, timeout=5)
        self.assertEqual(result["comparison"], "invalid_control")
        self.assertEqual(result["control_strength"], "invalid_output_shape")

    def test_guest_extensions_work_and_ambiguous_variants_are_rejected(self):
        self.seal_ok()
        (self.root / "candidate.wat").rename(self.root / "candidate.wasm")
        reports = [self.fake_report("pass", "aaa", []),
                   self.fake_report("fail", "bbb", [{"status": "fail", "mismatched_indices": [0]}])]
        with patch.object(workspace.report, "evaluate", side_effect=reports) as evaluator:
            verify_study(self.root, timeout=5)
        self.assertEqual(evaluator.call_args_list[0].args[1].suffix, ".wasm")
        (self.root / "candidate.wat").write_text("(module)")
        with patch.object(workspace.report, "evaluate", side_effect=AssertionError("must not execute")):
            with self.assertRaisesRegex(StudyError, "exactly one format"):
                verify_study(self.root, timeout=5)

    def test_timeout_receipt_does_not_block_later_successful_replay(self):
        self.seal_ok()
        good = self.fake_report("pass", "aaa", [])
        control = self.fake_report("fail", "bbb", [{"status": "fail", "mismatched_indices": [0]}])
        error = self.fake_report("error", "aaa", [{"status": "error", "error": "timeout"}])
        with patch.object(workspace.report, "evaluate", side_effect=[error, control, good, control]):
            first = verify_study(self.root, timeout=5)
            second = verify_study(self.root, timeout=5)
        self.assertEqual(first["comparison"], "error")
        self.assertEqual(second["comparison"], "supported_on_fixtures")
        self.assertNotEqual(first["run_id"], second["run_id"])



class StatusTests(TempStudy):
    def test_absent(self):
        self.assertEqual(study_status(self.root)["stage"], "absent")

    def test_drafting_then_seal_hints(self):
        self.init()
        status = study_status(self.root)
        self.assertEqual(status["stage"], "drafting")
        self.assertIn("protocol", status["next"].lower())

    def test_implementing_hint_after_seal(self):
        self.init()
        data = json.loads((EXAMPLE / "protocol.json").read_text())
        data["paper"]["pdf_sha256"] = fake_paper().pdf_sha256
        (self.root / "protocol.json").write_text(json.dumps(data, indent=2))
        self.write_methodology()
        seal_study(self.root)
        status = study_status(self.root)
        self.assertEqual(status["stage"], "implementing")
        self.assertIn("candidate.wat", status["next"])


class RealRoundtripTests(TempStudy):
    """One genuine WAT execution through the full sealed lifecycle."""

    def test_real_candidate_and_control_roundtrip(self):
        try:
            import wasmtime  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("wasmtime not installed")

        self.init(pdf=PDF_BYTES)
        data = json.loads((EXAMPLE / "protocol.json").read_text())
        data["paper"]["pdf_sha256"] = fake_paper().pdf_sha256
        (self.root / "protocol.json").write_text(json.dumps(data, indent=2))
        self.write_methodology()
        seal_study(self.root)

        shutil.copyfile(EXAMPLE / "candidate.wat", self.root / "candidate.wat")
        shutil.copyfile(EXAMPLE / "negative-control.wat", self.root / "negative-control.wat")

        receipt = verify_study(self.root, timeout=15)
        self.assertEqual(receipt["comparison"], "supported_on_fixtures")
        self.assertEqual(receipt["control_strength"], "strong")
        self.assertEqual(receipt["candidate"]["status"], "pass")
        self.assertEqual(receipt["control"]["status"], "fail")


if __name__ == "__main__":
    unittest.main()
