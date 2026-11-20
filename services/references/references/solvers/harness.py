"""Runs one generated solver inside a child process. Trusted code: it is a script, never imported by the services.

Started as `python -I -S harness.py` with an empty environment (see runner.py). stdin is one JSON object
{"code": str, "task": {"statement": str, "constraints": {...}}, "allowedImports": [str], "cpuS": int};
stdout gets one JSON line: {"ok": true, "value": {...}} or {"ok": false, "error": "..."}.
Before the solver code runs, the process limits itself: memory, CPU time, no files written, no child processes.
The solver sees builtins without file, import or reflection functions; print() goes nowhere.
"""

import builtins
import io
import json
import resource
import sys
import traceback

MEMORY_BYTES = 256 * 1024 * 1024
FORBIDDEN = (
    "open",
    "exec",
    "eval",
    "compile",
    "input",
    "breakpoint",
    "globals",
    "locals",
    "vars",
    "getattr",
    "setattr",
    "delattr",
    "memoryview",
    "exit",
    "quit",
    "help",
)
MAX_ERROR_CHARS = 1000


def limitSelf(cpuS: int) -> None:
    for limit, value in (
        (resource.RLIMIT_AS, MEMORY_BYTES),
        (resource.RLIMIT_CPU, cpuS),
        (resource.RLIMIT_FSIZE, 0),
        (resource.RLIMIT_NPROC, 0),
        (resource.RLIMIT_CORE, 0),
    ):
        try:
            resource.setrlimit(limit, (value, value))
        except (ValueError, OSError):
            pass


def restrictedBuiltins(allowedImports: frozenset[str]) -> dict[str, object]:
    realImport = builtins.__import__

    def guardedImport(name: str, *args: object, **kwargs: object) -> object:
        if name.split(".", 1)[0] not in allowedImports:
            raise ImportError(f"import of {name!r} is not allowed")
        return realImport(name, *args, **kwargs)  # type: ignore[arg-type]

    allowed: dict[str, object] = {name: value for name, value in vars(builtins).items() if name not in FORBIDDEN}
    allowed["__import__"] = guardedImport
    return allowed


def main() -> None:
    out = sys.stdout
    try:
        job = json.loads(sys.stdin.read())
        code, task = job["code"], job["task"]
        allowedImports = frozenset(job["allowedImports"])
        limitSelf(int(job.get("cpuS", 10)))
        sys.stdout = io.StringIO()
        namespace: dict[str, object] = {"__builtins__": restrictedBuiltins(allowedImports), "__name__": "solver"}
        exec(compile(code, "solver.py", "exec"), namespace)
        solve = namespace.get("solve")
        if not callable(solve):
            raise TypeError("the code does not define solve(task)")
        value = solve(task)
        if not isinstance(value, dict):
            raise TypeError(f"solve(task) must return a dict, got {type(value).__name__}")
        text = json.dumps({"ok": True, "value": value}, ensure_ascii=False, allow_nan=False)
    except BaseException as error:  # noqa: BLE001 - every failure of the candidate is its answer
        lines = [frame.lineno for frame in traceback.extract_tb(error.__traceback__) if frame.filename == "solver.py"]
        where = f" (solver.py line {lines[-1]})" if lines else ""
        message = f"{type(error).__name__}: {error}{where}"[:MAX_ERROR_CHARS]
        text = json.dumps({"ok": False, "error": message}, ensure_ascii=False)
    out.write(text + "\n")
    out.flush()


if __name__ == "__main__":
    main()
