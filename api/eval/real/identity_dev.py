"""Development check for the Document Identity gate on PAN uploads (dev split, no model call): which
evidence on the page itself separates a PAN card from a foreign ID card or a receipt?
  says_pan   the page text carries a PAN heading (the gate's own space-insensitive matching)
  has_code   OCR reads a PAN-shaped code on the page (cleaned image, else the upload as it came)
    python -m eval.real.identity_dev"""
import json

from eval import common as C


def main(n_pan: int = 80, n_other: int = 40):
    C.setup_env(provider="ollama", db=False)
    from app.agents.registry import get_agent
    from app.agents.tools.doc_keywords import page_text, scores
    from app.agents.tools.id_crossread import _PAN
    from eval.real import evidence_real as E
    from eval.real import intake_real as I
    pre = get_agent("preprocessing")
    sets = {"pan_cards": [(it["path"].read_bytes(), "image/png") for it in I.pan_items("dev", n_pan)],
            "foreign_ids": [(it["path"].read_bytes(), it["mime"]) for it in E.sidtd_items("dev", n_other // 2)][:n_other],
            "receipts": [(I._png(it["image"]), "image/png") for it in I.sroie_items("dev", n_other)]}
    out = {}
    for name, docs in sets.items():
        a = {"n": len(docs), "text_readable": 0, "says_pan": 0, "has_code": 0, "either": 0, "neither_but_readable": 0}
        for data, mime in docs:
            p = pre.execute({"data": data, "mime": mime, "doc_type": "pan"}).payload
            t = (p.get("page_text") or "").lower()
            if len(t.strip()) < 20:
                t = page_text(p["data"], p["mime"])
            up = page_text(data, mime)
            says_clean = scores(t)["pan"] > 0
            says = says_clean or scores(up)["pan"] > 0                 # heading in the cleaned image or the upload
            code = bool(_PAN.search(t.upper())) or bool(_PAN.search(up.upper()))
            a["says_pan_cleaned_only"] = a.get("says_pan_cleaned_only", 0) + says_clean
            readable = len(t.strip()) >= 40
            a["text_readable"] += readable
            a["says_pan"] += says
            a["has_code"] += code
            a["either"] += says or code
            a["neither_but_readable"] += readable and not (says or code)
        out[name] = a
        print(name, a, flush=True)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
