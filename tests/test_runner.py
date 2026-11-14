"""Run with: python -I -m unittest discover -s tests -v

Only the integration/Wasmtime tests require wasmtime-py >=39,<40.  Tests of
validation, subprocess isolation, deadlines, and transport work without it.
"""

from __future__ import annotations

import base64
import io
import json
import math
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

# This also works in a fresh src-layout checkout without packaging metadata.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from paper_falsifier import runner
from paper_falsifier.runner import RunnerError, execute

try:
    import wasmtime
except ModuleNotFoundError as exc:
    if exc.name != "wasmtime":
        raise
    wasmtime = None


_SIGNATURE = "(param $in i32) (param $n i32) (param $out i32) (param $cap i32) (result i32)"


def module(body="i32.const 0", *, memory="1 1", extra="", signature=_SIGNATURE):
    return f'''(module
      (memory (export "memory") {memory})
      {extra}
      (func (export "solve") {signature} {body}))'''


_COPY = """
    (local $i i32)
    (block $done
      (loop $again
        local.get $i local.get $n i32.ge_u br_if $done
        local.get $out local.get $i i32.const 8 i32.mul i32.add
        local.get $in local.get $i i32.const 8 i32.mul i32.add f64.load
        f64.store
        local.get $i i32.const 1 i32.add local.set $i
        br $again))
    local.get $n
"""


class FileCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="paper-falsifier-candidates-")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.serial = 0

    def candidate(self, text=None, *, suffix=".wat", data=None):
        self.serial += 1
        path = self.directory / f"candidate {self.serial}{suffix}"
        if data is None:
            data = (module() if text is None else text).encode("utf-8")
        path.write_bytes(data)
        return path


