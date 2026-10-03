"""Case-level agents on real data: Dedupe, Fraud Triage, Cross Verification, Policy Check.

Dedupe / Fraud Triage — real faces. LFW's ten View-2 folds are identity-disjoint, so a person in folds
4-8 never appears in folds 9-10. The index holds 1,000 applicants whose ID photo comes from folds 4-8;
"new genuine applicants" come from folds 9-10 and are therefore guaranteed to be different people —
any duplicate alert on them is a false alert. Planted duplicates use LFW's labelled same-person pairs.
The index is the app's own (dedupe.check / stage_faces / fraud_triage.triage) in the isolated eval data
dir; nothing touches the live index. Embeddings stay in memory (biometric data is never cached).

Cross Verification — real Indian first names (55k list) with common surnames, in labelled pairs built the
way Indian documents actually differ (initials, honorifics, surname-first order, omitted middle name,
marriage, spelling variants) and the way they must NOT match (other people, a father's document).

Policy Check — real L&T loan inputs through policy_context.build_context and the rules engine, compared
rule by rule with an independent implementation written from each rule's declared condition.
"""
from __future__ import annotations

import ast
import hashlib
import json
import operator
import random
import string
import subprocess
import tempfile
from pathlib import Path

from eval import common as C
from eval.real import core as R

NAMES = R.RAW / "names_in" / "Names.txt"
FACES = R.RAW / "faces" / "val"


