"""Bank-statement transaction parser.

The extraction prompt only returns the statement SUMMARY (opening/closing
balance, totals), so transaction-level fraud patterns were impossible. This
parser recovers the rows deterministically:

  born-digital PDF  → the PDF text layer (pypdfium2), line by line
  scan / photo      → RapidOCR word boxes grouped into lines by position

A row STARTS at a line that begins with a date (optionally after a serial number and a
transaction id) and ENDS at the first line that carries its amounts; the LAST amount is
the running balance, and debit vs credit is decided by how the balance moved (never by
column position, which varies by bank).

Why rows are assembled from several lines (measured on public Indian statement layouts,
2026-10-01: the one-line rule recovered 4% of 15,399 rows): banks print a row over 2-5 text
lines — transaction date and time, value date and narration, a wrapped narration, then
cheque number, amount and balance — and many start it with a serial number. Statements
also run to many pages, and some list the newest transaction first.
"""
from __future__ import annotations

import re

_DATE_TOKEN = r"(?:\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}[\s\-][A-Za-z]{3}[\s\-]\d{2,4})"
# a row start: [serial no] [transaction id] date ...
_DATE = re.compile(rf"^(?:\d{{1,4}}\s+)?(?:[A-Z]{{0,4}}\d{{5,16}}\s+)?({_DATE_TOKEN})\b")
_ANY_DATE = re.compile(_DATE_TOKEN)
_TIME = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AP]M)?\b", re.I)
MAX_PAGES_TEXT = 40       # born-digital pages cost ~2 ms each
MAX_PAGES_OCR = 6         # scanned pages cost ~4 s each (OCR)
# Any digit run with ',' or '.' group separators that ends in '.dd'. OCR on a
# photo misreads the separators inside Indian amounts ('1,48978.00',
# '1,47.715.00'), so the grouping is not trusted — only the final decimal;
# a clipped last digit ('3,33,532.0') is still an amount.
_AMT = re.compile(r"(?<![\w/])-?\d[\d,.]*\.\d{1,2}(?![\d])")


def _amount(s: str) -> float:
    head, _, dec = s.rpartition(".")
    neg = head.startswith("-")
    v = float(re.sub(r"\D", "", head) + "." + dec)
    return -v if neg else v


def _tol(v: float) -> float:
    return max(1.0, 0.005 * abs(v))


def _lines_from_pdf(data: bytes) -> list[str]:
    import pypdfium2 as pdfium
    from ...pdfium_lock import PDFIUM_LOCK
    out = []
    with PDFIUM_LOCK:                               # PDFium is not thread-safe
        pdf = pdfium.PdfDocument(data)
        try:
            for i in range(min(len(pdf), MAX_PAGES_TEXT)):
                text = pdf[i].get_textpage().get_text_bounded() or ""
                out += [ln.strip() for ln in text.splitlines() if ln.strip()]
        finally:
            pdf.close()
    return out


def _lines_from_image(data: bytes) -> list[str]:
    """OCR word quads → text lines. A photographed page is tilted (and under
    perspective the tilt changes down the page), so grouping words by raw y
    splices the date of one row onto the balance of the row above (measured:
    66% of photo rows recovered). Instead the text slope is fitted as a linear
    function of y from the quads' own top edges, and words are grouped by
    their tilt-corrected row position."""
    import io
    import numpy as np
    from PIL import Image
    from ...imaging import load_image
    from ...preprocess import _get_ocr_reader
    if data[:5] == b"%PDF-":
        pages = [load_image(data, max_dim=2000)]
        try:                                   # a scanned statement is rarely one page
            from ...utils import render_pdf_to_images
            rest = render_pdf_to_images(data, max_pages=MAX_PAGES_OCR, max_dimension=2000)[1:]
            pages += [Image.open(io.BytesIO(b)).convert("RGB") for b in rest]
        except Exception:  # noqa: BLE001 - page 1 alone is still useful
            pass
        out = []
        for im in pages:
            out += _lines_of_page(im)
        return out
    return _lines_of_page(Image.open(io.BytesIO(data)).convert("RGB"))


