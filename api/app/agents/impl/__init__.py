"""Agent implementations — importing this package registers every agent."""
from . import evidence, intake, ops  # noqa: F401

# later crews register themselves if present (each is self-contained)
for _mod in ("case_level", "compliance", "underwriting", "ops_extra", "liveness_agent"):
    try:
        __import__(f"{__name__}.{_mod}")
    except ModuleNotFoundError as _e:  # not built yet
        if _mod not in str(_e):
            raise
