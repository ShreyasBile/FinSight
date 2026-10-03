"""Development check for card outline detection (MIDV-500 dev frames only): share of frames where the
outline Stage 2 picks matches the true card (IoU >= 0.85), and where it picks a WRONG outline
(IoU < 0.5), with and without the rectangle fallback.   python -m eval.real.card_dev"""
from eval import common as C


def main():
    C.setup_env(provider="ollama", db=False)
    from app import preprocess as P
    from eval.real import intake_real as I
    frames = I.midv_items("dev")
    for label, on, fill, margin in (("as built", False, 0.90, 0.01), ("fallback 0.90 m=0", True, 0.90, -1.0),
                                    ("fallback 0.90 m=1%", True, 0.90, 0.01), ("fallback 0.90 m=3%", True, 0.90, 0.03),
                                    ("fallback 0.85 m=1%", True, 0.85, 0.01), ("fallback 0.93 m=1%", True, 0.93, 0.01)):
        P.RECT_FALLBACK, P.RECT_FILL, P.RECT_MARGIN = on, fill, margin
        by, wrong, n, wby = {}, 0, 0, {}
        for it in frames:
            q = I._card_quad(it["image"])
            iou = I._quad_iou(q, it["mask"]) if q is not None else 0.0
            b = by.setdefault(it["condition"], [0, 0])
            b[0] += iou >= 0.85
            b[1] += 1
            wrong += q is not None and iou < 0.5
            wby[it["condition"]] = wby.get(it["condition"], 0) + (q is not None and iou < 0.5)
            n += 1
        tot = sum(v[0] for v in by.values())
        print(f"{label}: located {tot}/{n} = {tot / n:.3f}  wrong outline {wrong}/{n} = {wrong / n:.3f}  "
              + "  ".join(f"{k}:{v[0]}/{v[1]}" for k, v in sorted(by.items())) + "  wrong by bg: " + str(dict(sorted(wby.items()))))
    P.RECT_FALLBACK, P.RECT_FILL, P.RECT_MARGIN = True, 0.90, 0.01


if __name__ == "__main__":
    main()