def _lines_of_page(im) -> list[str]:
    import numpy as np
    from ...preprocess import _get_ocr_reader
    raw = _get_ocr_reader(("en",)).readtext(np.array(im))
    toks = []
    for pts, text, _c in raw:
        p = np.asarray(pts, dtype=float)
        cx, cy = p[:, 0].mean(), p[:, 1].mean()
        w = np.hypot(*(p[1] - p[0]))
        h = max(4.0, np.hypot(*(p[3] - p[0])))
        slope = (p[1][1] - p[0][1]) / (p[1][0] - p[0][0]) if abs(p[1][0] - p[0][0]) > 1 else 0.0
        toks.append({"text": text, "cx": cx, "cy": cy, "h": h, "long": w > 3 * h, "slope": slope,
                     "x0": p[:, 0].min(), "x1": p[:, 0].max()})
    if not toks:
        return []
    long_ = [t for t in toks if t["long"] and abs(t["slope"]) < 0.2]
    if len(long_) >= 4:
        a, b = np.polyfit([t["cy"] for t in long_], [t["slope"] for t in long_], 1)
    else:
        a, b = 0.0, float(np.median([t["slope"] for t in long_])) if long_ else 0.0
    for t in toks:
        t["row"] = t["cy"] - (a * t["cy"] + b) * t["cx"]      # where this word's line crosses x = 0
    toks.sort(key=lambda t: (t["row"], t["cx"]))
    med_h = float(np.median([t["h"] for t in toks]))
    lines, cur = [], []
    for t in toks:
        if cur and t["row"] - np.mean([u["row"] for u in cur]) > 0.5 * med_h:
            lines.append(_join(cur, med_h))
            cur = []
        cur.append(t)
    if cur:
        lines.append(_join(cur, med_h))
    return lines


_FRAG_L = re.compile(r"[\d,]$")
_FRAG_R = re.compile(r"^[\d,.]")


def _join(words: list[dict], h: float) -> str:
    """Words of one line, left to right. OCR often splits an Indian-format
    amount into fragments ('1,' + '48,978.00', '26,89' + '5.00'); fragments
    that touch (gap under half a text height) and look like parts of one
    number are glued back together, or the balance is misread."""
    words = sorted(words, key=lambda u: u["x0"])
    out = words[0]["text"]
    for prev, w in zip(words, words[1:]):
        glue = (_FRAG_L.search(prev["text"]) and _FRAG_R.search(w["text"])
                and w["x0"] - prev["x1"] < 0.5 * h and not _AMT.fullmatch(prev["text"].strip()))
        out += ("" if glue else " ") + w["text"]
    return out


# totals a statement prints itself ("Total Credits 4,50,000.00") - read, not added up
_TOTAL_CR = re.compile(r"total\s*(?:credits?|deposits?)\b[^\d\-]{0,15}(-?\d[\d,.]*\.\d{2})(?!\d)", re.I)
_TOTAL_DR = re.compile(r"total\s*(?:debits?|withdrawals?)\b[^\d\-]{0,15}(-?\d[\d,.]*\.\d{2})(?!\d)", re.I)
_OPENING = re.compile(r"opening\s*balance[^\d\-]{0,12}(-?\d[\d,.]*\.\d{2})(?!\d)", re.I)
_CLOSING = re.compile(r"closing\s*balance[^\d\-]{0,12}(-?\d[\d,.]*\.\d{2})(?!\d)", re.I)


_SIDE = re.compile(r"(?<![A-Za-z])(CR|DR)(?![A-Za-z])")       # upper case only: "Dr Sharma" is a person
# What the narration says, for a row whose balance does not follow from the row above and that has no
# printed CR / DR marker (statements with separate debit / credit columns): the balance movement says
# nothing reliable about such a row. Used only when exactly one of the two matches.
_DEBIT_CUE = re.compile(r"withdraw|\bdebit|\bwdl\b|\batm\b|\bpos\b|purchase|\bemi\b|chq\s*paid"
                        r"|\b(?:neft|imps|rtgs|upi)\W{0,2}dr\b", re.I)
_CREDIT_CUE = re.compile(r"deposit|\bcredit|salary|refund|\binterest\b"
                         r"|\b(?:neft|imps|rtgs|upi)\W{0,2}cr\b", re.I)


def _narration_side(description: str) -> str | None:
    d, c = bool(_DEBIT_CUE.search(description)), bool(_CREDIT_CUE.search(description))
    return None if d == c else ("debit" if d else "credit")


