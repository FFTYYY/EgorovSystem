"""Egorov: end-to-end encrypted variables, secrets and commands for all your machines.

    from egorovsystem import Egorov, get_variable, get_function, set_variable

    key = Egorov["openai_keys"]          # a variable, or a function if no variable has that name
    key = get_variable("openai_keys")    # always a variable
    fn = get_function("deploy")          # {"type": "cmd" | "python", "content": ..., "note": ...}

Configure with EGOROV_SERVER and EGOROV_PWD (a device token), or run
``egolocal-run`` once and let programs on this machine use it.
"""

from ._version import __version__
from .client import (
    AuthError,
    Egorov,
    EgorovError,
    NotFoundError,
    get_data,
    get_function,
    get_variable,
    set_variable,
)

__all__ = [
    "__version__",
    "AuthError",
    "Egorov",
    "EgorovError",
    "NotFoundError",
    "get_data",
    "get_function",
    "get_variable",
    "set_variable",
]
