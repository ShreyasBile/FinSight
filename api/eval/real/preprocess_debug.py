"""Development diagnostic for preprocessing on real scans (dev split only): which path does a page
take and why is it not left alone?   python -m eval.real.preprocess_debug [n]"""
import io
import sys

from eval import common as C


def main(n: int = 14):
    C.setup_env(provider="ollama", db=False)
    import cv2
    import numpy as np
    from PIL import Image, ImageOps
    from app import preprocess as P
    from eval.real import intake_real as I
    for it in I.sroie_items("dev", n) + I.funsd_items("dev", 4):
        data = I._png(it["image"])
        logs = []
        out = P.preprocess_image(data, langs=("en",), page=True, log_fn=logs.append)
        img = P._pil_to_bgr(ImageOps.exif_transpose(Image.open(io.BytesIO(data))))
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        p1, p5, p50 = (float(np.percentile(gray, q)) for q in (1, 5, 50))
        key = [m.strip()[:60] for m in logs if any(k in m for k in ("Deskew", "Contour (", "rotat", "Rotat", "Clean, upright", "Fixes", "revert"))]
        print(f"{it['set']} {img.shape[1]}x{img.shape[0]} p1={p1:.0f} p5={p5:.0f} p50={p50:.0f} paper_like={P.paper_like(img) is not None} "
              f"unchanged={out == data} | {key[:5]}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 14)
