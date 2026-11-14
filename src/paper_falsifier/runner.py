"""Run a small, import-free numeric WebAssembly candidate.

Requires Python 3.11+ and wasmtime-py 50.0.1 in the current interpreter's
(non-user) environment.  Compilation, instantiation (including start functions),
and execution all take place in a fresh, isolated Python subprocess.

The only exports are ``memory`` and ``solve``.  Memory must start with one 64 KiB
page; its *effective* maximum is one page, even if the module declares a larger
or unbounded maximum.  ``solve(i32, i32, i32, i32) -> i32`` receives the input
byte offset (zero), input element count, output byte offset, and output element
capacity.  Buffers contain little-endian f64 values and may together occupy at
most one page.  The returned count must be between zero and the capacity.

RunnerError messages have stable category prefixes: validation, candidate,
compile, imports, abi, memory, resource, fuel, stack, trap, result, timeout,
or worker.  This is a Wasmtime capability boundary, not an OS sandbox for
arbitrary native code: the trusted worker itself runs Python and Wasmtime.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import threading
import time


__all__ = ["RunnerError", "execute", "execute_candidate", "read_candidate"]

_MAX_CANDIDATE_BYTES = 128 * 1024
_MEMORY_BYTES = 64 * 1024
_MAX_VALUES = _MEMORY_BYTES // 8
_FUEL = 10_000_000
_WASM_STACK_BYTES = 256 * 1024
_MAX_TABLE_ELEMENTS = 1024
_MAX_REQUEST_BYTES = 384 * 1024
_MAX_RESPONSE_BYTES = 128 * 1024
_WORKER_PATH = Path(__file__).resolve()


class RunnerError(RuntimeError):
    """An invalid candidate, invalid request, or bounded execution failure."""


def _detail(error: object) -> str:
    # Compiler diagnostics can contain candidate-controlled text.  Do not send
    # arbitrarily large diagnostics back over the worker protocol.
    return str(error).replace("\n", " ").replace("\r", " ")[:600]


def _check_layout(input_count: int, max_outputs: int) -> None:
    if type(max_outputs) is not int or not 0 <= max_outputs <= _MAX_VALUES:
        raise RunnerError(f"validation: max_outputs must be an integer in [0, {_MAX_VALUES}]")
    if not 0 <= input_count <= _MAX_VALUES:
        raise RunnerError(f"validation: inputs may contain at most {_MAX_VALUES} values")
    if input_count + max_outputs > _MAX_VALUES:
        raise RunnerError("memory: input and output buffers exceed 64 KiB")


def _pack_inputs(inputs: list[float], max_outputs: int) -> bytes:
    # Exact built-in types prevent iterators, coercion hooks, booleans, and list
    # subclasses from turning numeric validation into arbitrary/unbounded work.
    if type(inputs) is not list:
        raise RunnerError("validation: inputs must be a plain list of finite numbers")
    _check_layout(len(inputs), max_outputs)
    values = []
    for index, value in enumerate(inputs):
        if type(value) not in (int, float):
            raise RunnerError(f"validation: input {index} is not a plain number")
        try:
            number = float(value)
        except (OverflowError, ValueError) as exc:
            raise RunnerError(f"validation: input {index} is not representable as f64") from exc
        if not math.isfinite(number):
            raise RunnerError(f"validation: input {index} is not finite")
        values.append(number)
    return struct.pack(f"<{len(values)}d", *values)


def _check_timeout(timeout: float) -> float:
    if type(timeout) not in (int, float):
        raise RunnerError("validation: timeout must be a finite positive number")
    try:
        seconds = float(timeout)
    except (ValueError, OverflowError) as exc:
        raise RunnerError("validation: timeout must be a finite positive number") from exc
    if not math.isfinite(seconds) or not 0 < seconds <= threading.TIMEOUT_MAX:
        raise RunnerError("validation: timeout must be positive, finite, and within platform limits")
    return seconds


@dataclass(frozen=True)
class Candidate:
    kind: str
    data: bytes


def read_candidate(candidate: Path) -> Candidate:
    if not isinstance(candidate, Path):
        raise RunnerError("candidate: expected a pathlib.Path")
    kind = candidate.suffix.lower()
    if kind not in (".wat", ".wasm"):
        raise RunnerError("candidate: expected a .wat or .wasm file")
    fd = None
    try:
        # Do not block opening a FIFO or follow a symlink to a device.  Recheck
        # the opened descriptor to cover replacement between lstat and open.
        if not stat.S_ISREG(candidate.lstat().st_mode):
            raise RunnerError("candidate: expected a regular file, not a symlink or special file")
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(candidate, flags)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise RunnerError("candidate: expected a regular file")
        if not 0 < info.st_size <= _MAX_CANDIDATE_BYTES:
            raise RunnerError("candidate: file must contain between 1 byte and 128 KiB")
        # A bounded read also catches files that grow after fstat.  Never read
        # more than the limit plus one byte, even for a changing regular file.
        with os.fdopen(fd, "rb") as stream:
            fd = None
            data = stream.read(_MAX_CANDIDATE_BYTES + 1)
        if not 0 < len(data) <= _MAX_CANDIDATE_BYTES:
            raise RunnerError("candidate: file must contain between 1 byte and 128 KiB")
        return Candidate(kind[1:], data)
    except (OSError, ValueError) as exc:
        raise RunnerError(f"candidate: cannot read file: {_detail(exc)}") from exc
    finally:
        if fd is not None:
            os.close(fd)


def _encode_blob(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _decode_blob(value: object, limit: int, description: str) -> bytes:
    if type(value) is not str or len(value) > 4 * ((limit + 2) // 3):
        raise RunnerError(f"worker: invalid {description} encoding or size")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RunnerError(f"worker: invalid {description} encoding") from exc
    if len(decoded) > limit:
        raise RunnerError(f"worker: {description} exceeds its size limit")
    return decoded


def _finite_values(data: bytes, label: str) -> list[float]:
    if len(data) % 8:
        raise RunnerError(f"{label}: buffer is not an array of f64 values")
    values = [item[0] for item in struct.iter_unpack("<d", data)]
    if not all(math.isfinite(value) for value in values):
        raise RunnerError(f"{label}: non-finite numeric value")
    return values


def _run_worker(request: bytes, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", str(_WORKER_PATH), "--worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={},
            cwd=str(_WORKER_PATH.parent),
            close_fds=True,
            shell=False,
        )
    except (OSError, ValueError) as exc:
        raise RunnerError(f"worker: could not launch isolated Python: {_detail(exc)}") from exc

    response = b""
    io_error = None
    done = threading.Event()

    def exchange() -> None:
        nonlocal response, io_error
        try:
            # A thread covers blocked writes as well as reads.  Unlike
            # communicate(), the stdout read has an explicit allocation cap.
            process.stdin.write(request)
            process.stdin.close()
            response = process.stdout.read(_MAX_RESPONSE_BYTES + 1)
            if len(response) > _MAX_RESPONSE_BYTES:
                io_error = "worker: response exceeds output limit"
        except (OSError, ValueError) as exc:
            io_error = f"worker: pipe failure: {_detail(exc)}"
        finally:
            done.set()

    thread = threading.Thread(target=exchange, name="paper-falsifier-worker-io", daemon=True)
    started = False
    try:
        thread.start()
        started = True
        if not done.wait(max(0.0, deadline - time.monotonic())):
            raise RunnerError("timeout: worker exceeded wall-time during compilation or execution")
        if io_error is not None:
            raise RunnerError(io_error)
        try:
            process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise RunnerError("timeout: worker exceeded wall-time during compilation or execution") from exc
        # Even a worker that finished before wait() was reached must fit the
        # deadline (in particular, extremely small timeouts cannot slip past).
        if time.monotonic() > deadline:
            raise RunnerError("timeout: worker exceeded wall-time during compilation or execution")
        if process.returncode != 0:
            raise RunnerError(f"worker: isolated process exited with status {process.returncode}")
        return response
    except RuntimeError as exc:
        if isinstance(exc, RunnerError):
            raise
        raise RunnerError(f"worker: could not start I/O thread: {_detail(exc)}") from exc
    finally:
        # No child (or pipe thread) is left running on success, timeout, protocol
        # failure, or interruption.  Guests cannot create descendant processes.
        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        process.wait()
        if started:
            thread.join()
        for stream in (process.stdin, process.stdout):
            try:
                stream.close()
            except OSError:
                # A worker that died before consuming stdin can leave a
                # buffered write whose close raises BrokenPipeError.
                pass


def _parse_response(response: bytes, max_outputs: int) -> list[float]:
    if len(response) > _MAX_RESPONSE_BYTES:
        raise RunnerError("worker: response exceeds output limit")
    try:
        message = json.loads(response)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise RunnerError("worker: malformed response") from exc
    if type(message) is not dict:
        raise RunnerError("worker: malformed response")
    if set(message) == {"error"} and type(message["error"]) is str:
        if not message["error"] or len(message["error"]) > 1024:
            raise RunnerError("worker: malformed error response")
        raise RunnerError(message["error"])
    if set(message) != {"outputs"}:
        raise RunnerError("worker: malformed response")
    data = _decode_blob(message["outputs"], max_outputs * 8, "output")
    return _finite_values(data, "result")


def execute(
    candidate: Path,
    inputs: list[float],
    max_outputs: int,
    *,
    timeout: float = 5.0,
) -> list[float]:
    """Evaluate one candidate in a fresh worker or raise :class:`RunnerError`.

    Plain finite Python ints/floats are accepted as inputs (not bools or custom
    numeric objects).  The candidate must be a nonempty regular file, not a
    symlink, at most 128 KiB.  ``timeout`` covers worker startup, compilation,
    instantiation, execution, and reply transfer; parent-side validation occurs
    before that deadline starts.  No environment variables or extra file
    descriptors are inherited by the worker, and no imports are given to Wasm.
    """
    return execute_candidate(read_candidate(candidate), inputs, max_outputs, timeout=timeout)


def execute_candidate(
    candidate: Candidate,
    inputs: list[float],
    max_outputs: int,
    *,
    timeout: float = 5.0,
) -> list[float]:
    """Execute the same bounded candidate snapshot across multiple cases."""
    seconds = _check_timeout(timeout)
    input_bytes = _pack_inputs(inputs, max_outputs)
    request = json.dumps(
        {"format": candidate.kind, "candidate": _encode_blob(candidate.data),
         "inputs": _encode_blob(input_bytes), "max_outputs": max_outputs},
        separators=(",", ":"),
    ).encode("ascii")
    if len(request) > _MAX_REQUEST_BYTES:
        raise RunnerError("validation: request exceeds transport limit")
    return _parse_response(_run_worker(request, seconds), max_outputs)


def _is_solve_type(value: object, wasmtime: object) -> bool:
    return (
        isinstance(value, wasmtime.FuncType)
        and [str(param) for param in value.params] == ["i32"] * 4
        and [str(result) for result in value.results] == ["i32"]
    )


def _trap_error(trap: object) -> RunnerError:
    code = getattr(trap.trap_code, "name", "")
    if code == "OUT_OF_FUEL":
        return RunnerError("fuel: guest exhausted its CPU instruction budget")
    if code == "STACK_OVERFLOW":
        return RunnerError("stack: guest exceeded the WebAssembly stack limit")
    if code == "MEMORY_OUT_OF_BOUNDS":
        return RunnerError("memory: guest accessed out-of-bounds linear memory")
    return RunnerError(f"trap: {_detail(trap)}")


def _execute_guest(kind: str, candidate: bytes, inputs: bytes, max_outputs: int) -> bytes:
    try:
        import wasmtime
    except ImportError as exc:
        raise RunnerError("worker: wasmtime-py 50.0.1 must be installed") from exc

    try:
        config = wasmtime.Config()
        config.consume_fuel = True
        config.max_wasm_stack = _WASM_STACK_BYTES
        config.parallel_compilation = False
        config.wasm_threads = False
        config.wasm_component_model = False
        config.wasm_custom_page_sizes = False
        config.wasm_memory64 = False
        config.wasm_multi_memory = False
        config.wasm_relaxed_simd = False
        config.wasm_simd = False
        config.wasm_bulk_memory = False
        config.wasm_gc = False
        config.wasm_stack_switching = False
        engine = wasmtime.Engine(config)
        if kind == "wat":
            binary = wasmtime.wat2wasm(candidate.decode("utf-8"))
        else:
            # Module() also accepts textual bytes: explicitly prevent a .wasm
            # file from silently taking the WAT path.
            if not candidate.startswith(b"\x00asm"):
                raise RunnerError("compile: .wasm candidate lacks the WebAssembly binary magic")
            binary = candidate
        module = wasmtime.Module(engine, binary)
    except (wasmtime.WasmtimeError, UnicodeError) as exc:
        raise RunnerError(f"compile: invalid WebAssembly: {_detail(exc)}") from exc

    # Inspect module types BEFORE instantiation: an invalid ABI must not get to
    # run a start function, and Wasmtime's numeric coercions are not ABI checks.
    if module.imports:
        raise RunnerError("imports: guest must have zero imports (including WASI)")
    exports = {export.name: export.type for export in module.exports}
    if set(exports) != {"memory", "solve"}:
        raise RunnerError("abi: expected exactly the exports 'memory' and 'solve'")
    if not isinstance(exports["memory"], wasmtime.MemoryType):
        raise RunnerError("abi: 'memory' must export a linear memory")
    if not _is_solve_type(exports["solve"], wasmtime):
        raise RunnerError("abi: 'solve' must have signature (i32, i32, i32, i32) -> i32")
    if exports["memory"].limits.min != 1:
        raise RunnerError("memory: initial linear memory must be exactly one 64 KiB page")

    store = wasmtime.Store(engine)
    try:
        store.set_limits(
            memory_size=_MEMORY_BYTES,
            table_elements=_MAX_TABLE_ELEMENTS,
            instances=1,
            tables=1,
            memories=1,
        )
        store.set_fuel(_FUEL)
        instance = wasmtime.Instance(store, module, [])
    except wasmtime.Trap as exc:
        raise _trap_error(exc) from exc
    except wasmtime.WasmtimeError as exc:
        raise RunnerError(f"resource: instantiation failed: {_detail(exc)}") from exc

    bound_exports = instance.exports(store)
    memory = bound_exports["memory"]
    solve = bound_exports["solve"]
    # Check bound externs too, rather than trusting callable() or discovering a
    # signature mismatch through invocation (which misses i64/f32 coercions).
    if not isinstance(memory, wasmtime.Memory):
        raise RunnerError("abi: bound 'memory' is not linear memory")
    if not isinstance(solve, wasmtime.Func) or not _is_solve_type(solve.type(store), wasmtime):
        raise RunnerError("abi: bound 'solve' has the wrong signature")
    output_offset = len(inputs)
    output_end = output_offset + max_outputs * 8
    if memory.data_len(store) != _MEMORY_BYTES or output_end > memory.data_len(store):
        raise RunnerError("memory: linear memory is too short for the input/output buffers")
    try:
        if inputs:
            written = memory.write(store, inputs, 0)
            if written != len(inputs):
                raise RunnerError("memory: incomplete input write")
        count = solve(store, 0, len(inputs) // 8, output_offset, max_outputs)
    except wasmtime.Trap as exc:
        raise _trap_error(exc) from exc
    except wasmtime.WasmtimeError as exc:
        raise RunnerError(f"trap: execution failed: {_detail(exc)}") from exc
    except (IndexError, OverflowError) as exc:
        raise RunnerError(f"memory: input/output access failed: {_detail(exc)}") from exc
    if type(count) is not int or not 0 <= count <= max_outputs:
        raise RunnerError("result: solve returned a negative count or a count above output capacity")
    # read() uses slice semantics, so explicitly verify length rather than
    # accepting silent truncation of an out-of-bounds request.
    output = bytes(memory.read(store, output_offset, output_offset + count * 8))
    if len(output) != count * 8:
        raise RunnerError("memory: incomplete output read")
    _finite_values(output, "result")
    return output


def _worker_main() -> int:
    try:
        request = sys.stdin.buffer.read(_MAX_REQUEST_BYTES + 1)
        if len(request) > _MAX_REQUEST_BYTES:
            raise RunnerError("worker: request exceeds transport limit")
        try:
            message = json.loads(request)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise RunnerError("worker: malformed request") from exc
        if type(message) is not dict or set(message) != {"format", "candidate", "inputs", "max_outputs"}:
            raise RunnerError("worker: malformed request")
        if message["format"] not in ("wat", "wasm"):
            raise RunnerError("worker: unknown candidate format")
        candidate = _decode_blob(message["candidate"], _MAX_CANDIDATE_BYTES, "candidate")
        if not candidate:
            raise RunnerError("candidate: empty candidate")
        inputs = _decode_blob(message["inputs"], _MEMORY_BYTES, "input")
        _finite_values(inputs, "validation")
        _check_layout(len(inputs) // 8, message["max_outputs"])
        output = _execute_guest(message["format"], candidate, inputs, message["max_outputs"])
        response = {"outputs": _encode_blob(output)}
    except RunnerError as exc:
        response = {"error": str(exc)[:1024]}
    except Exception as exc:
        # A dependency/API failure is not a guest trap and should retain a
        # distinct label, but never emit an unbounded traceback to a pipe.
        response = {"error": f"worker: internal failure ({type(exc).__name__}): {_detail(exc)}"}
    encoded = json.dumps(response, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    if len(encoded) > _MAX_RESPONSE_BYTES:
        encoded = b'{"error":"worker: response exceeds output limit"}'
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit("This module is an internal worker; use execute() from Python.")
    raise SystemExit(_worker_main())
