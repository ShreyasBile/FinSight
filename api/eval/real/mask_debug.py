"""Development diagnostic for masking (dev data; the PAN cards are synthetic, the page IDs generated):
why are the last four characters not readable after masking?   python -m eval.real.mask_debug"""
import base64
import io

from eval import common as C


def main():
    C.setup_env(provider="ollama", db=False)
    from PIL import Image
    from app import mask as M
    from app.imaging import load_image
    from eval.real import core as R
    from eval.real import intake_real as I
    cache = R.ItemCache("pii_masking", "pan-v3-dev")
    shown = 0
    for it in I.pan_items("dev", 20):
        got = cache.get(it["id"])
        if not got or got.get("last4"):
            continue
        data, pan = it["path"].read_bytes(), it["expected"]["pan_number"]
        pil = load_image(data).convert("RGB")
        toks = M._upright_tokens(pil)
        regs = M._find_regions(toks, {}, "pan")
        res = M.mask_document(data, {}, "pan")
        masked = base64.b64decode(res["masked_image"].split(",", 1)[1])
        after = I._ocr_text(masked, rotations=(0,))
        cand = [t for t in toks if any(ch.isdigit() for ch in t["text"]) and len(t["alnum"]) >= 8]
        print(f"pan={pan} tokens={len(toks)} candidates={[(t['text'], t['box']) for t in cand][:3]} regions={[(r[0], r[1], r[2]) for r in regs]}")
        print("   after:", [w for w in after.split() if "X" in w.upper() or pan[-4:] in w.upper() or any(c.isdigit() for c in w)][:6],
              "| size", pil.size, "masked size", Image.open(io.BytesIO(masked)).size)
        shown += 1
        if shown >= 6:
            break


if __name__ == "__main__":
    main()
