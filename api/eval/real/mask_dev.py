"""Development hunt for masking misses on real pages (dev split only; the IDs printed on the pages are
generated, so they may be shown).   python -m eval.real.mask_dev [n_per_set] [skip]

The scored dev set (46 pages) had no miss left after the 2026-10-02 fixes, while the test set kept 8 of 90
exposed. This runs MORE dev pages through the same check and, for every page still exposed, shows which
number survived and how OCR read its line on the original - the only way to see a miss the patterns
do not cover without looking at test pages."""
import base64
import json
import sys

from eval import common as C


def main(n_per_set: int = 40, skip: int = 0):
    C.setup_env(provider="ollama", db=False)
    from app import mask as M
    from app.agents.registry import get_agent
    from app.imaging import load_image
    from eval.real import core as R
    from eval.real import intake_real as I
    agent = get_agent("pii_masking")
    pages = [dict(it, kind="sroie") for it in I.sroie_items("dev", skip + n_per_set)[skip:]] + \
            [dict(it, kind="funsd") for it in I.funsd_items("dev", skip + n_per_set)[skip:]]
    pages += [{"id": it["id"], "image": it["path"].read_bytes(), "words": None, "kind": "rvl"}
              for it in I.rvl_items(8, "dev")]
    cache = R.ItemCache("pii_masking", "hunt-v6-dev")
    agg = {"n": 0, "exposed": 0, "last4": 0, "nothing_masked": 0}
    for it in pages:
        data, pan, aad = I._print_ids(I._png(it["image"]), it["id"])
        dt = I._pick(it["id"], ("salary_slip", "bank_statement", "itr", "form16"))
        r = cache.get(it["id"])
        if r is None or I.exposed(r) or not r.get("last4"):       # a miss is re-run with the current code
            r = cache.put(it["id"], I._mask_check(agent, data, dt, [pan, aad], []))
        agg["n"] += 1
        agg["last4"] += bool(r.get("last4")) and not I.exposed(r)
        agg["nothing_masked"] += not r.get("masked_regions")
        if not I.exposed(r) and r.get("last4"):
            continue
        agg["exposed"] += I.exposed(r)
        toks = M._upright_tokens(load_image(data).convert("RGB"))
        regs = M._find_regions(toks, {}, dt)
        lines = [t["text"] for t in toks if pan[:5] in t["alnum"] or pan[-5:] in t["alnum"] or aad[:4] in t["text"]
                 or aad[-4:] in t["text"] or "adha" in t["text"].lower() or "PAN" in t["text"]]
        res = M.mask_document(data, {}, dt)
        after = I._ocr_text(base64.b64decode(res["masked_image"].split(",", 1)[1]), rotations=(0,)) if res else ""
        seen = [w for w in after.split() if "XX" in w.upper() or pan[-4:] in w.upper() or aad[-4:] in w][:6]
        print(f"{it['kind']} {'EXPOSED' if I.exposed(r) else 'last4 lost'} leak={r.get('leak')} lookalike={r.get('leak_lookalike')} "
              f"regions={[(lb, n) for _b, lb, n in regs]} pan={pan} aad={aad}\n     lines on the original: {lines[:6]}"
              f"\n     read back after masking: {seen}", flush=True)
    print(json.dumps(agg))


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:3]))
