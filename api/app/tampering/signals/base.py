"""The contract every detection signal fulfils.

A signal is a function that takes whatever inputs it needs and returns a
SignalResult. The pipeline/fusion layers only ever consume SignalResults, so
signals stay completely independent of each other and can be added, removed,
or reweighted without touching anything else.
"""
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image


@dataclass
class SignalResult:
    name: str
    # False when the signal could not run for this document (e.g. no trained
    # CNN yet, PDF checks on a JPEG). Unavailable signals are EXCLUDED from
    # fusion and their weight is redistributed — never counted as zero.
    available: bool
    # 0.0 (clean) .. 1.0 (strong tamper evidence). Meaningless if unavailable.
    score: float = 0.0
    # Human-readable, specific observations ("running balance breaks at row 7",
    # "EXIF Software = Adobe Photoshop 25.1"). Shown to the reviewing officer.
    findings: list = field(default_factory=list)
    # Visual evidence (heatmaps, match overlays) keyed by label, for the UI.
    artifacts: dict = field(default_factory=dict)
    # Why the signal didn't run, when available=False.
    skip_reason: Optional[str] = None

    def to_dict(self, include_artifacts: bool = False) -> dict:
        d = {
            "name": self.name,
            "available": self.available,
            "score": round(float(self.score), 4),
            "findings": list(self.findings),
        }
        if self.skip_reason:
            d["skip_reason"] = self.skip_reason
        if include_artifacts:
            d["artifacts"] = list(self.artifacts.keys())
        return d


def unavailable(name: str, reason: str) -> SignalResult:
    return SignalResult(name=name, available=False, skip_reason=reason)