_NOT_A_ROW = re.compile(r"(opening|closing)\s*balance|\btotal\b|brought\s*forward|carried\s*forward", re.I)


def _blocks(lines: list[str], max_rows: int) -> list[dict]:
    """Assemble rows: a block opens at a row-start line and closes at the first line that carries
    amounts. A second date line before any amount (value date under transaction date) belongs to the
    same row; lines after the amounts (wrapped narration, page footers) are ignored until the next
    row start, so a footer's "Closing Balance" can never be taken for a row's balance."""
    raw, cur, done = [], None, True
    for ln in lines:
        m = _DATE.match(ln)
        if m and (done or cur is None):
            cur, done = {"date": m.group(1), "text": []}, False
            body = ln[m.end():]
        elif done or cur is None:
            continue
        else:
            body = ln
        clean = _TIME.sub(" ", _ANY_DATE.sub(" ", body))
        amts = [(a, _amount(a)) for a in _AMT.findall(clean)]
        for a, _ in amts:
            clean = clean.replace(a, " ")
        cur["text"].append(clean)
        if amts and _NOT_A_ROW.search(clean):   # "01/04/2023 ... Opening Balance 12,345.00" is a summary line
            done = True
        elif amts:
            side = _SIDE.findall(clean)            # a printed CR / DR marker beside the amounts (the last one)
            raw.append({"date": cur["date"], "description": re.sub(r"\s+", " ", " ".join(cur["text"])).strip()[:60],
                        "txn": amts[-2][1] if len(amts) >= 2 else None, "balance": amts[-1][1],
                        "side": ("credit" if side[-1] == "CR" else "debit") if side else None})
            done = True
            if len(raw) >= max_rows:
                break
        elif len(cur["text"]) > 6:              # a date with no amounts in sight: not a transaction
            done = True
    return raw


def _when(s: str):
    from datetime import datetime
    s = re.sub(r"\s+", " ", s.strip())
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d %b %Y", "%d-%b-%Y", "%d/%m/%y", "%d-%m-%y",
                "%d-%b-%y", "%d %b %y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _newest_last(raw: list[dict]) -> list[dict]:
    """Balances reconcile only in time order; some banks print the newest transaction first."""
    ds = [d for d in (_when(r["date"]) for r in raw) if d]
    down = sum(1 for a, b in zip(ds, ds[1:]) if b < a)
    up = sum(1 for a, b in zip(ds, ds[1:]) if b > a)
    return raw[::-1] if down > up else raw


def parse(data: bytes, opening_balance: float | None = None, max_rows: int = 2000) -> dict:
    """{transactions: [{date, description, debit, credit, balance}], source, lines,
    opening_balance}. Never raises; an unreadable statement gives no rows."""
    lines, source = [], "none"
    try:
        if data[:5] == b"%PDF-":
            lines = _lines_from_pdf(data)
            source = "pdf_text"
        if sum(1 for ln in lines if _DATE.match(ln)) < 2:
            lines = _lines_from_image(data)
            source = "ocr"
    except Exception as exc:  # noqa: BLE001
        return {"transactions": [], "source": f"error: {type(exc).__name__}", "lines": 0,
                "opening_balance": opening_balance, "closing_balance_printed": None,
                "closing_matches": False, "reconciled_share": 0.0}
    if opening_balance is None:
        for ln in lines:
            m = _OPENING.search(ln)
            if m:
                opening_balance = _amount(m.group(1))
                break
    raw = _newest_last(_blocks(lines, max_rows))
    rows = _reconcile(raw, opening_balance, exact=(source == "pdf_text"))
    closing = None
    for ln in lines:
        m = _CLOSING.search(ln)
        if m:
            closing = _amount(m.group(1))
    ok = sum(1 for r in rows if r["reconciled"])
    return {"transactions": rows, "source": source, "lines": len(lines), "opening_balance": opening_balance,
            "closing_balance_printed": closing,
            "totals_printed": _printed_totals(lines),
            "closing_matches": (closing is not None and bool(rows) and abs(rows[-1]["balance"] - closing) <= _tol(closing)),
            "reconciled_share": round(ok / len(rows), 4) if rows else 0.0}


