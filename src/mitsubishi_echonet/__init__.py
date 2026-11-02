"""Tools for local ECHONET Lite control of Mitsubishi Electric Wi-Fi adapters."""

import sys as _sys

__version__ = "0.1.0"

MIN_PYTHON = (3, 11)


def _check_python_version() -> None:
    """Clean error and exit on interpreters older than MIN_PYTHON.

    Python 3.10 is end-of-life; running on it is not supported. Called at
    import time, before any submodule (and its 3.11-only syntax) loads.
    """
    if _sys.version_info < MIN_PYTHON:
        have = ".".join(str(p) for p in _sys.version_info[:3])
        want = ".".join(str(p) for p in MIN_PYTHON)
        raise SystemExit(
            f"mitsubishi-echonet requires Python {want}+ (you are running {have}). "
            "Python 3.10 is end-of-life; please use a newer interpreter."
        )


_check_python_version()
