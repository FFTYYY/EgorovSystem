"""EGO: run a function stored in Egorov.

    EGO <name> [args...]           run it; extra args are passed along
    EGO --get <variable> [-o FILE] print a variable's value and nothing else
    EGO --show <name> [-o FILE]    print a function (after ${variable} substitution)
    EGO --help <name>              print the note of a variable or function
    EGO --list                     list what this device may read
    EGO --whoami                   show which device this machine uses

Output that is piped or redirected is the exact stored text in UTF-8.
-o FILE writes it to FILE directly; use it in Windows PowerShell 5.1,
whose `>` turns output into UTF-16.
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
from .client import EgorovError, NotFoundError, get_client, get_function, get_variable

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


def emit(text: str, output: Optional[str] = None, label: Optional[str] = None) -> None:
    """Write text exactly as stored: UTF-8, newlines untouched, nothing added.

    With ``output`` the bytes go straight into that file (private on POSIX),
    which also sidesteps Windows PowerShell 5.1, whose ``>`` re-encodes
    everything as UTF-16. On a terminal, ``label`` is shown first and the
    text ends with a newline so the prompt starts on a fresh line."""
    data = text.encode("utf-8")
    if output:
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        return
    buffer = getattr(sys.stdout, "buffer", None)
    if sys.stdout.isatty() or buffer is None:
        if label and sys.stdout.isatty():
            print(label)
        sys.stdout.write(text + "\n" if sys.stdout.isatty() and not text.endswith("\n") else text)
        sys.stdout.flush()
    else:
        sys.stdout.flush()
        buffer.write(data)
        buffer.flush()


def split_output(args: list) -> tuple:
    """Take ``-o FILE`` / ``--output FILE`` out of ``args``. A dangling ``-o``
    stays in the rest, where the caller rejects it as a usage error."""
    rest, output = [], None
    index = 0
    while index < len(args):
        if args[index] in ("-o", "--output") and index + 1 < len(args):
            output = args[index + 1]
            index += 2
        else:
            rest.append(args[index])
            index += 1
    return rest, output


def one_name(names: list) -> Optional[str]:
    return names[0] if len(names) == 1 and names[0] not in ("-o", "--output") else None


def show_notes(name: str) -> int:
    """Print the note of the variable and/or function called ``name``."""
    client = get_client()
    found = []
    for kind in ("function", "variable"):
        try:
            found.append((kind, client.get_item(kind, name).get("note", "")))
        except NotFoundError:
            pass
    if not found:
        raise NotFoundError(f"no variable or function named {name!r} (or this device may not read it)")
    if len(found) == 1:
        kind, note = found[0]
        if note:
            print(note)
        else:
            print(f"{kind} {name!r} has no note", file=sys.stderr)
        return 0
    for kind, note in found:
        print(f"{kind} {name}: {note or '(no note)'}")
    return 0


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] in ("-h", "--help") and len(argv) == 1):
        print(__doc__.strip())
        return 0
    if argv[0] in ("-h", "--help"):
        if len(argv) != 2:
            print("usage: EGO --help <name>", file=sys.stderr)
            return 2
        return show_notes(argv[1])
    if argv[0] == "--get":
        names, output = split_output(argv[1:])
        name = one_name(names)
        if name is None:
            print("usage: EGO --get <variable> [-o FILE]", file=sys.stderr)
            return 2
        emit(get_variable(name), output)
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
        names, output = split_output(argv[1:])
        name = one_name(names)
        if name is None:
            print("usage: EGO --show <name> [-o FILE]", file=sys.stderr)
            return 2
        function = get_function(name)
        emit(function["content"], output, label=f"# type: {function['type']}")
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