def _printed_totals(lines: list[str]):
    """(total credits, total debits) as the statement prints them, or None unless both are there."""
    got = [next((_amount(m.group(1)) for ln in lines for m in [rx.search(ln)] if m), None)
           for rx in (_TOTAL_CR, _TOTAL_DR)]
    return tuple(got) if None not in got else None


def _last_page(data: bytes):
    """(page count, last page as an image) of a PDF."""
    import pypdfium2 as pdfium
    from ...pdfium_lock import PDFIUM_LOCK
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            n = len(pdf)
            page = pdf[n - 1]
            w, h = page.get_size()
            return n, page.render(scale=min(2.0, 2000 / max(w, h))).to_pil().convert("RGB")
        finally:
            pdf.close()


def at_upload(data: bytes) -> tuple[dict, bool]:
    """What the upload request itself learns about a statement PDF, and whether its rows still have to
    be read afterwards. A text layer is parsed in full (milliseconds). A scan is not: reading six pages
    by OCR took four minutes on the request path (measured on real scanned statements, 2026-10-02), so
    only its page count and the totals printed on its last page are looked at here; the rows are read
    after the response (document_flows._parse_statement_later)."""
    try:
        lines = _lines_from_pdf(data)
    except Exception:  # noqa: BLE001
        lines = []
    if sum(1 for ln in lines if _DATE.match(ln)) >= 2:
        return parse(data), False
    pages, printed = 1, None
    try:
        pages, last = _last_page(data)
        printed = _printed_totals(_lines_of_page(last))
    except Exception:  # noqa: BLE001 - nothing learned; the rows are still read afterwards
        pass
    return ({"transactions": [], "source": "scan", "lines": 0, "opening_balance": None,
             "closing_balance_printed": None, "closing_matches": False, "reconciled_share": 0.0,
             "totals_printed": printed, "pages": pages}, True)


def _reconcile(raw: list[dict], opening: float | None, exact: bool = False) -> list[dict]:
    """Debit vs credit from the balance movement, cross-checked against the
    printed amount. Every row carries `reconciled` (its amount and balances agree).

    When the two disagree it depends on where the numbers came from:
      OCR (a photo / scan)   a digit was probably misread. The NEXT row decides which number: if
                             prev +/- amount makes the next row add up, the printed balance was wrong
                             and is corrected; otherwise the balance movement is trusted.
      exact (PDF text layer) nothing was misread - the statement itself does not add up at this row
                             (rows printed out of balance order, or an edited figure). The printed
                             amount and balance are kept as printed and the row is left unreconciled,
                             so it can be shown to a reviewer but never raises a pattern flag alone.
    A printed CR / DR marker decides the side when present; otherwise the balance movement does."""
    rows, prev = [], opening
    for i, r in enumerate(raw):
        bal, txn, side = r["balance"], r["txn"], r.get("side")
        debit = credit = 0.0
        reconciled = False
        if prev is None:
            if txn is not None:
                is_cr = side == "credit" or (side is None and any(
                    k in r["description"].upper() for k in ("CR", "CREDIT", "SALARY", "NEFT IN", "DEP")))
                credit, debit = (txn, 0.0) if is_cr else (0.0, txn)
        else:
            delta = round(bal - prev, 2)
            if txn is None or abs(abs(delta) - txn) <= _tol(txn):
                amount = abs(delta) if txn is None else txn
                reconciled = txn is not None
            elif exact:
                amount = txn                         # printed figures stand; the row does not reconcile
            else:
                nxt = raw[i + 1] if i + 1 < len(raw) else None
                fixed = None
                if nxt is not None and nxt["txn"] is not None:
                    for cand in (prev - txn, prev + txn):
                        if abs(abs(nxt["balance"] - cand) - nxt["txn"]) <= _tol(nxt["txn"]):
                            fixed = cand
                            break
                if fixed is not None:            # the printed balance was misread
                    bal, delta, amount, reconciled = round(fixed, 2), round(fixed - prev, 2), txn, True
                else:                            # trust the balance movement
                    amount = abs(delta)
            # a reconciled row's side is certain from the balance; the marker decides only when it is not
            if not reconciled and not side and exact:
                side = _narration_side(r["description"])
            is_cr = (side == "credit") if (side and not reconciled) else delta >= 0
            if is_cr:
                credit = amount
            else:
                debit = amount
        rows.append({"date": r["date"], "description": r["description"], "debit": round(debit, 2),
                     "credit": round(credit, 2), "balance": bal, "reconciled": reconciled})
        prev = bal
    return rows


