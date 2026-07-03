"""Loads the compiled proprietary risk engine if its binary is present.

The gateway ships with only the compiled `_risk_engine` extension (built from the
private engine source via risk_engine/build_engine.sh) -- never the source. If
the binary isn't installed (e.g. someone is running the open gateway without it),
assess() raises EngineUnavailable and the server reports it cleanly.
"""

from __future__ import annotations


class EngineUnavailable(Exception):
    pass


try:
    from . import _risk_engine as _eng  # compiled extension, dropped in by the build
    _assess = _eng.assess
    AVAILABLE = True
except Exception:
    _assess = None
    AVAILABLE = False


def assess(snapshot: dict) -> dict:
    if _assess is None:
        raise EngineUnavailable(
            "risk engine binary not installed; build it with risk_engine/build_engine.sh"
        )
    return _assess(snapshot)
