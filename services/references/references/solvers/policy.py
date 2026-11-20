"""What generated solver code may contain, checked before it ever reaches the sandbox.

The sandbox is the security boundary (no network, no secrets, read-only, limits). This check only refuses
code that obviously does more than arithmetic, so such a candidate fails fast with a reason the author can fix:
imports outside a short list of pure standard modules, file and process access, dynamic code, dunder attributes.
"""

import ast

MAX_CODE_CHARS = 20_000
ALLOWED_IMPORTS = frozenset(
    {"math", "cmath", "fractions", "decimal", "itertools", "functools", "statistics", "re", "collections", "numbers"}
)
FORBIDDEN_NAMES = frozenset(
    {
        "open",
        "exec",
        "eval",
        "compile",
        "__import__",
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
    }
)


class CodeRefused(ValueError):
    """The code breaks the policy; the message is safe to show to the author."""


def checkSolverCode(code: str) -> None:
    if len(code) > MAX_CODE_CHARS:
        raise CodeRefused(f"the code is longer than {MAX_CODE_CHARS} characters")
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        raise CodeRefused(f"SyntaxError: {error.msg} (line {error.lineno})") from error
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                checkModule(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise CodeRefused("relative imports are not allowed")
            checkModule(node.module or "")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise CodeRefused(f"the name {node.id!r} is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise CodeRefused(f"the attribute {node.attr!r} is not allowed")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            raise CodeRefused("global and nonlocal are not allowed")
    if not any(isinstance(node, ast.FunctionDef) and node.name == "solve" for node in tree.body):
        raise CodeRefused("the code must define a top-level function solve(task)")


def checkModule(name: str) -> None:
    if name.split(".", 1)[0] not in ALLOWED_IMPORTS:
        allowed = ", ".join(sorted(ALLOWED_IMPORTS))
        raise CodeRefused(f"import of {name!r} is not allowed; only: {allowed}")