def summary(txn: dict | None) -> dict | None:
    """Total credits / debits of the statement, added up from its rows. The reading model cannot add up
    a statement (measured on 60 real-layout statements: no total within 0.5%), so the totals come from
    the rows - but only when the rows are known to be the whole statement: they start from a printed
    opening balance and end on the printed closing balance. Otherwise None (pages missing or misread)."""
    rows = (txn or {}).get("transactions") or []
    if len(rows) < 3 or txn.get("opening_balance") is None or not txn.get("closing_matches"):
        return None
    return {"total_credits": round(sum(r["credit"] for r in rows), 2),
            "total_debits": round(sum(r["debit"] for r in rows), 2),
            "rows": len(rows), "rows_not_reconciled": sum(1 for r in rows if not r["reconciled"]),
            "source": txn.get("source")}


MODEL_SUM_ROWS = 10        # from this many rows (or two pages) on, a total the model "read" is its own arithmetic


def apply_summary(parsed: dict | None, txn: dict | None) -> dict | None:
    """Statement totals from the statement itself, never from the reading model's arithmetic.

    The prompt asks for total credits / debits. A statement that prints them is read; most do not, and
    then the model adds the rows up - wrongly (measured on 60 real-layout statements of ~160 rows: no
    total within 0.5%). A made-up total is worse than none: the tampering rule "opening + credits -
    debits = closing" then fails on a GENUINE statement, and income analysis divides it into a monthly
    income. So, in order:
      1. totals printed on the statement                         -> those;
      2. rows that run from the opening to the closing balance   -> their sums (see summary());
      3. otherwise the model's totals stay only if they agree with its own opening and closing balance;
         on a statement of MODEL_SUM_ROWS rows or more (or of several pages, when the rows are not
         read yet) that they do not agree with, they are removed.
    Returns what was done, for the trace, or None when nothing was changed."""
    if not isinstance(parsed, dict) or not txn:
        return None
    if txn.get("totals_printed"):
        parsed["total_credits"], parsed["total_debits"] = txn["totals_printed"]
        return {"totals_from": "printed on the statement"}
    tot = summary(txn)
    if tot is not None:
        parsed["total_credits"], parsed["total_debits"] = tot["total_credits"], tot["total_debits"]
        return {"totals_from": "transaction rows", "rows": tot["rows"], "rows_not_reconciled": tot["rows_not_reconciled"]}
    if len(txn.get("transactions") or []) < MODEL_SUM_ROWS and (txn.get("pages") or 1) < 2:
        return None

    def num(k):
        try:
            return float(str(parsed.get(k)).replace(",", ""))
        except (TypeError, ValueError):
            return None
    o, c, cr, dr = num("opening_balance"), num("closing_balance"), num("total_credits"), num("total_debits")
    if cr is None and dr is None:
        return None
    if None not in (o, c, cr, dr) and abs(o + cr - dr - c) <= max(1.0, 0.01 * abs(c)):
        return None                              # consistent with its own balances: plausible, kept
    parsed["total_credits"] = parsed["total_debits"] = None
    return {"totals_from": None, "removed": "not printed on the statement; the rows could not all be read "
                                            "and the reading model's own sums did not add up"}


def parse_best(original: bytes, preprocessed: bytes | None = None) -> dict:
    """Statement rows from the upload. A born-digital PDF is read from its text
    layer; a photo is read from the preprocessed page and — if that read does
    not fully reconcile — from the original photo too, keeping whichever
    reconciles more rows (preprocessing can crop or skew a full page)."""
    if original[:5] == b"%PDF-" or not preprocessed or preprocessed is original:
        return parse(original)
    first = parse(preprocessed)
    if first["reconciled_share"] >= 0.9 and len(first["transactions"]) >= 3:
        return first
    second = parse(original)
    score = lambda r: sum(1 for t in r["transactions"] if t["reconciled"])  # noqa: E731
    best = second if score(second) > score(first) else first
    best["read_from"] = "original" if best is second else "preprocessed"
    return best
