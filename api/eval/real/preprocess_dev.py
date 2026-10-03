"""Development check for preprocessing on real scans (dev split only): word recall before / after
cleanup, harm and help rates.   python -m eval.real.preprocess_dev [n_sroie] [n_funsd]"""
import sys

from eval import common as C


def main(n_s: int = 24, n_f: int = 12):
    C.setup_env(provider="ollama", db=False)
    from app.agents.registry import get_agent
    from eval.real import intake_real as I
    agent = get_agent("preprocessing")
    rows = []
    for it in I.sroie_items("dev", n_s) + I.funsd_items("dev", n_f):
        data = I._png(it["image"])
        out = agent.execute({"data": data, "mime": "image/png", "doc_type": "salary_slip"}).payload["data"]
        b = I._word_recall(I._ocr_text(data), it["words"])
        a = b if out == data else I._word_recall(I._ocr_text(out), it["words"])
        if a is not None and b is not None:
            rows.append((it["set"], b, a))
    for st in ("sroie", "funsd", None):
        rs = [r for r in rows if st is None or r[0] == st]
        if rs:
            print(st or "all", "n", len(rs), "before", round(sum(r[1] for r in rs) / len(rs), 4), "after", round(sum(r[2] for r in rs) / len(rs), 4),
                  "harmed(-2pt)", sum(r[2] < r[1] - 0.02 for r in rs), "harmed(-5pt)", sum(r[2] < r[1] - 0.05 for r in rs),
                  "helped(+2pt)", sum(r[2] > r[1] + 0.02 for r in rs))


if __name__ == "__main__":
    main(*(int(x) for x in sys.argv[1:3]))