# ── real faces ──────────────────────────────────────────────────────────────
def lfw_pairs():
    """LFW View 2: [(fold 0-9, label, img_a, img_b)]."""
    out = []
    for i, line in enumerate((FACES / "lfw_ann.txt").read_text().splitlines()):
        lab, a, b = line.split()
        out.append({"i": i, "fold": i // 600, "label": int(lab), "a": FACES / a, "b": FACES / b})
    return out


_EMB: dict = {}


def embed(path: Path):
    """SFace embedding of one benchmark face (the face service, on the same neutral canvas as the face eval)."""
    if path not in _EMB:
        from app.face.service import embed_faces
        from eval.real.face_real import raw_image
        faces = embed_faces(raw_image(path))
        _EMB[path] = [float(v) for v in faces[0]["embedding"]] if faces else None
    return _EMB[path]


def _pan(rng: random.Random) -> str:
    from eval.datasets.synth.identities import make_pan
    return make_pan(rng, rng.choice(string.ascii_uppercase))


def _case(pan: str | None, file_sha: str, account: str | None = None) -> dict:
    docs = {"pan": {"sha256": file_sha, "parsed": {"pan_number": pan} if pan else {}}}
    if account:
        docs["bank_statement"] = {"sha256": hashlib.sha256((file_sha + "stmt").encode()).hexdigest(),
                                  "parsed": {"account_number": account}}
    return {"documents": docs}


def _enroll(cid: str, case: dict, emb):
    from app.agents.tools import dedupe
    if emb is not None:
        dedupe.stage_faces(cid, {"pan": [emb]})
    return dedupe.check(cid, case, register=True)


def build_population(split: str = "test", n_index: int = 1000, n_each: int = 100, n_new: int = 500):
    """Index of n_index people + planted probes. Test: gallery folds 4-8, new people folds 9-10.
    Dev: gallery folds 1-2, new people fold 3 (used only to choose an improvement)."""
    rng = random.Random(f"{R.SEED}-{split}")
    pairs = lfw_pairs()
    gal_folds, new_folds = ({3, 4, 5, 6, 7}, {8, 9}) if split == "test" else ({0, 1}, {2})
    same = [p for p in pairs if p["fold"] in gal_folds and p["label"] == 1]
    other = [p for p in pairs if p["fold"] in gal_folds and p["label"] == 0]
    fresh = [p for p in pairs if p["fold"] in new_folds]
    rng.shuffle(same)
    rng.shuffle(other)
    rng.shuffle(fresh)
    reserved = same[:3 * n_each]                       # their photo a is enrolled; photo b comes back later
    index = [{"cid": f"G{i:04d}", "img": p["a"], "pan": _pan(rng), "sha": hashlib.sha256(f"g{i}".encode()).hexdigest(),
              "account": str(rng.randint(10 ** 10, 10 ** 11 - 1))} for i, p in enumerate(reserved)]
    pool = [p["a"] for p in other] + [p["b"] for p in other] + [p["a"] for p in same[3 * n_each:]]
    for p in pool[: n_index - len(index)]:
        i = len(index)
        index.append({"cid": f"G{i:04d}", "img": p, "pan": _pan(rng), "sha": hashlib.sha256(f"g{i}".encode()).hexdigest(),
                      "account": str(rng.randint(10 ** 10, 10 ** 11 - 1))})
    fresh_imgs = [p["a"] for p in fresh] + [p["b"] for p in fresh]
    probes = []
    for k, p in enumerate(reserved[:n_each]):          # same person, same PAN: a returning customer
        g = index[k]
        probes.append({"cid": f"R{k:03d}", "img": p["b"], "pan": g["pan"], "sha": f"r{k}", "expect": ("repeat_applicant", g["cid"])})
    for k, p in enumerate(reserved[n_each:2 * n_each]):  # same face, NEW PAN: synthetic identity
        g = index[n_each + k]
        probes.append({"cid": f"S{k:03d}", "img": p["b"], "pan": _pan(rng), "sha": f"s{k}",
                       "expect": ("same_face_different_id", g["cid"])})
    for k in range(n_each):                            # enrolled PAN, a different person's face: stolen ID
        g = index[2 * n_each + k]
        probes.append({"cid": f"T{k:03d}", "img": fresh_imgs[k], "pan": g["pan"], "sha": f"t{k}",
                       "expect": ("same_id_different_face", g["cid"])})
    for k in range(n_each // 2):                       # the very same file, new PAN, a different face
        g = index[3 * n_each + k]
        probes.append({"cid": f"F{k:03d}", "img": fresh_imgs[n_each + k], "pan": _pan(rng), "sha": g["sha"],
                       "expect": ("same_file_reused", g["cid"])})
    for k in range(n_new):                             # new genuine applicants: guaranteed different people
        probes.append({"cid": f"N{k:03d}", "img": fresh_imgs[2 * n_each + k], "pan": _pan(rng), "sha": f"n{k}", "expect": None})
    return index, probes


def _sha(s: str) -> str:
    return s if len(s) == 64 else hashlib.sha256(s.encode()).hexdigest()


def run_dedupe(index, probes, threshold: float | None = None) -> list[dict]:
    from app.agents.tools import dedupe
    old = dedupe.FACE_DEDUPE_T
    if threshold is not None:
        dedupe.FACE_DEDUPE_T = threshold
    try:
        dedupe.reset_index()
        for g in index:
            _enroll(g["cid"], _case(g["pan"], _sha(g["sha"])), embed(g["img"]))
        out = []
        for p in probes:
            e = embed(p["img"])
            if e is not None:
                dedupe.stage_faces(p["cid"], {"pan": [e]})
            res = dedupe.check(p["cid"], _case(p["pan"], _sha(p["sha"])), register=False)
            with dedupe._lock:                          # a probe is looked up, not added to the index
                conn = dedupe._db()
                conn.execute("DELETE FROM faces WHERE case_id = ?", (p["cid"],))
                conn.commit()
                conn.close()
            out.append({"hits": [(h["kind"], h["other_case"]) for h in res["hits"]], "face_found": e is not None})
        return out
    finally:
        dedupe.FACE_DEDUPE_T = old


def _dedupe_metrics(probes, res) -> dict:
    planted = [(p, r) for p, r in zip(probes, res) if p["expect"]]
    new = [(p, r) for p, r in zip(probes, res) if not p["expect"]]
    found = [tuple(p["expect"]) in [tuple(h) for h in r["hits"]] for p, r in planted]
    per = {}
    for (p, r), f in zip(planted, found):
        b = per.setdefault(p["expect"][0], [0, 0])
        b[0] += f
        b[1] += 1
    extra = sum(1 for p, r in planted for h in r["hits"] if h[1] != p["expect"][1])
    alerts = [r for _, r in new if r["hits"]]
    kinds = {}
    for r in alerts:
        for k, _ in r["hits"]:
            kinds[k] = kinds.get(k, 0) + 1
    return {"recall": R.wilson(sum(found), len(found)),
            "recall_by_kind": {k: {"found": round(v[0] / v[1], 4), "n": v[1]} for k, v in per.items()},
            "false_alert_rate": R.wilson(len(alerts), len(new)),
            "false_floor_alert_rate": R.wilson(sum(1 for r in alerts if any(k != "repeat_applicant" for k, _ in r["hits"])), len(new)),
            "false_alert_kinds": kinds,
            "false_hits_per_new_applicant": round(sum(len(r["hits"]) for _, r in new) / max(1, len(new)), 3),
            "extra_hits_on_planted": extra, "no_face_found": sum(1 for _, r in planted + new if not r["face_found"])}


BASELINE_T = 0.363        # the 1:N threshold as originally built (SFace's 1:1 verification value)


def eval_dedupe(split: str = "test", n_index: int = 1000) -> dict:
    """Two scorings of the same population: the threshold as originally built (saved as
    dedupe.<split>.baseline.json) and the app's current 1:N threshold (dedupe.<split>.json)."""
    from app.agents.tools import dedupe
    index, probes = build_population(split, n_index)
    data = {"name": "LFW View 2 (identity-disjoint folds): index from folds " + ("4-8" if split == "test" else "1-2")
                    + ", new applicants from folds " + ("9-10" if split == "test" else "3"),
            "split": split, "n_index": len(index), "n_probes": len(probes),
            "planted": {"repeat_applicant": 100, "same_face_different_id": 100, "same_id_different_face": 100,
                        "same_file_reused": 50}, "n_new_genuine": sum(1 for p in probes if not p["expect"]),
            "note": "an alert on a new applicant is false by construction (different folds = different people)"}
    base = _dedupe_metrics(probes, run_dedupe(index, probes, threshold=BASELINE_T))
    base["face_match_threshold"] = BASELINE_T
    out = R.save("dedupe", split, base, data)
    (R.RESULTS / f"dedupe.{split}{'.smoke' if __import__('os').environ.get('EVAL_SMOKE') else ''}.baseline.json").write_text(
        __import__("json").dumps(out, indent=1, default=str), encoding="utf-8")
    metrics = _dedupe_metrics(probes, run_dedupe(index, probes))
    metrics["face_match_threshold"] = dedupe.FACE_DEDUPE_T
    metrics["baseline_at_0.363"] = {k: base[k] for k in ("recall", "false_alert_rate", "false_floor_alert_rate",
                                                          "false_hits_per_new_applicant", "recall_by_kind")}
    return R.save("dedupe", split, metrics, {**data, "threshold_chosen_on": "LFW folds 1-3 (development), 1.08M impostor pairs"})


def impostor_fmr(split: str, thresholds) -> dict:
    """Per-pair false-match rate between photos of guaranteed-different people (different LFW folds)."""
    import numpy as np
    pairs = lfw_pairs()
    folds = range(3, 10) if split == "test" else range(0, 3)
    rng = random.Random(f"fmr-{split}")
    by_fold = {}
    for p in pairs:
        if p["fold"] in folds:
            by_fold.setdefault(p["fold"], []).append(p["a"])
    embs, fold_of = [], []
    for f, imgs in by_fold.items():
        for img in rng.sample(imgs, min(600, len(imgs))):
            e = embed(img)
            if e is not None:
                embs.append(e)
                fold_of.append(f)
    X = np.asarray(embs, dtype=np.float32)
    X /= np.linalg.norm(X, axis=1, keepdims=True)
    S = X @ X.T
    F = np.asarray(fold_of)
    mask = F[:, None] != F[None, :]
    iu = np.triu_indices(len(X), 1)
    vals = S[iu][mask[iu]]
    gen = [float(np.dot(np.asarray(embed(p["a"])) / np.linalg.norm(embed(p["a"])),
                        np.asarray(embed(p["b"])) / np.linalg.norm(embed(p["b"]))))
           for p in pairs if p["fold"] in folds and p["label"] == 1 and embed(p["a"]) and embed(p["b"])]
    return {"impostor_pairs": int(vals.size), "genuine_pairs": len(gen),
            "by_threshold": {f"{t:.3f}": {"fmr": float((vals >= t).mean()), "tar": round(sum(g >= t for g in gen) / len(gen), 4)}
                             for t in thresholds}}


# ── fraud triage on the same population ─────────────────────────────────────
def _triage_run(split: str, n_index: int, threshold: float | None) -> tuple[dict, dict]:
    from app.agents.tools import dedupe, fraud_triage
    rng = random.Random(f"triage-{split}")
    index, _ = build_population(split, n_index)
    pairs = lfw_pairs()
    gal_folds = {3, 4, 5, 6, 7} if split == "test" else {0, 1}
    used = {g["img"] for g in index}
    same = [p for p in pairs if p["fold"] in gal_folds and p["label"] == 1 and p["a"] not in used and p["b"] not in used]
    rng.shuffle(same)
    rings, apps = [], []
    for k, p in enumerate(same[:40]):                  # same face under two PANs
        a, b = f"RF{k:02d}a", f"RF{k:02d}b"
        apps += [{"cid": a, "img": p["a"], "pan": _pan(rng), "sha": a, "account": None},
                 {"cid": b, "img": p["b"], "pan": _pan(rng), "sha": b, "account": None}]
        rings.append({a, b})
    for k in range(20):                                # one bank account behind three PANs
        acct = str(rng.randint(10 ** 10, 10 ** 11 - 1))
        ids = [f"RA{k:02d}{c}" for c in "abc"]
        apps += [{"cid": c, "img": None, "pan": _pan(rng), "sha": c, "account": acct} for c in ids]
        rings.append(set(ids))
    for k in range(20):                                # the same file under two PANs
        ids = [f"RS{k:02d}{c}" for c in "ab"]
        apps += [{"cid": c, "img": None, "pan": _pan(rng), "sha": f"shared-file-{k}", "account": None} for c in ids]
        rings.append(set(ids))
    old = dedupe.FACE_DEDUPE_T
    if threshold is not None:
        dedupe.FACE_DEDUPE_T = threshold
    try:
        dedupe.reset_index()
        for g in index + apps:
            _enroll(g["cid"], _case(g["pan"], _sha(g["sha"]), g.get("account")), embed(g["img"]) if g["img"] else None)
        with C.Timer() as t:
            res = fraud_triage.triage([{"case_id": g["cid"], "application_no": g["cid"]} for g in index + apps],
                                      limit=10 ** 6, already_open=set())
    finally:
        dedupe.FACE_DEDUPE_T = old
    props = [p for p in res["proposals"] if p["kind"] == "ring"]
    # Who is certainly a different person? LFW's folds are identity-disjoint, but INSIDE a fold the same
    # person appears in several pairs (the benchmark has no identity labels in this copy). So an
    # unplanted face link between two applications of the same fold may well be a true match and cannot
    # be scored; a link across folds, or any unplanted account / file link, is certainly false.
    fold = {str(p_[k]): p_["fold"] for p_ in pairs for k in ("a", "b")}
    fold_of = {g["cid"]: fold.get(str(g["img"])) for g in index + apps if g["img"]}
    ring_of = {c: i for i, r in enumerate(rings) for c in r}

    def edge_class(e):
        if e["a"] in ring_of and ring_of.get(e["a"]) == ring_of.get(e["b"]):
            return "planted"
        if set(e["kinds"]) - {"face_across_ids"} or fold_of.get(e["a"]) != fold_of.get(e["b"]):
            return "false"
        return "unverifiable"

    def classify(p):
        kinds = {edge_class(e) for e in p["links"]}
        if "false" in kinds:
            return "false"
        if "planted" in kinds:
            return "correct"
        return "unverifiable"
    cls = [classify(p) for p in props]
    good = [p for p, c in zip(props, cls) if c == "correct"]
    covered = [r for r in rings if any(r <= set(p["case_ids"]) for p in good)]
    exact = [r for r in rings if any(set(p["case_ids"]) == r for p in props)]
    by_kind = {}
    for r in rings:
        k = next(iter(r))[:2]
        b_ = by_kind.setdefault({"RF": "same_face_two_pans", "RA": "shared_account", "RS": "shared_file"}[k], [0, 0])
        b_[0] += r in covered
        b_[1] += 1
    n_false = cls.count("false")
    metrics = {"proposal_precision": R.wilson(len(good), len(good) + n_false),
               "ring_recall": R.wilson(len(covered), len(rings)),
               "ring_exact_rate": R.wilson(len(exact), len(rings)),
               "recall_by_ring_kind": {k: {"found": round(v[0] / v[1], 4), "n": v[1]} for k, v in by_kind.items()},
               "proposals": len(props), "proposals_correct": len(good), "proposals_false": n_false,
               "proposals_unverifiable": cls.count("unverifiable"),
               "largest_proposal": max((len(p["case_ids"]) for p in props), default=0),
               "largest_false_proposal": max((len(p["case_ids"]) for p, c in zip(props, cls) if c == "false"), default=0),
               "links": res["links"], "indexed": res["indexed_cases"], "face_match_threshold": threshold or old,
               "seconds": round(t.ms / 1000, 1)}
    data = {"name": "LFW index of 1,000 real faces + 80 planted rings (40 same-face pairs, 20 shared accounts, 20 shared files)",
            "split": split, "n_apps": len(index) + len(apps), "n_rings": len(rings),
            "scoring": "correct = contains a planted ring's link and no certainly-false link; false = contains a face "
                       "link between people of different LFW folds (guaranteed different) or an unplanted account / "
                       "file link; unverifiable = only same-fold face links (LFW repeats people inside a fold, this "
                       "copy has no identity labels) - left out of precision. A ring is recalled when a correct "
                       "proposal contains all its applications.",
            "scoring_note": "first scoring (2026-10-01) counted every unplanted proposal as false; corrected the same "
                            "day after seeing that most were same-fold links, i.e. possibly the same real person"}
    return metrics, data


def eval_fraud_triage(split: str = "test", n_index: int = 1000) -> dict:
    """Scored twice on the same population: the 1:N threshold as originally built (0.363, kept in the
    result as baseline_at_0.363) and the app's current threshold."""
    base, _ = _triage_run(split, n_index, BASELINE_T)
    metrics, data = _triage_run(split, n_index, None)
    metrics["baseline_at_0.363"] = {k: base[k] for k in ("proposal_precision", "ring_recall", "proposals", "proposals_false",
                                                          "proposals_unverifiable", "largest_false_proposal", "links")}
    return R.save("fraud_triage", split, metrics, data)


# ── cross verification ──────────────────────────────────────────────────────
SURNAMES = ["Sharma", "Verma", "Gupta", "Agarwal", "Jain", "Patel", "Shah", "Mehta", "Desai", "Joshi", "Kulkarni",
            "Deshpande", "Patil", "Pawar", "Jadhav", "Reddy", "Naidu", "Rao", "Iyer", "Iyengar", "Nair", "Menon",
            "Pillai", "Kurup", "Hegde", "Shetty", "Bhat", "Kamath", "Singh", "Kaur", "Gill", "Sandhu", "Sidhu",
            "Chauhan", "Rathore", "Yadav", "Mishra", "Pandey", "Tiwari", "Dubey", "Tripathi", "Shukla", "Saxena",
            "Srivastava", "Banerjee", "Chatterjee", "Mukherjee", "Ghosh", "Bose", "Das", "Dutta", "Sen", "Roy",
            "Khan", "Ansari", "Qureshi", "Sheikh", "Siddiqui", "Fernandes", "DSouza", "Pereira", "Thomas",
            "Mathew", "Varghese", "Choudhary", "Thakur", "Rawat", "Negi", "Bisht", "Mohanty", "Panda", "Sahu"]
# spellings of one name seen across Indian documents (transliteration)
VARIANTS = [("Mohammed", "Mohammad"), ("Mohammed", "Mohd"), ("Lakshmi", "Laxmi"), ("Srinivas", "Shrinivas"),
            ("Shrikant", "Srikanth"), ("Venkatesh", "Venkatesan"), ("Pooja", "Puja"), ("Rajesh", "Rajes"),
            ("Saurabh", "Sourabh"), ("Vaibhav", "Vaibhaw"), ("Kavitha", "Kavita"), ("Sunita", "Sunitha"),
            ("Jyoti", "Jyothi"), ("Deepak", "Dipak"), ("Rakesh", "Raakesh"), ("Sachin", "Sachine"),
            ("Ankit", "Ankith"), ("Swati", "Swathi"), ("Prakash", "Parkash"), ("Shweta", "Sweta")]
HONOR = ["Mr.", "Shri", "Smt.", "Dr.", "Ms.", "Mrs."]


def _first_names() -> list[str]:
    seen, out = set(), []
    for n in NAMES.read_text(encoding="utf-8", errors="ignore").split():
        n = n.strip().capitalize()
        if n.isalpha() and 3 <= len(n) <= 12 and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def name_pairs(split: str, n_each: int = 150) -> list[dict]:
    """[(a, b, category, acceptable levels)] built from real first names."""
    firsts = R.take([{"id": n} for n in _first_names()], split)
    firsts = [x["id"] for x in firsts]
    rng = random.Random(f"names-{split}")
    U = str.upper
    out = []

    def add(cat, a, b, ok):
        out.append({"a": a, "b": b, "category": cat, "acceptable": ok})
    for _ in range(n_each):
        f, m, f2 = rng.choice(firsts), rng.choice(firsts), rng.choice(firsts)
        s, s2 = rng.sample(SURNAMES, 2)
        add("exact_case", U(f"{f} {m} {s}"), f"{f} {m} {s}", {"match"})
        add("honorific", U(f"{f} {s}"), f"{rng.choice(HONOR)} {f} {s}", {"match"})
        add("initials", U(f"{f} {m} {s}"), U(rng.choice([f"{f[0]}. {m[0]}. {s}", f"{f[0]} {m[0]} {s}", f"{f} {m[0]}. {s}"])), {"match"})
        add("middle_omitted", U(f"{f} {m} {s}"), U(f"{f} {s}"), {"match"})
        add("surname_first", U(f"{f} {m} {s}"), U(rng.choice([f"{s} {f} {m}", f"{s}, {f} {m}"])), {"match"})
        add("punctuation_spacing", U(f"{f} {m} {s}"), U(f"{f}  {m}.{s}").replace(".", ". "), {"match"})
        add("married_surname", U(f"{f} {s}"), U(f"{f} {s2}"), {"partial", "mismatch"})
        v1, v2 = rng.choice(VARIANTS)
        add("spelling_variant", U(f"{v1} {m} {s}"), U(f"{v2} {m} {s}"), {"match", "partial"})
        add("different_person", U(f"{f} {s}"), U(f"{f2} {s2}"), {"mismatch"})
        add("same_surname_other_person", U(f"{f} {s}"), U(f"{f2} {s}"), {"mismatch"})
        # Western-India convention: given name + FATHER's name + surname. The father's own document
        # carries "<father> <surname>" — it is not the applicant's.
        add("fathers_document", U(f"{f} {m} {s}"), U(f"{m} {s}"), {"partial", "mismatch"})
    return out


def _js_names(pairs) -> list[str] | None:
    tmp = Path(tempfile.mkdtemp(dir=str(R.DATA_ROOT / "work"))) / "corpus.json"
    tmp.write_text(json.dumps({"validate": [], "identity": [], "names": [[p["a"], p["b"]] for p in pairs],
                               "cross": [], "dates": []}), encoding="utf-8")
    proc = subprocess.run(["node", str(C.EVAL_DIR / "js_parity.mjs"), str(tmp)], capture_output=True, text=True,
                          encoding="utf-8", cwd=str(C.API_DIR))
    tmp.unlink(missing_ok=True)
    return json.loads(proc.stdout)["names"] if proc.returncode == 0 else None


def eval_cross_verification(split: str = "test") -> dict:
    from app.docrules import compare_names
    pairs = name_pairs(split)
    got = [compare_names(p["a"], p["b"])["level"] for p in pairs]
    js = _js_names(pairs)
    per = {}
    for p, g in zip(pairs, got):
        b = per.setdefault(p["category"], {"n": 0, "ok": 0, "levels": {}})
        b["n"] += 1
        b["ok"] += g in p["acceptable"]
        b["levels"][g] = b["levels"].get(g, 0) + 1
    # names read off PAN cards by the extraction model (from the extraction run's cache)
    from eval.real.intake_real import extraction_cached, pan_items
    levels = [g.get("name_level") for it in pan_items(split, 150)
              if (g := extraction_cached("pan", split, it["id"])) and g.get("name_level")]
    metrics = {"name_pair_accuracy": R.wilson(sum(g in p["acceptable"] for p, g in zip(pairs, got)), len(pairs)),
               "js_parity": R.wilson(sum(a == b for a, b in zip(got, js)), len(pairs)) if js else None,
               "extracted_name_match": R.wilson(sum(lv == "match" for lv in levels), len(levels)) if levels else None,
               "per_category": {k: {"accuracy": round(v["ok"] / v["n"], 4), "levels": v["levels"]} for k, v in per.items()},
               "n_pairs": len(pairs)}
    data = {"name": "real Indian first names (Kaggle list, split by name) + common surnames, labelled variant pairs; "
                    "PAN-card names as read by the extraction model",
            "split": split, "acceptable_levels": {c: sorted(next(p["acceptable"] for p in pairs if p["category"] == c))
                                                  for c in per}}
    return R.save("cross_verification", split, metrics, data)


# ── policy check ────────────────────────────────────────────────────────────
_OPS = {"eq": operator.eq, "ne": operator.ne, "gt": operator.gt, "gte": operator.ge, "lt": operator.lt,
        "lte": operator.le, "in": lambda a, b: a in b, "not_in": lambda a, b: a not in b, "contains": lambda a, b: b in a}


def _get(ctx, dotted):
    cur = ctx
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _arith(expr: str, ctx):
    """Independent arithmetic: + - * / over dotted fields and numbers; None when a field is missing."""
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.BinOp):
            a, b = ev(n.left), ev(n.right)
            if a is None or b is None:
                return None
            if isinstance(n.op, ast.Div) and b == 0:
                return None
            return {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}[type(n.op)](a, b)
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, (ast.Attribute, ast.Name)):
            parts = []
            while isinstance(n, ast.Attribute):
                parts.append(n.attr)
                n = n.value
            parts.append(n.id)
            v = _get(ctx, ".".join(reversed(parts)))
            return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        raise ValueError("unsupported")
    try:
        return ev(ast.parse(expr, mode="eval"))
    except (ValueError, KeyError, SyntaxError):
        return None


def _cond(node, ctx):
    if "all" in node:
        r = [_cond(c, ctx) for c in node["all"]]
        return False if False in r else (None if None in r else True)
    if "any" in node:
        r = [_cond(c, ctx) for c in node["any"]]
        return True if True in r else (None if None in r else False)
    if "not" in node:
        r = _cond(node["not"], ctx)
        return None if r is None else not r
    op = node.get("op")
    if op in ("exists", "not_exists"):
        present = _get(ctx, node["field"]) is not None
        return present if op == "exists" else not present
    left = _arith(node["expr"], ctx) if "expr" in node else _get(ctx, node["field"])
    if left is None or op not in _OPS:
        return None
    try:
        return bool(_OPS[op](left, node.get("value")))
    except TypeError:
        return None


def independent_status(rule, ctx) -> str:
    if rule.applies_when is not None and _cond(rule.applies_when, ctx) is False:
        return "NOT_APPLICABLE"
    if any(_get(ctx, f) is None for f in rule.required_fields):
        return "INCONCLUSIVE"
    r = _cond(rule.condition, ctx)
    if r is None:
        return "INCONCLUSIVE"
    return "PASS" if r else ("FAIL" if rule.rule_type == "mandatory" else "WARNING")


def _fields_of(node) -> list[str]:
    import re
    if any(k in node for k in ("all", "any")):
        return [f for c in node.get("all", node.get("any", [])) for f in _fields_of(c)]
    if "not" in node:
        return _fields_of(node["not"])
    if "expr" in node:
        return re.findall(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", node["expr"])
    return [node["field"]]


def _loan_terms(key: str) -> dict:
    """Exercises every digital-lending rule both ways (the L&T data has no KFS / consent fields)."""
    rng = random.Random(key)
    pick = lambda: rng.choice([True, True, True, False, None])  # noqa: E731
    t = {"kfs_provided": pick(), "apr_disclosed": pick(), "all_fees_in_kfs": pick(),
         "disbursal_to_borrower_account": pick(), "penal_charge_is_interest": rng.choice([False, False, True, None]),
         "cooling_off_period_days": rng.choice([0, 1, 3, None])}
    return {k: v for k, v in t.items() if v is not None}


def eval_policy_check(split: str = "test", n: int = 2000) -> dict:
    from app.agents.tools import policy_context
    from app.compliance import db as dbm
    from app.compliance.rules_engine import evaluate_application
    from eval.real.risk_real import lt_inputs, lt_rows
    # 1) the rules the live app evaluates (read-only): are they well formed?
    live = []
    try:
        conn = dbm.get_db()
        live = dbm.load_rules(conn, active_only=True)
        conn.close()
    except Exception:  # noqa: BLE001
        pass
    bad = [r.rule_id for r in live if not (r.regulation or "").strip() or len((r.rule_name or "").strip()) < 8
           or r.rule_name.strip().lower() == r.rule_id.strip().lower()]
    # 2) the engine on real inputs vs the independent implementation (seeded rules + the live ones)
    C.fresh_compliance_db()
    conn = dbm.get_db()
    seeded = dbm.load_rules(conn, active_only=True)
    conn.close()
    rules = {r.rule_id: r for r in seeded}
    for r in live:
        rules.setdefault(f"live:{r.rule_id}", r)
    rules = list(rules.values())
    df = lt_rows()
    df = df[[R.split_of(i) == split for i in df["id"]]]
    rows = R.sample([{"id": i} for i in df["id"]], n)
    by_id = df.set_index("id")
    rng = random.Random(R.SEED)
    agree = total = false_pass = overall_ok = 0
    disagreements, status_count, credit_default = [], {}, {}
    for k, row in enumerate(rows):
        r = by_id.loc[row["id"]]
        case, extra = lt_inputs(r)
        for dt in case["documents"]:              # FinSight would have read the number off the card
            case["documents"][dt]["parsed"] = {"pan_number": _pan(rng)} if dt == "pan" else {"aadhaar_number": "XXXX-XXXX-1234"}
        case["case_id"] = f"LT-{row['id']}"
        for variant in ("as_applied", "with_offer_terms"):
            ex = dict(extra)
            if variant == "with_offer_terms":
                ex["stage"] = "offer"                 # the loan-terms rules apply from the offer stage
                ex["loan_terms"] = _loan_terms(row["id"])
                ex["consent"] = {"data_collection": True, "credit_bureau_pull": rng.random() < 0.9}
                ex["monthly_income"] = float(rng.choice([15000, 25000, 40000, 60000]))
                ex["proposed_emi"] = round(ex["loan_amount"] / 24, 0)
            ctx = policy_context.build_context(case, ex, None)
            res = evaluate_application(rules, ctx)
            mine = {}
            for rule, chk in zip(rules, res["checks"]):
                ind = independent_status(rule, ctx)
                mine[rule.rule_id] = ind
                total += 1
                agree += ind == chk["status"]
                status_count[chk["status"]] = status_count.get(chk["status"], 0) + 1
                if chk["status"] == "PASS" and any(_get(ctx, f) is None for f in _fields_of(rule.condition)):
                    false_pass += 1
                if ind != chk["status"] and len(disagreements) < 10:
                    disagreements.append({"rule": rule.rule_id, "engine": chk["status"], "independent": ind})
            st = list(mine.values())
            graded = [s for s in st if s != "NOT_APPLICABLE"]
            mand_inc = any(s == "INCONCLUSIVE" and rl.rule_type == "mandatory" for rl, s in zip(rules, st))
            exp_overall = ("FAIL" if "FAIL" in graded else "NEEDS_INFO" if mand_inc else
                           "PASS_WITH_WARNINGS" if "WARNING" in graded else "PASS")
            overall_ok += exp_overall == res["overall_status"]
            if variant == "as_applied":
                cs = next((c["status"] for c in res["checks"] if c["rule_id"] == "POL_CREDIT_001"), None)
                b = credit_default.setdefault(cs, [0, 0])
                b[0] += int(r["label"])
                b[1] += 1
    n_ctx = 2 * len(rows)
    metrics = {"rule_accuracy": R.wilson(agree, total), "overall_status_accuracy": R.wilson(overall_ok, n_ctx),
               "no_false_pass": 1.0 if false_pass == 0 else round(1 - false_pass / total, 6), "false_passes": false_pass,
               "rules_well_formed": R.wilson(len(live) - len(bad), len(live)) if live else None,
               "malformed_live_rules": bad, "status_counts": status_count,
               "default_rate_by_credit_rule": {str(k): {"default_rate": round(v[0] / v[1], 4), "n": v[1]}
                                               for k, v in credit_default.items()}}
    data = {"name": "L&T vehicle loans (real inputs: bureau score, EMIs, documents) through build_context + the rules "
                    "engine; rules = the seeded set + the live knowledge base's active rules",
            "split": split, "n_loans": len(rows), "n_contexts": n_ctx, "n_rules": len(rules),
            "variants": {"as_applied": "assessment stage, only what L&T records (no consent or income; the "
                                       "loan-terms rules are not applicable yet)",
                         "with_offer_terms": "offer stage, plus deterministic offer terms / consent / income, so every rule is "
                                             "exercised both ways (labelled as constructed, not real)"}}
    return R.save("policy_check", split, metrics, data, disagreements=disagreements)
