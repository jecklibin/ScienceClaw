from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["rpa_manager", "RPASession", "RPAStep", "cdp_connector"]

if TYPE_CHECKING:  # pragma: no cover - imported for type checkers only
    from .cdp_connector import cdp_connector
    from .manager import RPASession, RPAStep, rpa_manager


def __getattr__(name: str) -> Any:
    if name in {"rpa_manager", "RPASession", "RPAStep"}:
        from .manager import RPASession as _RPASession
        from .manager import RPAStep as _RPAStep
        from .manager import rpa_manager as _rpa_manager

        globals().update(
            {
                "rpa_manager": _rpa_manager,
                "RPASession": _RPASession,
                "RPAStep": _RPAStep,
            }
        )
        return globals()[name]

    if name == "cdp_connector":
        from .cdp_connector import cdp_connector as _cdp_connector

        globals()["cdp_connector"] = _cdp_connector
        return _cdp_connector

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
