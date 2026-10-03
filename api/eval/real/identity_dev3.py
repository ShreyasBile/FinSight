"""Development check (dev split, no model call): do bank statements carry a phrase only a statement prints
on page 1, and do receipts / office pages not?   python -m eval.real.identity_dev3"""
import json

from eval import common as C


def main():
    C.setup_env(provider="ollama", db=False)
    from app.agents.registry import get_agent
    from app.agents.tools.doc_keywords import lacks_own_words, page_text
    from eval.real import intake_real as I
    pre = get_agent("preprocessing")

    def text(data, mime):
        p = pre.execute({"data": data, "mime": mime, "doc_type": "bank_statement"}).payload
        t = (p.get("page_text") or "")
        return t if len(t.strip()) >= 20 else page_text(p["data"], p["mime"])
    sets = {"statements_digital": [(s["path"].read_bytes(), "application/pdf") for s in I.statement_items("dev", None) if s["kind"] == "digital"],
            "statements_scanned": [(s["path"].read_bytes(), "application/pdf") for s in I.statement_items("dev", None) if s["kind"] == "scanned"][:20],
            "receipts": [(I._png(it["image"]), "image/png") for it in I.sroie_items("dev", 40)],
            "office_pages": [(I._png(it["path"]), "image/png") for it in I.rvl_items(3, "dev")]}
    out = {}
    for name, docs in sets.items():
        a = {"n": len(docs), "readable": 0, "lacks_statement_words": 0}
        for data, mime in docs:
            t = text(data, mime)
            a["readable"] += len(t.strip()) >= 40
            a["lacks_statement_words"] += lacks_own_words("bank_statement", t)
        out[name] = a
        print(name, a, flush=True)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