class ValidationTests(FileCase):
    def test_plain_finite_inputs_only(self):
        class CoercionBomb:
            def __float__(self):
                raise AssertionError("must not coerce custom numbers")

        class ListBomb(list):
            def __iter__(self):
                raise AssertionError("must not iterate list subclasses")

        invalid = [
            (), iter([1.0]), "1", None, ListBomb([1.0]),
            [True], [False], ["1"], [None], [CoercionBomb()],
            [float("nan")], [float("inf")], [-float("inf")], [1 << 100_000],
            [0.0] * (runner._MAX_VALUES + 1),
        ]
        path = self.candidate()
        with mock.patch.object(runner, "_run_worker") as worker:
            for inputs in invalid:
                with self.subTest(input_type=type(inputs).__name__, size=len(inputs) if type(inputs) is list else None):
                    with self.assertRaisesRegex(RunnerError, "^validation:"):
                        execute(path, inputs, 0)
            worker.assert_not_called()

    def test_capacity_validation(self):
        path = self.candidate()
        with mock.patch.object(runner, "_run_worker") as worker:
            for capacity in (-1, runner._MAX_VALUES + 1, True, False, 1.0, "1", None, 1 << 100_000):
                with self.subTest(capacity_type=type(capacity).__name__):
                    with self.assertRaisesRegex(RunnerError, "^validation:"):
                        execute(path, [], capacity)
            worker.assert_not_called()

    def test_combined_buffers_must_fit(self):
        with mock.patch.object(runner, "_run_worker") as worker:
            with self.assertRaisesRegex(RunnerError, "^memory:"):
                execute(self.candidate(), [0.0] * runner._MAX_VALUES, 1)
            worker.assert_not_called()

    def test_timeout_validation(self):
        path = self.candidate()
        with mock.patch.object(runner, "_run_worker") as worker:
            for timeout in (0, -1, True, "1", None, float("nan"), float("inf"), 1 << 100_000):
                with self.subTest(timeout_type=type(timeout).__name__):
                    with self.assertRaisesRegex(RunnerError, "^validation:"):
                        execute(path, [], 0, timeout=timeout)
            worker.assert_not_called()

    def test_path_and_file_validation(self):
        path = self.candidate()
        invalid = [
            str(path), None, self.directory / "missing.wat",
            self.candidate(suffix=".py"), self.candidate(data=b""),
            self.candidate(data=b" " * (runner._MAX_CANDIDATE_BYTES + 1)),
        ]
        directory = self.directory / "directory.wat"
        directory.mkdir()
        invalid.append(directory)
        link = self.directory / "link.wat"
        link.symlink_to(path)
        invalid.append(link)
        with mock.patch.object(runner, "_run_worker") as worker:
            for candidate in invalid:
                with self.subTest(candidate=candidate):
                    with self.assertRaisesRegex(RunnerError, "^candidate:"):
                        execute(candidate, [], 0)
            worker.assert_not_called()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires FIFOs")
    def test_fifo_is_rejected_without_opening(self):
        path = self.directory / "fifo.wat"
        os.mkfifo(path)
        with mock.patch.object(runner.os, "open", side_effect=AssertionError("must not open FIFO")):
            with self.assertRaisesRegex(RunnerError, "^candidate:"):
                execute(path, [], 0)

    def test_open_descriptor_is_rechecked(self):
        info = SimpleNamespace(st_mode=stat.S_IFIFO, st_size=1)
        with mock.patch.object(runner.os, "fstat", return_value=info):
            with self.assertRaisesRegex(RunnerError, "^candidate: expected a regular file"):
                execute(self.candidate(), [], 0)

    def test_growing_file_is_still_bounded(self):
        path = self.candidate(data=b" " * (runner._MAX_CANDIDATE_BYTES + 1))
        info = SimpleNamespace(st_mode=stat.S_IFREG, st_size=1)
        with mock.patch.object(runner.os, "fstat", return_value=info):
            with self.assertRaisesRegex(RunnerError, "^candidate: file must contain"):
                execute(path, [], 0)

    def test_read_error_is_runner_error(self):
        path = self.candidate()
        with mock.patch.object(runner.os, "open", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(RunnerError, "^candidate: cannot read file"):
                execute(path, [], 0)

    def test_numeric_packing_is_little_endian(self):
        packed = runner._pack_inputs([1, -0.0, 0.25], 0)
        self.assertEqual(packed, struct.pack("<3d", 1.0, -0.0, 0.25))


class ProtocolTests(unittest.TestCase):
    def test_valid_output_and_worker_error(self):
        encoded = base64.b64encode(struct.pack("<2d", 3.5, -1.0)).decode("ascii")
        self.assertEqual(runner._parse_response(json.dumps({"outputs": encoded}).encode(), 2), [3.5, -1.0])
        with self.assertRaisesRegex(RunnerError, "^fuel: exhausted"):
            runner._parse_response(b'{"error":"fuel: exhausted"}', 0)

    def test_rejects_malformed_or_oversized_responses(self):
        invalid = [
            b"", b"not json", b"[]", b"null", b"\xff", b"{}",
            b'{"outputs":[]}', b'{"outputs":"!"}', b'{"outputs":"AA=="}',
            b'{"outputs":"","extra":1}', b'{"error":false}',
            b'{"error":""}', b'{"outputs":"","error":"bad"}',
            json.dumps({"error": "x" * 1025}).encode(),
            b" " * (runner._MAX_RESPONSE_BYTES + 1),
            json.dumps({"outputs": base64.b64encode(struct.pack("<2d", 0, 0)).decode()}).encode(),
        ]
        for response in invalid:
            with self.subTest(response_prefix=response[:80]):
                with self.assertRaises(RunnerError):
                    runner._parse_response(response, 1)

    def test_parent_rechecks_finiteness(self):
        for number in (float("nan"), float("inf"), -float("inf")):
            response = json.dumps({"outputs": base64.b64encode(struct.pack("<d", number)).decode()}).encode()
            with self.assertRaisesRegex(RunnerError, "^result: non-finite"):
                runner._parse_response(response, 1)

    def run_request(self, request):
        stdout = io.BytesIO()
        with mock.patch.object(runner.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(request))), \
             mock.patch.object(runner.sys, "stdout", SimpleNamespace(buffer=stdout)), \
             mock.patch.object(runner, "_execute_guest", return_value=b"") as guest:
            self.assertEqual(runner._worker_main(), 0)
            response = json.loads(stdout.getvalue())
        return response, guest

    def test_worker_revalidates_request(self):
        valid = {"format": "wat", "candidate": "KG1vZHVsZSk=", "inputs": "", "max_outputs": 0}
        response, guest = self.run_request(json.dumps(valid).encode())
        self.assertEqual(response, {"outputs": ""})
        guest.assert_called_once_with("wat", b"(module)", b"", 0)
        variants = [
            {"format": "python"}, {"candidate": ""}, {"candidate": "!"},
            {"inputs": "AA=="}, {"max_outputs": True}, {"max_outputs": -1},
            {"inputs": base64.b64encode(struct.pack("<d", float("nan"))).decode()},
            {"inputs": base64.b64encode(b"\x00" * runner._MEMORY_BYTES).decode(), "max_outputs": 1},
        ]
        for changes in variants:
            with self.subTest(changes=list(changes)):
                response, guest = self.run_request(json.dumps(valid | changes).encode())
                self.assertIn("error", response)
                guest.assert_not_called()
        for request in (b"[]", b"{}", b"invalid", b" " * (runner._MAX_REQUEST_BYTES + 1)):
            response, guest = self.run_request(request)
            self.assertTrue(response["error"].startswith("worker:"))
            guest.assert_not_called()


