"""Public-facing intake, protocol, and report behavior."""

import hashlib
import json
import tempfile
from contextlib import redirect_stdout
from io import StringIO
import unittest
from pathlib import Path
from unittest.mock import patch

from paper_falsifier.arxiv import SourceError, arxiv_id, fetch
from paper_falsifier.cli import main
from paper_falsifier.protocol import ProtocolError, read_protocol, verify_paper
from paper_falsifier.report import evaluate
from paper_falsifier.runner import RunnerError

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "temperature-scaling"


class ArxivTests(unittest.TestCase):
    def test_ids(self):
        self.assertEqual(arxiv_id("arXiv:2107.07511"), "2107.07511")
        self.assertEqual(arxiv_id("https://arxiv.org/pdf/2107.07511v6.pdf"), "2107.07511v6")
        self.assertEqual(arxiv_id("https://arxiv.org/abs/math/0101001v1"), "math/0101001v1")
        for link in ("http://arxiv.org/abs/2107.07511", "https://arxiv.org.evil/abs/2107.07511",
                     "https://arxiv.org@evil/abs/2107.07511", "https://arxiv.org/abs/../foo"):
            with self.subTest(link=link), self.assertRaises(SourceError):
                arxiv_id(link)

    def test_resolve_pin_and_hash(self):
        html = b'<meta property="og:url" content="https://arxiv.org/abs/2107.07511v6">'
        html += b'<meta name="citation_title" content="A Paper">'
        html += b'<meta name="citation_author" content="An Author">'
        pdf = b"%PDF-fake"
        with patch("paper_falsifier.arxiv._download", side_effect=[html, pdf]) as transport:
            paper, bytes_read = fetch("2107.07511")
        self.assertEqual(transport.call_args.args[0], "https://arxiv.org/pdf/2107.07511v6")
        self.assertEqual(paper.pdf_sha256, hashlib.sha256(pdf).hexdigest())
        self.assertEqual(paper.authors, ("An Author",))
        self.assertEqual(bytes_read, pdf)


class ApplicabilityTests(unittest.TestCase):
    def test_frozen_protocol_and_negative_control(self):
        expected_hash = (EXAMPLE / "protocol.sha256").read_text().strip()
        protocol = read_protocol(EXAMPLE / "protocol.json", expected_hash)
        passing = evaluate(protocol, EXAMPLE / "candidate.wat", timeout=8)
        failing = evaluate(protocol, EXAMPLE / "negative-control.wat", timeout=8)
        self.assertEqual(passing["status"], "pass")
        self.assertEqual(failing["status"], "fail")
        self.assertEqual(passing["case_count"], len(passing["results"]))
        self.assertTrue(all(case["mismatched_indices"] for case in failing["results"]))
        for evidence, candidate in [(passing, "candidate.wat"), (failing, "negative-control.wat")]:
            self.assertEqual(evidence["protocol_sha256"], expected_hash)
            self.assertEqual(evidence["candidate_sha256"],
                             hashlib.sha256((EXAMPLE / candidate).read_bytes()).hexdigest())

    def test_runner_failure_is_distinct_from_falsification(self):
        protocol = EXAMPLE / "protocol.json"
        protocol_hash = (EXAMPLE / "protocol.sha256").read_text().strip()
        args = ["run", str(protocol), str(EXAMPLE / "negative-control.wat"),
                "--expect-sha", protocol_hash]
        with patch("paper_falsifier.report.execute_candidate", side_effect=RunnerError("timeout: worker")):
            with redirect_stdout(StringIO()) as output:
                self.assertEqual(main(args), 2)
        evidence = json.loads(output.getvalue())
        self.assertEqual(evidence["status"], "error")
        self.assertEqual(evidence["results"][0]["status"], "error")

    def test_protocol_lock_and_bad_source(self):
        protocol = read_protocol(EXAMPLE / "protocol.json")
        with self.assertRaisesRegex(ProtocolError, "Frozen protocol hash mismatch"):
            read_protocol(EXAMPLE / "protocol.json", "a" * 64)
        with tempfile.TemporaryDirectory() as work:
            wrong_pdf = Path(work) / "paper.pdf"
            wrong_pdf.write_bytes(b"%PDF-wrong")
            with self.assertRaisesRegex(ProtocolError, "does not match"):
                verify_paper(protocol, wrong_pdf)

    def test_reject_nonfinite_values_and_duplicate_keys(self):
        data = json.loads((EXAMPLE / "protocol.json").read_text())
        data["cases"][0]["inputs"] = [float("inf")]
        with tempfile.TemporaryDirectory() as work:
            p = Path(work) / "protocol.json"
            p.write_text(json.dumps(data))
            with self.assertRaisesRegex(ProtocolError, "must be finite"):
                read_protocol(p)
            p.write_text('{"schema":1,"schema":1}')
            with self.assertRaisesRegex(ProtocolError, "duplicate"):
                read_protocol(p)
