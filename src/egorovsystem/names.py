"""Item names, device scopes and ``${variable}`` references."""

from __future__ import annotations

import fnmatch
import re
from typing import Callable, Iterable, Optional

ITEM_KINDS = ("variable", "function")
NAME_PATTERN = r"[A-Za-z0-9_][A-Za-z0-9_.\-]{0,127}"
NAME_RE = re.compile(rf"^{NAME_PATTERN}$")
SCOPE_RE = re.compile(r"^(?:(variable|function):)?([A-Za-z0-9_.\-*?\[\]]{1,128})$")
REFERENCE_RE = re.compile(r"\$\{(" + NAME_PATTERN + r")\}")


def is_valid_name(name: object) -> bool:
    return isinstance(name, str) and bool(NAME_RE.match(name))


def is_valid_kind(kind: object) -> bool:
    return kind in ITEM_KINDS


def normalize_scopes(scopes: object) -> list[str]:
    """Validate a device's scope list. ``["*"]`` means everything.

    A scope is a glob on item names, optionally prefixed with ``variable:`` or
    ``function:`` to restrict it to one kind, e.g. ``variable:openai_*``.
    """
    if scopes is None:
        return ["*"]
    if not isinstance(scopes, list) or not 1 <= len(scopes) <= 64:
        raise ValueError("scopes must be a list of 1 to 64 patterns")
    result = []
    for scope in scopes:
        if not isinstance(scope, str) or not SCOPE_RE.match(scope.strip()):
            raise ValueError(f"invalid scope pattern: {scope!r}")
        if scope.strip() not in result:
            result.append(scope.strip())
    return result


def scope_allows(scopes: Iterable[str], kind: str, name: str) -> bool:
    for scope in scopes:
        match = SCOPE_RE.match(scope)
        if not match:
            continue
        scope_kind, pattern = match.groups()
        if scope_kind and scope_kind != kind:
            continue
        if fnmatch.fnmatchcase(name, pattern):
            return True
    return False


def variables_granted_by_functions(scopes: Iterable[str], refs_by_function: dict) -> set:
    """Variables a device may read because a function it may read uses them.

    Granting a function grants the variables it references: the device could
    not run the function without them anyway."""
    scopes = list(scopes)
    granted: set = set()
    for function, refs in refs_by_function.items():
        if scope_allows(scopes, "function", function):
            granted.update(refs)
    return granted


def normalize_refs(refs: object) -> list[str]:
    """Validate the list of variable names a function references."""
    if not isinstance(refs, list) or len(refs) > 256:
        raise ValueError("refs must be a list of at most 256 names")
    result: list[str] = []
    for name in refs:
        if not is_valid_name(name):
            raise ValueError(f"invalid variable name in refs: {name!r}")
        if name not in result:
            result.append(name)
    return result


def find_references(content: str) -> list[str]:
    """Variable names referenced as ``${name}``, in order of first use."""
    seen: list[str] = []
    for match in REFERENCE_RE.finditer(content or ""):
        if match.group(1) not in seen:
            seen.append(match.group(1))
    return seen


def substitute(content: str, lookup: Callable[[str], Optional[str]]) -> str:
    """Replace ``${name}`` with ``lookup(name)`` in a single pass.

    Names the lookup does not know (it returns ``None``) are left untouched,
    exactly like the old server did, so shell syntax such as ``${HOME}`` keeps
    working. Values are inserted verbatim and never expanded again.
    """
    def replace(match: "re.Match[str]") -> str:
        value = lookup(match.group(1))
        return match.group(0) if value is None else value

    return REFERENCE_RE.sub(replace, content or "")