class SubprocessTests(FileCase):
    def helper(self, source):
        # Trusted test worker scripts live separately from candidate files.
        directory = tempfile.TemporaryDirectory(prefix="paper-falsifier-test-worker-")
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "worker.py"
        path.write_text(source, encoding="utf-8")
        return path

    def test_isolated_command_environment_and_fds(self):
        descriptor = os.open(self.candidate(), os.O_RDONLY)
        self.addCleanup(os.close, descriptor)
        os.set_inheritable(descriptor, True)
        helper = self.helper(f'''
import json, os, sys
sys.stdin.buffer.read()
try:
    os.fstat({descriptor})
except OSError:
    fd_closed = True
else:
    fd_closed = False
ok = (fd_closed and sys.flags.isolated and sys.flags.ignore_environment
      and sys.flags.no_user_site and "PAPER_FALSIFIER_TEST_SECRET" not in os.environ
      and "PYTHONPATH" not in os.environ)
print(json.dumps({{"outputs": ""}} if ok else {{"error": "worker: isolation failed"}}))
''')
        with mock.patch.object(runner, "_WORKER_PATH", helper), \
             mock.patch.dict(os.environ, {"PAPER_FALSIFIER_TEST_SECRET": "test-only", "PYTHONPATH": str(self.directory)}), \
             mock.patch.object(runner.subprocess, "Popen", wraps=subprocess.Popen) as popen:
            self.assertEqual(execute(self.candidate(), [], 0), [])
        args, kwargs = popen.call_args
        self.assertEqual(args[0], [sys.executable, "-I", "-B", str(helper), "--worker"])
        self.assertEqual(kwargs["env"], {})
        self.assertTrue(kwargs["close_fds"])
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(kwargs["cwd"], str(helper.parent))

    def test_wall_timeout_includes_worker_startup_and_compilation(self):
        for source in (
            "import time; time.sleep(30)",  # includes blocked request writes
            "import sys, time; sys.stdin.buffer.read(); time.sleep(30)",
            "import os, sys, time; sys.stdin.buffer.read(); os.close(1); time.sleep(30)",
        ):
            with self.subTest(source=source):
                helper = self.helper(source)
                with mock.patch.object(runner, "_WORKER_PATH", helper), \
                     mock.patch.object(runner.subprocess, "Popen", wraps=subprocess.Popen) as popen:
                    # Keep the real child so we can assert it was reaped.
                    real_popen = popen._mock_wraps
                    children = []

                    def launch(*args, **kwargs):
                        child = real_popen(*args, **kwargs)
                        children.append(child)
                        return child

                    popen.side_effect = launch
                    start = time.monotonic()
                    with self.assertRaisesRegex(RunnerError, "^timeout:"):
                        runner._run_worker(b"x" * (256 * 1024), 0.3)
                    self.assertLess(time.monotonic() - start, 5.0)
                    self.assertIsNotNone(children[0].returncode)
                    self.assertTrue(children[0].stdin.closed)
                    self.assertTrue(children[0].stdout.closed)

    def test_stdout_is_bounded(self):
        helper = self.helper(f'''
import sys, time
sys.stdin.buffer.read()
sys.stdout.buffer.write(b"x" * {runner._MAX_RESPONSE_BYTES + 8192})
sys.stdout.buffer.flush()
time.sleep(30)
''')
        with mock.patch.object(runner, "_WORKER_PATH", helper):
            with self.assertRaisesRegex(RunnerError, "^worker: response exceeds output limit"):
                execute(self.candidate(), [], 0)

    def test_stderr_cannot_fill_a_pipe(self):
        helper = self.helper('''
import sys
sys.stdin.buffer.read()
sys.stderr.write("x" * (1024 * 1024))
print('{"outputs":""}')
''')
        with mock.patch.object(runner, "_WORKER_PATH", helper):
            self.assertEqual(execute(self.candidate(), [], 0), [])

    def test_worker_exit_and_launch_failure(self):
        helper = self.helper("import sys; sys.stdin.buffer.read(); sys.exit(7)")
        with mock.patch.object(runner, "_WORKER_PATH", helper):
            with self.assertRaisesRegex(RunnerError, "^worker: isolated process exited with status 7"):
                execute(self.candidate(), [], 0)
        with mock.patch.object(runner.subprocess, "Popen", side_effect=OSError("no interpreter")):
            with self.assertRaisesRegex(RunnerError, "^worker: could not launch"):
                execute(self.candidate(), [], 0)

    def test_broken_pipe_is_runner_error(self):
        helper = self.helper("import os; os._exit(9)")
        with mock.patch.object(runner, "_WORKER_PATH", helper):
            with self.assertRaisesRegex(RunnerError, "^worker:"):
                runner._run_worker(b"x" * (256 * 1024), 5.0)

    def test_invalid_worker_reply(self):
        helper = self.helper("import sys; sys.stdin.buffer.read(); print('not json')")
        with mock.patch.object(runner, "_WORKER_PATH", helper):
            with self.assertRaisesRegex(RunnerError, "^worker: malformed response"):
                execute(self.candidate(), [], 0)


