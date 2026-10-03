"""Versioned model registry with an explicit promotion workflow.

models/
├── registry.json        {"versions": {"v001": {...}, ...}}
└── v001/
    ├── weights.pt
    └── metrics.json

Lifecycle: train.py saves a new version as status=candidate → evaluate.py
measures it on held-out groups → `--promote` flips it to live (and demotes the
previous live model to retired). Inference ONLY ever loads the live model, so
a bad training run can never silently change production scores.
"""
import json
import os
from datetime import datetime

from app.tampering.config import CFG


class ModelRegistry:
    def __init__(self, models_dir: str | None = None):
        self.dir = models_dir or CFG.storage.models_dir
        os.makedirs(self.dir, exist_ok=True)
        self.index_path = os.path.join(self.dir, "registry.json")

    def _load(self) -> dict:
        if not os.path.isfile(self.index_path):
            return {"versions": {}}
        with open(self.index_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, index: dict) -> None:
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)

    def next_version(self) -> str:
        index = self._load()
        nums = [int(v[1:]) for v in index["versions"]] or [0]
        return f"v{max(nums) + 1:03d}"

    def weights_path(self, version: str) -> str:
        return os.path.join(self.dir, version, "weights.pt")

    def register(self, version: str, info: dict) -> None:
        """Called by train.py after saving weights. Status starts as candidate."""
        index = self._load()
        index["versions"][version] = {
            "version": version,
            "status": "candidate",
            "created": datetime.now().isoformat(timespec="seconds"),
            **info,
        }
        self._save(index)

    def set_metrics(self, version: str, metrics: dict) -> None:
        index = self._load()
        index["versions"][version]["metrics"] = metrics
        self._save(index)
        with open(os.path.join(self.dir, version, "metrics.json"), "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2)

    def set_calibration(self, version: str, w: float, b: float) -> None:
        """Store the CNN's page-score calibration: evidence = sigmoid(w*max + b).
        Fit per model on held-out pages, because different models produce
        different raw score ranges (a conservative model's forged-max may be
        0.64 where an over-confident one's is 0.92) — a single hardcoded floor
        can't serve both. cnn_classifier reads this for whichever model is live."""
        index = self._load()
        index["versions"][version]["cnn_calibration"] = [float(w), float(b)]
        self._save(index)

    def promote(self, version: str) -> None:
        """Make `version` live; previous live model becomes retired."""
        index = self._load()
        if version not in index["versions"]:
            raise ValueError(f"unknown model version {version}")
        for v, info in index["versions"].items():
            if info["status"] == "live":
                info["status"] = "retired"
        index["versions"][version]["status"] = "live"
        self._save(index)

    def get_live(self) -> dict | None:
        for info in self._load()["versions"].values():
            if info["status"] == "live":
                return info
        return None

    def all_versions(self) -> list[dict]:
        return sorted(self._load()["versions"].values(), key=lambda v: v["version"])
