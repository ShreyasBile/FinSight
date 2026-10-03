"""Loads config.yaml into one typed, dot-accessible object.

Usage:
    from app.tampering.config import CFG
    CFG.fusion.weights["ela"]
    CFG.pipeline.gate_threshold
"""
import os
from types import SimpleNamespace

import yaml

# config.yaml, models/ and data/ all live inside this tampering package, so the
# root is this file's own directory (not its parent — that was the standalone
# layout where models/ sat beside app/).
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.yaml")


def _to_namespace(obj):
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _to_namespace(v) for k, v in obj.items()})
    return obj


def load_config(path: str = CONFIG_PATH) -> SimpleNamespace:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    cfg = _to_namespace(raw)
    # fusion.weights stays a plain dict (iterated by key at fusion time)
    cfg.fusion.weights = raw["fusion"]["weights"]
    # per-family, per-signal "genuine floor" (see fusion.calibrated); plain dicts, absent = no calibration
    cfg.fusion.genuine_floor = {fam: dict(v or {}) for fam, v in (raw["fusion"].get("genuine_floor") or {}).items()}
    # resolve storage paths relative to the project root so the pipeline works
    # no matter which directory it is launched from
    cfg.storage.db_path = os.path.join(PROJECT_ROOT, raw["storage"]["db_path"])
    cfg.storage.archive_dir = os.path.join(PROJECT_ROOT, raw["storage"]["archive_dir"])
    cfg.storage.models_dir = os.path.join(PROJECT_ROOT, raw["storage"]["models_dir"])
    cfg.training.synthetic_dir = os.path.join(PROJECT_ROOT, raw["training"]["synthetic_dir"])
    return cfg


CFG = load_config()