@unittest.skipIf(wasmtime is None, "requires wasmtime-py >=39,<40")
class WasmTests(FileCase):
    def evaluate(self, text, inputs=None, capacity=1, **kwargs):
        return execute(self.candidate(text), [] if inputs is None else inputs, capacity, **kwargs)

    def test_sum(self):
        text = module('''
          (local $i i32) (local $sum f64)
          (block $done (loop $again
            local.get $i local.get $n i32.ge_u br_if $done
            local.get $sum
            local.get $in local.get $i i32.const 8 i32.mul i32.add f64.load
            f64.add local.set $sum
            local.get $i i32.const 1 i32.add local.set $i
            br $again))
          local.get $out local.get $sum f64.store
          i32.const 1
        ''')
        self.assertEqual(self.evaluate(text, [1, -2.5, 4.25]), [2.75])
        self.assertEqual(self.evaluate(text, []), [0.0])

    def test_exact_pointer_count_and_capacity_arguments(self):
        body = " ".join(
            f"local.get $out i32.const {index * 8} i32.add local.get ${name} f64.convert_i32_u f64.store"
            for index, name in enumerate(("in", "n", "out", "cap"))
        ) + " i32.const 4"
        self.assertEqual(self.evaluate(module(body), [9.0, 8.0, 7.0], 4), [0.0, 3.0, 24.0, 4.0])

    def test_wat_and_wasm_round_trip(self):
        text = module(_COPY)
        inputs = [-0.0, 1.7976931348623157e308, -1e-310, 1.25]
        for suffix, data in ((".wat", text.encode()), (".wasm", bytes(wasmtime.wat2wasm(text)))):
            with self.subTest(suffix=suffix):
                outputs = execute(self.candidate(suffix=suffix, data=data), inputs, len(inputs))
                self.assertEqual(outputs, inputs)
                self.assertEqual(math.copysign(1, outputs[0]), -1)
                self.assertTrue(all(type(value) is float for value in outputs))

    def test_full_page_layout_and_full_transport(self):
        inputs = [index / 4 for index in range(runner._MAX_VALUES // 2)]
        self.assertEqual(self.evaluate(module(_COPY), inputs, len(inputs)), inputs)
        self.assertEqual(self.evaluate(module(), [0.0] * runner._MAX_VALUES, 0), [])
        self.assertEqual(self.evaluate(module("local.get $cap"), [], runner._MAX_VALUES), [0.0] * runner._MAX_VALUES)
        self.assertEqual(self.evaluate(module(), [], 0), [])

    def test_candidate_size_boundary_and_uppercase_extension(self):
        text = module().encode()
        data = text + b" " * (runner._MAX_CANDIDATE_BYTES - len(text))
        self.assertEqual(execute(self.candidate(suffix=".WAT", data=data), [], 0), [])

    def test_returned_count_selects_only_reported_outputs(self):
        body = "local.get $out f64.const 2.5 f64.store local.get $out i32.const 8 i32.add f64.const nan f64.store i32.const 1"
        self.assertEqual(self.evaluate(module(body), [], 2), [2.5])

    def test_fresh_instance_globals_and_memory(self):
        text = module('''
          global.get $counter i32.const 1 i32.add global.set $counter
          local.get $out global.get $counter f64.convert_i32_s f64.store
          local.get $out i32.const 8 i32.add
          local.get $out i32.const 8 i32.add f64.load f64.const 1 f64.add f64.store
          i32.const 2
        ''', extra="(global $counter (mut i32) (i32.const 0))")
        path = self.candidate(text)
        self.assertEqual(execute(path, [], 2), [1.0, 1.0])
        self.assertEqual(execute(path, [], 2), [1.0, 1.0])

    def test_invalid_compilation_and_binary_text_confusion(self):
        candidates = [
            self.candidate("(module"), self.candidate(data=b"\xff"),
            self.candidate(suffix=".wasm", data=module().encode()),
            self.candidate(suffix=".wasm", data=b"\x00asm\x01"),
        ]
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(RunnerError, "^compile:"):
                    execute(candidate, [], 0)

    def test_all_import_kinds_and_wasi_are_rejected(self):
        imports = [
            '(import "env" "callback" (func))',
            '(import "env" "memory" (memory 1 1))',
            '(import "env" "table" (table 1 funcref))',
            '(import "env" "value" (global i32))',
            '(import "wasi_snapshot_preview1" "fd_write" (func (param i32 i32 i32 i32) (result i32)))',
        ]
        for declaration in imports:
            with self.subTest(declaration=declaration):
                with self.assertRaisesRegex(RunnerError, "^imports:"):
                    self.evaluate(f"(module {declaration})", capacity=0)
        imported_solve = '''(module
          (import "env" "solve" (func $solve (param i32 i32 i32 i32) (result i32)))
          (export "solve" (func $solve))
          (memory (export "memory") 1 1))'''
        with self.assertRaisesRegex(RunnerError, "^imports:"):
            self.evaluate(imported_solve, capacity=0)

    def test_missing_extra_and_wrong_kind_exports(self):
        texts = [
            "(module)", '(module (memory (export "memory") 1 1))',
            '(module (func (export "memory")) (func (export "solve") (param i32 i32 i32 i32) (result i32) i32.const 0))',
            '(module (memory (export "memory") 1 1) (global (export "solve") i32 (i32.const 0)))',
            module(extra='(func (export "extra"))'),
            module().replace('export "solve"', 'export "Solve"'),
        ]
        for text in texts:
            with self.subTest(text=text):
                with self.assertRaisesRegex(RunnerError, "^abi:"):
                    self.evaluate(text, capacity=0)

    def test_wrong_signatures_including_coercible_numeric_types(self):
        signatures = [
            ("(param i32 i32 i32) (result i32)", "i32.const 0"),
            ("(param i32 i32 i32 i32 i32) (result i32)", "i32.const 0"),
            ("(param i32 i32 i32 i32)", ""),
            ("(param i32 i32 i32 i32) (result i32 i32)", "i32.const 0 i32.const 0"),
        ]
        for kind in ("i64", "f32", "f64"):
            signatures.append((f"(param i32 i32 i32 {kind}) (result i32)", "i32.const 0"))
            signatures.append((f"(param i32 i32 i32 i32) (result {kind})", f"{kind}.const 0"))
        for signature, body in signatures:
            with self.subTest(signature=signature):
                with self.assertRaisesRegex(RunnerError, "^abi:.*signature"):
                    self.evaluate(module(body, signature=signature), capacity=0)

    def test_abi_validation_precedes_start_function(self):
        text = module("i64.const 0", signature="(param i32 i32 i32 i32) (result i64)", extra="(func $start (loop br 0)) (start $start)")
        with self.assertRaisesRegex(RunnerError, "^abi:.*signature"):
            self.evaluate(text, capacity=0)

    def test_bound_signature_is_independently_checked(self):
        original = wasmtime.Instance.exports
        callback = mock.Mock(return_value=0)

        def wrong_exports(instance, store):
            exports = original(instance, store)
            wrong_type = wasmtime.FuncType([wasmtime.ValType.i64()] * 4, [wasmtime.ValType.i32()])
            return {"memory": exports["memory"], "solve": wasmtime.Func(store, wrong_type, callback)}

        with mock.patch.object(wasmtime.Instance, "exports", new=wrong_exports):
            with self.assertRaisesRegex(RunnerError, "^abi: bound 'solve'"):
                runner._execute_guest("wat", module().encode(), b"", 0)
        callback.assert_not_called()

    def test_bound_memory_size_and_short_read_are_checked(self):
        with mock.patch.object(wasmtime.Memory, "data_len", return_value=8):
            with self.assertRaisesRegex(RunnerError, "^memory: linear memory is too short"):
                runner._execute_guest("wat", module().encode(), b"", 0)
        with mock.patch.object(wasmtime.Memory, "read", return_value=bytearray(7)):
            with self.assertRaisesRegex(RunnerError, "^memory: incomplete output read"):
                runner._execute_guest("wat", module("i32.const 1").encode(), b"", 1)

    def test_initial_memory_must_be_exactly_one_page(self):
        for memory in ("0 1", "0 0", "2 2"):
            with self.subTest(memory=memory):
                with self.assertRaisesRegex(RunnerError, "^memory:"):
                    self.evaluate(module(memory=memory), capacity=0)

    def test_store_limit_blocks_growth_even_without_declared_maximum(self):
        body = """
          local.get $out i32.const 1 memory.grow f64.convert_i32_s f64.store
          local.get $out i32.const 8 i32.add memory.size f64.convert_i32_u f64.store
          i32.const 2
        """
        for memory in ("1 1", "1 20", "1"):
            with self.subTest(memory=memory):
                self.assertEqual(self.evaluate(module(body, memory=memory), [], 2), [-1.0, 1.0])
        text = module("local.get $out memory.size f64.convert_i32_u f64.store i32.const 1", memory="1", extra="(func $start i32.const 1 memory.grow drop) (start $start)")
        self.assertEqual(self.evaluate(text), [1.0])

    def test_memory_proposals_and_multiple_memories_are_disabled(self):
        for text in (module(memory="1 1 shared"), module(memory="i64 1 1"), module(extra="(memory 1 1)")):
            with self.subTest(text=text):
                with self.assertRaisesRegex(RunnerError, "^compile:"):
                    self.evaluate(text, capacity=0)

    def test_table_limits(self):
        for extra in (f"(table {runner._MAX_TABLE_ELEMENTS + 1} funcref)", "(table 0 funcref) (table 0 funcref)"):
            with self.subTest(extra=extra):
                with self.assertRaisesRegex(RunnerError, "^resource:"):
                    self.evaluate(module(extra=extra), capacity=0)
        body = f"local.get $out ref.null func i32.const {runner._MAX_TABLE_ELEMENTS} table.grow 0 f64.convert_i32_s f64.store i32.const 1"
        self.assertEqual(self.evaluate(module(body, extra="(table 1 funcref)")), [-1.0])

    def test_store_limits_are_installed_before_instantiation(self):
        events = []
        set_limits = wasmtime.Store.set_limits
        instantiate = wasmtime.Instance.__init__

        def record_limits(store, **limits):
            events.append(("limits", limits))
            return set_limits(store, **limits)

        def record_instance(instance, store, compiled, imports):
            events.append(("instance", list(imports)))
            return instantiate(instance, store, compiled, imports)

        with mock.patch.object(wasmtime.Store, "set_limits", new=record_limits), \
             mock.patch.object(wasmtime.Instance, "__init__", new=record_instance):
            self.assertEqual(runner._execute_guest("wat", module().encode(), b"", 0), b"")
        self.assertEqual(events, [
            ("limits", {"memory_size": 65536, "table_elements": runner._MAX_TABLE_ELEMENTS,
                        "instances": 1, "tables": 1, "memories": 1}),
            ("instance", []),
        ])

    def test_return_count_validation(self):
        for count in (-1, 2, 2147483648):
            with self.subTest(count=count):
                with self.assertRaisesRegex(RunnerError, "^result:.*count"):
                    self.evaluate(module(f"i32.const {count}"), capacity=1)

    def test_nonfinite_guest_outputs(self):
        for value in ("nan", "inf", "-inf"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(RunnerError, "^result: non-finite"):
                    self.evaluate(module(f"local.get $out f64.const {value} f64.store i32.const 1"))

    def test_traps_and_out_of_bounds_memory(self):
        with self.assertRaisesRegex(RunnerError, "^trap:"):
            self.evaluate(module("unreachable"), capacity=0)
        with self.assertRaisesRegex(RunnerError, "^memory: guest accessed out-of-bounds"):
            self.evaluate(module("i32.const 65536 f64.const 0 f64.store i32.const 0"), capacity=0)
        with self.assertRaisesRegex(RunnerError, "^trap:"):
            self.evaluate(module(extra="(func $start unreachable) (start $start)"), capacity=0)

    def test_infinite_solve_and_start_exhaust_fuel(self):
        for text in (module("(loop br 0) i32.const 0"), module(extra="(func $start (loop br 0)) (start $start)")):
            with self.subTest(text=text):
                with self.assertRaisesRegex(RunnerError, "^fuel:"):
                    self.evaluate(text, capacity=0)

    def test_recursion_hits_stack_bound(self):
        text = module("i32.const 0 call $recurse", extra="""
          (func $recurse (param $n i32) (result i32)
            local.get $n i32.const 1 i32.add call $recurse
            i32.const 1 i32.add)
        """)
        with self.assertRaisesRegex(RunnerError, "^stack:"):
            self.evaluate(text, capacity=0)

    def test_tiny_wall_timeout(self):
        with self.assertRaisesRegex(RunnerError, "^timeout:"):
            self.evaluate(module(), capacity=0, timeout=1e-9)

    def test_runner_remains_usable_after_guest_failure(self):
        with self.assertRaisesRegex(RunnerError, "^trap:"):
            self.evaluate(module("unreachable"), capacity=0)
        self.assertEqual(self.evaluate(module(), capacity=0), [])


if __name__ == "__main__":
    unittest.main()
