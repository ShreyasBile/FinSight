"""Development check of the card outline (MIDV development frames only), through the app's own path.

History, all on the development frames (card found = IoU >= 0.85 with the true outline):
  contour search alone (with the rectangle fallback)          44 of 90, a wrong outline used on ~9%
  a learned network trained on synthetic scenes                23 of 90   (rejected)
  a straight-line quadrilateral search                         23 of 90   (rejected)
  region + edge candidates under one score (app/card_outline)  68 of 90, no wrong outline used
      table 20/20, keyboard 20/20, clutter 20/20, card held at the frame edge 8/30
  growing partial outlines outward to the next straight edges  no change (rejected)
Not solved: a pale glossy card held in front of a whiteboard (2 of 20 frames).

    python -m eval.real.card_regions_dev [--viz]
"""
from __future__ import annotations

import io
import json
import os
import sys

import numpy as np

from eval import common as C


def main():
    C.setup_env(provider="ollama", db=False)
    import cv2
    from PIL import Image
    from eval.real.intake_real import _card_quad, _quad_iou, midv_items
    by, tiles = {}, []
    for it in midv_items("dev"):
        q = _card_quad(it["image"])
        iou = _quad_iou(q, it["mask"]) if q is not None else 0.0
        b = by.setdefault(it["condition"], {"n": 0, "located": 0, "wrong_outline": 0, "none_used": 0})
        b["n"] += 1
        b["located"] += iou >= 0.85
        b["wrong_outline"] += q is not None and iou < 0.5
        b["none_used"] += q is None
        if "--viz" in sys.argv and iou < 0.85 and len(tiles) < 12:
            img = cv2.cvtColor(np.array(Image.open(io.BytesIO(it["image"])).convert("RGB")), cv2.COLOR_RGB2BGR)
            m = (np.array(Image.open(io.BytesIO(it["mask"])).convert("L")) > 127).astype(np.uint8)
            cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(img, cnts, -1, (0, 255, 0), 8)
            if q is not None:
                cv2.polylines(img, [np.round(q).astype(np.int32)], True, (0, 0, 255), 8)
            sm = cv2.resize(img, (270, 480))
            cv2.putText(sm, f"{it['clip']} {iou:.2f}", (5, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
            tiles.append(sm)
    for k, v in sorted(by.items()):
        print(k, v, flush=True)
    print(json.dumps({k: sum(v[k] for v in by.values()) for k in ("n", "located", "wrong_outline", "none_used")}))
    if tiles:
        while len(tiles) % 4:
            tiles.append(np.zeros_like(tiles[0]))
        cv2.imwrite(os.path.join(os.environ.get("VIZ_OUT", "."), "card_misses.jpg"),
                    np.vstack([np.hstack(tiles[i:i + 4]) for i in range(0, len(tiles), 4)]))


if __name__ == "__main__":
    main()
