"""EGO: run a function stored in Egorov.

    EGO <name> [args...]     run it; extra args are passed along
    EGO --show <name>        print it (after ${variable} substitution)
    EGO --list               list what this device may read
    EGO --whoami             show which device this machine uses
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Optional

from . import __version__
from .client import EgorovError, get_client, get_function

PEP723_RE = re.compile(r"(?m)^# /// script\s*$")


def quote_args(args: list) -> str:
    if not args:
        return ""
    if sys.platform == "win32":
        return " " + subprocess.list2cmdline(args)
    return " " + " ".join(shlex.quote(a) for a in args)


def run_python(content: str, args: list) -> int:
    """Run Python code with the interpreter that runs EGO itself, so it works
    even where no ``python`` is on PATH. Scripts that declare dependencies with
    PEP 723 inline metadata run through ``uv run`` when uv is installed."""
    fd, path = tempfile.mkstemp(suffix=".py", prefix="egorov-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        uv = shutil.which("uv")
        if uv and PEP723_RE.search(content):
            command = [uv, "run", "--quiet", "--script", path, *args]
        else:
            command = [sys.executable, path, *args]
        return subprocess.call(command)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def run_cmd(content: str, args: list) -> int:
    return subprocess.call(content + quote_args(args), shell=True)


def execute(function: dict, args: list) -> int:
    kind = function.get("type", "cmd")
    if kind == "cmd":
        return run_cmd(function["content"], args)
    if kind == "python":
        return run_python(function["content"], args)
    raise EgorovError(f"Unknown function type {kind!r}; this EGO understands 'cmd' and 'python'.")


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if argv[0] == "--version":
        print(f"EGO {__version__}")
        return 0
    if argv[0] == "--list":
        for item in get_client().list_items():
            print(f"{item['kind']:8}  {item['name']}")
        return 0
    if argv[0] == "--whoami":
        me = get_client().whoami()
        print(f"{me['name']} ({me['id']}), scopes: {', '.join(me['scopes'])}")
        return 0
    if argv[0] == "--show":
        if len(argv) != 2:
            print("usage: EGO --show <name>", file=sys.stderr)
            return 2
        function = get_function(argv[1])
        print(f"# type: {function['type']}")
        print(function["content"])
        return 0
    return execute(get_function(argv[0]), argv[1:])


def run(argv: Optional[list] = None) -> None:
    try:
        code = main(argv)
    except EgorovError as exc:
        print(f"EGO: {exc}", file=sys.stderr)
        code = 1
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    run()
