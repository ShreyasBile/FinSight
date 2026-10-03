"""Fixes that came out of the real-data evaluation (2026-10-01/02), pinned so they stay fixed:

* tampering fusion counts a pixel signal only above what genuine real documents reach;
* PAN / Aadhaar printed inside a longer OCR line are masked, with OCR's glued or unevenly spaced text;
* the statement parser assembles rows printed over several lines, with a serial-number prefix.
"""
from app import mask as M
from app.agents.tools import transactions as T
from app.tampering import fusion
from app.tampering.signals.base import SignalResult


def sig(**scores):
    return [SignalResult(k, True, v) for k, v in scores.items()]


# ── tampering fusion ────────────────────────────────────────────────────────
def test_scores_genuine_documents_reach_are_no_evidence():
    assert fusion.calibrated("cnn", 0.85, "page") == 0.0            # where genuine scans sit
    assert fusion.calibrated("cnn", 1.0, "page") == 1.0
    assert 0 < fusion.calibrated("noise", 0.9, "id") < 1
    assert fusion.calibrated("business_rules", 0.4, "page") == 0.4  # deterministic signals are not floored
    assert fusion.family_of("pan") == "id" and fusion.family_of("bank_statement") == "page"


def test_a_typical_genuine_scan_is_low_but_real_evidence_still_decides():
    genuine = fusion.fuse(sig(metadata=0.15, cnn=0.88, noise=0.6, ela=0.7, copy_move=0.9), "vehicle_quotation")
    assert genuine["risk_tier"] == "LOW"
    assert genuine["contributions"]["cnn"]["raw_score"] == 0.88     # what the signal reported is kept
    editor = fusion.fuse(sig(metadata=0.9, cnn=0.5, noise=0.2, ela=0.2, copy_move=0.1), "salary_slip")
    assert editor["risk_tier"] == "MEDIUM"                          # an editor tag alone: a look, not HIGH
    rule = fusion.fuse(sig(metadata=0.15, business_rules=1.0, cnn=0.3), "salary_slip")
    assert rule["risk_tier"] == "HIGH"                              # a failed deterministic rule decides
    both = fusion.fuse(sig(metadata=0.9, pdf_forensics=0.85), "salary_slip")
    assert both["risk_tier"] == "HIGH"                              # two independent non-pixel signals


def test_a_lone_ela_or_noise_hit_is_not_evidence_but_a_backed_one_is():
    quiet = dict(metadata=0.15, copy_move=0.1, cnn=0.5)                # the other signals of a real run
    # saturated on an ordinary scan and nothing else: as common on genuine documents as on forged ones
    assert fusion.fuse(sig(**quiet, ela=1.0, noise=0.3), "salary_slip")["risk_tier"] == "LOW"
    assert fusion.fuse(sig(**quiet, ela=0.2, noise=1.0), "pan")["risk_tier"] == "LOW"
    # backed by another signal it counts in full: two pixel signals, or a pixel signal and an editor tag
    assert fusion.fuse(sig(**quiet, ela=1.0, noise=1.0), "salary_slip")["risk_tier"] == "HIGH"
    assert fusion.fuse(sig(**{**quiet, "metadata": 0.9}, ela=1.0, noise=0.3), "salary_slip")["risk_tier"] == "HIGH"
    # copy-move still stands alone
    assert fusion.fuse(sig(**{**quiet, "copy_move": 1.0}, ela=0.2, noise=0.3), "salary_slip")["risk_tier"] == "MEDIUM"


# ── PII masking ─────────────────────────────────────────────────────────────
BOX = (100, 50, 500, 70)


def found(text):
    return [(label, n) for _, label, n in M._inline_ids({"text": text, "box": BOX})]


def test_ids_inside_a_line_are_found_as_ocr_returns_them():
    assert found("PAN: ABCPE1234F") == [("PAN", 6)]
    assert found("PermanentAccountNumberODRPY5239Y") == [("PAN", 6)]       # OCR dropped the spaces
    assert found("PANJCXPI8184B") == [("PAN", 6)]
    assert found("Aadhaar No: 3422 5282 8721") == [("Aadhaar", 8)]
    assert found("UID: 25518287 7646") == [("Aadhaar", 8)]                 # uneven spacing
    assert found("Aadhaar988935832534") == [("Aadhaar", 8)]
    assert found("Emp: R Sharma  PAN ABCPE1234F  UID 3422 5282 8721") == [("PAN", 6), ("Aadhaar", 8)]


def test_an_aadhaar_split_by_ocr_after_its_first_group_is_still_masked():
    def tok(text, x0, x1):
        return {"text": text, "alnum": M._norm_alnum(text), "value": None, "box": (x0, 50, x1, 70)}
    for parts in (["Aadhaar Number 8482", "8464", "4904"], ["Aadhaar Number 8482", "84644904"],
                  ["UID: 8482 8464", "4904"]):
        toks, x = [], 100
        for p in parts:
            toks.append(tok(p, x, x + 12 * len(p)))
            x += 12 * len(p) + 8
        regs = M._find_regions(toks, {}, "salary_slip")
        assert regs and all(lb == "Aadhaar" for _b, lb, _n in regs) and sum(n for _b, _lb, n in regs) == 8, parts
        assert max(b[2] for b, _lb, _n in regs) <= toks[-1]["box"][2] - 12 * 4 + 2, parts     # the last four stay
    # a date or an amount at the end of an Aadhaar line is not a number group
    assert M._find_regions([tok("Aadhaar seeded on 12/05/2021", 100, 440), tok("4904", 450, 500)], {}, "salary_slip") == []


def test_a_misread_pan_glued_to_its_label_is_still_found():
    assert found("PermanentAccountNumberUNYPU56501") == [("PAN", 6)]      # final I read as 1, no space
    assert found("PAN No. UTHPA98860") == [("PAN", 6)]                    # final O read as 0
    assert found("PAN:ABCPE1234F") == [("PAN", 6)]                        # still one region, not two
    assert found("Account Number 1234567890") == []                       # ten digits are not a PAN


def test_other_numbers_are_left_alone():
    for text in ("VID 1234 5678 9012 3456", "A/c 123456789012", "Ref UPI/123456789012/NEFT", "Invoice No 12345678 9012 3456",
                 "Order INVOICE1234Z ready", "Total 1,23,456.00"):
        assert found(text) == [], text


def test_a_pan_with_a_lookalike_character_is_still_a_pan():
    assert M._pan_shaped("ABCPE123OF") and M._pan_shaped("A8CPE1234F")     # O for 0, 8 for B
    assert not M._pan_shaped("MH12AB1234") and not M._pan_shaped("ABCDEFGHIJ")
    box, label, n_x = M._find_regions([{"text": "ABCPE123OF", "alnum": "ABCPE123OF", "box": BOX, "value": None}], {}, "pan")[0]
    assert label == "PAN" and box[2] < BOX[2]                              # the last four stay visible


def test_the_cover_follows_glyph_widths():
    (box, _, _), = M._inline_ids({"text": "PAN: ABCPE1234F", "box": BOX})
    assert BOX[0] < box[0] and box[2] < BOX[2]                             # label and last four are outside it


# ── statement parser ────────────────────────────────────────────────────────
TYPE1 = ["Opening Balance Rs. 10,000.00",
         "05-01-2023", "10:22:13", "05 Jan 2023 NEFT SALARY ACME", "LTD", "1001 Rs. 50,000.00 Rs. 60,000.00",
         "07-01-2023", "09:01:55", "07 Jan 2023 UPI/DR/123456789012/RENT 1002 Rs. 15,000.00 Rs. 45,000.00",
         "Page 1 of 3", "Closing Balance Rs. 45,000.00"]
TYPE2 = ["Opening Balance 2,00,000.00",
         "1 S12345678 05/01/2023 05/01/2023 10:22:13 AM -", "NEFT-123456789012-SALARY ACME CR 50,000.00 2,50,000.00",
         "2 S12345679 07/01/2023 07/01/2023 09:01:55 AM 100234 UPI-RENT DR 15,000.00 2,35,000.00"]


def rows(lines, opening):
    return T._reconcile(T._newest_last(T._blocks(lines, 100)), opening, exact=True)


def test_rows_printed_over_several_lines_are_assembled():
    r = rows(TYPE1, 10000.0)
    assert [(x["date"], x["credit"], x["debit"], x["balance"], x["reconciled"]) for x in r] == [
        ("05-01-2023", 50000.0, 0.0, 60000.0, True), ("07-01-2023", 0.0, 15000.0, 45000.0, True)]
    assert "SALARY" in r[0]["description"]


def test_serial_number_prefix_and_cr_dr_marker():
    r = rows(TYPE2, 200000.0)
    assert [(x["date"], x["credit"], x["debit"], x["balance"]) for x in r] == [
        ("05/01/2023", 50000.0, 0.0, 250000.0), ("07/01/2023", 0.0, 15000.0, 235000.0)]


def test_newest_first_statements_are_put_in_time_order():
    newest_first = ["07/01/2023 UPI RENT 15,000.00 45,000.00", "05/01/2023 SALARY 50,000.00 60,000.00"]
    r = rows(newest_first, 10000.0)
    assert [x["date"] for x in r] == ["05/01/2023", "07/01/2023"] and all(x["reconciled"] for x in r)


def test_a_text_layer_figure_is_kept_as_printed_when_the_row_does_not_add_up():
    lines = ["05/01/2023 SALARY 50,000.00 60,000.00", "06/01/2023 TRANSFER 1,000.00 99,000.00"]   # balance jumps
    exact = T._reconcile(T._blocks(lines, 10), 10000.0, exact=True)[1]
    ocr = T._reconcile(T._blocks(lines, 10), 10000.0, exact=False)[1]
    assert (exact["credit"] or exact["debit"]) == 1000.0 and exact["reconciled"] is False
    assert (ocr["credit"] or ocr["debit"]) == 39000.0                  # a scan: the balance movement is trusted


def test_the_narration_decides_the_side_of_a_row_that_does_not_add_up():
    # separate debit / credit columns, no CR / DR marker, and a balance that jumps: the movement would
    # call this withdrawal a credit
    lines = ["05/01/2023 SALARY 50,000.00 60,000.00", "06/01/2023 ATM WDL CASH 1,000.00 99,000.00",
             "07/01/2023 NEFT Cr-123-ACME 2,000.00 5,000.00", "08/01/2023 Dr Sharma clinic 500.00 90,000.00"]
    r = T._reconcile(T._blocks(lines, 10), 10000.0, exact=True)
    assert (r[1]["debit"], r[1]["credit"]) == (1000.0, 0.0)
    assert (r[2]["debit"], r[2]["credit"]) == (0.0, 2000.0)            # balance fell, narration says credit
    assert r[3]["credit"] == 500.0                                     # no cue ("Dr" is a person): movement
    assert T._narration_side("REFUND OF ATM WITHDRAWAL") is None       # both cues: no opinion


def test_statement_totals_come_from_the_rows_only_when_they_are_the_whole_statement():
    r = rows(["05/01/2023 SALARY 50,000.00 60,000.00", "07/01/2023 UPI RENT 15,000.00 45,000.00",
              "09/01/2023 UPI GROCER 5,000.00 40,000.00"], 10000.0)
    whole = {"transactions": r, "opening_balance": 10000.0, "closing_matches": True, "source": "pdf_text"}
    parsed = {"total_credits": 123.0, "total_debits": 9.0}             # what a reading model made up
    done = T.apply_summary(parsed, whole)
    assert (parsed["total_credits"], parsed["total_debits"]) == (50000.0, 20000.0) and done["rows"] == 3
    partial = {**whole, "closing_matches": False}                      # pages missing: the rows cannot stand in
    assert T.summary(partial) is None and T.apply_summary({"total_credits": 1}, partial) is None
    assert T.summary({**whole, "opening_balance": None}) is None and T.apply_summary(None, whole) is None


def test_printed_totals_are_read_and_made_up_totals_are_removed():
    lines = ["Opening Balance 10,000.00", "05/01/2023 SALARY 50,000.00 60,000.00",
             "Total Credits : 50,000.00   Total Debits : 0.00", "Closing Balance 60,000.00"]
    printed = [next((T._amount(m.group(1)) for ln in lines for m in [rx.search(ln)] if m), None)
               for rx in (T._TOTAL_CR, T._TOTAL_DR)]
    assert printed == [50000.0, 0.0]
    parsed = {"total_credits": 1.0, "total_debits": 2.0}
    assert T.apply_summary(parsed, {"transactions": [], "totals_printed": (50000.0, 0.0)})["totals_from"].startswith("printed")
    assert (parsed["total_credits"], parsed["total_debits"]) == (50000.0, 0.0)
    # a long statement, no printed totals, rows incomplete (a scan cut off after six pages)
    long_ = {"transactions": [{"credit": 1.0, "debit": 0.0, "balance": 1.0, "reconciled": True}] * 40,
             "opening_balance": 100.0, "closing_matches": False, "totals_printed": None}
    made_up = {"opening_balance": 100, "closing_balance": 500, "total_credits": 9999, "total_debits": 12}
    done = T.apply_summary(made_up, long_)
    assert done["totals_from"] is None and made_up["total_credits"] is None and made_up["total_debits"] is None
    consistent = {"opening_balance": 100, "closing_balance": 500, "total_credits": 900, "total_debits": 500}
    assert T.apply_summary(consistent, long_) is None and consistent["total_credits"] == 900
    short = {**long_, "transactions": long_["transactions"][:5]}        # a short statement: the model may be right
    assert T.apply_summary(dict(made_up), short) is None
    # a scan at upload time: no rows yet, but several pages - the model saw two of them
    scan = {"transactions": [], "source": "scan", "totals_printed": None, "pages": 6}
    later = dict(made_up, total_credits=9999, total_debits=12)
    assert T.apply_summary(later, scan)["totals_from"] is None and later["total_credits"] is None
    assert T._printed_totals(lines) == (50000.0, 0.0) and T._printed_totals(lines[:2]) is None


def test_a_scanned_statement_is_not_read_row_by_row_on_the_request_path(monkeypatch):
    import io
    from PIL import Image
    buf = io.BytesIO()
    pages = [Image.new("RGB", (300, 400), "white") for _ in range(3)]
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:])
    read = []
    monkeypatch.setattr(T, "_lines_of_page", lambda im: read.append(im.size) or ["Total Credits 5,000.00", "Total Debits 1,200.00"])
    txn, later = T.at_upload(buf.getvalue())
    assert later is True and txn["pages"] == 3 and txn["transactions"] == [] and len(read) == 1   # one page looked at
    assert txn["totals_printed"] == (5000.0, 1200.0)


# ── extraction: a statement read that times out is retried on its first page ──
def test_a_pdf_read_that_times_out_is_retried_on_page_one(monkeypatch):
    import io
    from PIL import Image
    from app import providers
    from app.agents.impl import intake
    from app.agents.llm.gateway import LLMUnavailable

    buf = io.BytesIO()
    Image.new("RGB", (300, 400), "white").save(buf, "PDF")
    seen = []

    class Slow:
        name = "fake"

        async def extract(self, data, mime, doc_type, prompt):
            seen.append(mime)
            if mime == "application/pdf":
                raise LLMUnavailable("Ollama unreachable/timed out: ReadTimeout")
            return '{"bank_name": "X"}', {"bank_name": "X"}

    monkeypatch.setattr(providers, "get_provider", lambda: Slow())
    out = intake.ExtractionAgent.__new__(intake.ExtractionAgent).execute(
        {"data": buf.getvalue(), "mime": "application/pdf", "doc_type": "bank_statement", "prompt": '{"bank_name": ""}'})
    assert seen == ["application/pdf", "image/png"]
    assert out.payload["parsed"] == {"bank_name": "X"} and "first page only" in out.warnings[0]


# ── extraction: the PAN number is checked against the card's own OCR text ──
def test_the_pan_number_is_confirmed_or_corrected_by_ocr():
    from app.agents.tools.id_crossread import cross_read_pan, ocr_pan
    text = "INCOME TAX DEPARTMENT GOVT. OF INDIA PermanentAccountNumberABCPE1234F Signature"
    assert ocr_pan(text) == "ABCPE1234F"                              # glued to its label by OCR
    calls = []

    def upload(t):
        return lambda: calls.append(1) or t
    # 1. the text the flow already has confirms the model: no second read
    assert cross_read_pan({"pan_number": "abcpe 1234f"}, text, upload(text))["pan_number"] == "confirmed" and not calls
    # 2. it does not: the upload is read, and its single PAN-shaped token is the number
    p = {"pan_number": "ABCPE1284F", "full_name": "A B"}
    assert cross_read_pan(p, "smudged", upload(text)) == {"pan_number": "corrected", "by": "ocr of the upload"}
    assert p == {"pan_number": "ABCPE1234F", "full_name": "A B"} and calls == [1]
    # a PAN-shaped read that no OCR pass can check stands; several candidates are no opinion
    p = {"pan_number": "ABCPE1284F"}
    assert cross_read_pan(p, "nothing", upload("ABCPE1234F and XYZPK9876L")) is None and p["pan_number"] == "ABCPE1284F"
    # 3. a read that is not PAN-shaped: the cleaned image's token, else look-alike characters put right
    p = {"pan_number": "ABCPE12E4F"}
    assert cross_read_pan(p, text, upload(""))["pan_number"] == "corrected" and p["pan_number"] == "ABCPE1234F"
    p = {"pan_number": "ABCPEI23OF"}
    assert cross_read_pan(p, "", None) == {"pan_number": "repaired", "by": "look-alike characters"} and p["pan_number"] == "ABCPE1230F"
    p = {"pan_number": "ABCPE12"}
    assert cross_read_pan(p, "", None) is None and p["pan_number"] == "ABCPE12" and cross_read_pan(None, text) is None


# ── PII masking: an ID card whose number was not located is withheld ──
def test_an_id_card_whose_number_was_not_found_is_withheld_not_published(monkeypatch):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (640, 400), "white").save(buf, "PNG")
    monkeypatch.setattr(M, "_all_regions", lambda pil, parsed, doc_type="generic": [])
    card = M.mask_document(buf.getvalue(), {}, "pan")
    assert card["withheld"] is True and card["complete"] is False and card["masked_count"] == 0
    page = M.mask_document(buf.getvalue(), {}, "salary_slip")          # a page may simply carry no ID
    assert "withheld" not in page and page["masked_count"] == 0
    monkeypatch.setattr(M, "_all_regions", lambda pil, parsed, doc_type="generic": [((10, 10, 200, 40), "PAN", 6)])
    ok = M.mask_document(buf.getvalue(), {}, "pan")
    assert ok["complete"] is True and ok["masked_count"] == 1 and "withheld" not in ok


def test_a_long_statement_goes_to_the_model_as_first_and_last_page(monkeypatch):
    import io
    import pypdfium2 as pdfium
    from PIL import Image
    from app import providers
    from app.agents.impl import intake

    pages = [Image.new("RGB", (200, 280), c) for c in ("white", "red", "green", "blue")]
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:])
    seen = []

    class Reader:
        name = "fake"

        async def extract(self, data, mime, doc_type, prompt):
            seen.append(len(pdfium.PdfDocument(data)))
            return "{}", {"bank_name": "X"}

    monkeypatch.setattr(providers, "get_provider", lambda: Reader())
    agent = intake.ExtractionAgent.__new__(intake.ExtractionAgent)
    agent.execute({"data": buf.getvalue(), "mime": "application/pdf", "doc_type": "bank_statement", "prompt": "{}"})
    agent.execute({"data": buf.getvalue(), "mime": "application/pdf", "doc_type": "itr", "prompt": "{}"})
    assert seen == [2, 4]                                              # other documents are sent whole
    assert intake._summary_pages(b"not a pdf") is None


def test_the_cover_edge_snaps_to_the_gap_before_the_last_four():
    import numpy as np
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (700, 60), "white")
    d = ImageDraw.Draw(img)
    font = M._font(28)
    text = "PAN: ABCPE1234F"
    d.text((20, 12), text, fill=(10, 10, 10), font=font)
    arr = np.asarray(img)
    x = lambda k: 20 + d.textlength(text[:k], font=font)                  # noqa: E731 - true left edge of char k
    true_cut, cw = x(11), (x(11) - x(5)) / 6
    for off in (-0.45, 0.0, 0.45, 0.8):                                    # the computed edge is off by this many characters
        box = (int(x(5)), 10, int(true_cut + off * cw), 46)
        snapped = M._snap_to_gap(arr, box, 6)
        assert x(10) + 0.5 * cw < snapped[2] <= x(11) + 2, off              # after the 6th character, before the 7th
    blank = np.full((60, 700, 3), 255, dtype=np.uint8)
    assert M._snap_to_gap(blank, (100, 10, 200, 46), 6) == (100, 10, 200, 46)   # nothing to read: unchanged


# ── cross verification: a document in the father's name is not the applicant's ──
def test_a_dropped_first_name_is_not_the_same_person():
    from app.docrules import compare_names
    assert compare_names("Rahul Suresh Sharma", "Suresh Sharma")["level"] == "partial"     # the father's document
    assert compare_names("Suresh Sharma", "Rahul Suresh Sharma")["level"] == "partial"
    assert compare_names("Rahul Suresh Sharma", "S Sharma")["level"] == "partial"
    assert compare_names("Rahul Suresh Sharma", "Rahul Sharma")["level"] == "match"        # middle name omitted
    assert compare_names("Rahul Suresh Sharma", "R S Sharma")["level"] == "match"          # initials
    assert compare_names("Sharma Rahul", "Mr. Rahul Sharma")["level"] == "match"           # surname first, honorific
    assert compare_names("Rahul Sharma", "Rahul Verma")["level"] == "partial"
    assert compare_names("Rahul Sharma", "Suresh Sharma")["level"] == "mismatch"


# ── document identity: an ID card the page's own text does not confirm ──
def test_a_pan_upload_with_no_pan_heading_or_code_on_the_page_is_marked_unconfirmed():
    from app.agents.tools.doc_keywords import unconfirmed
    foreign = "republica de espana documento nacional de identidad apellidos nombre sexo nacionalidad 12345678z"
    assert unconfirmed("pan", foreign) is True
    assert unconfirmed("pan", "incometaxdepartment govt. of india name father's name date of birth signature") is False
    assert unconfirmed("pan", "some card text that ocr read badly but with the number abcpe1234f on it somewhere") is False
    assert unconfirmed("pan", "too short") is False                     # unreadable page: no opinion
    assert unconfirmed("salary_slip", foreign) is False                 # only ID cards with a fixed heading


def test_a_receipt_uploaded_as_a_bank_statement_is_refused_by_what_the_page_says():
    from app.agents.impl.intake import DocumentIdentityAgent
    from app.agents.tools.doc_keywords import lacks_own_words
    receipt = "super mart sdn bhd tax invoice cash total 45.60 change 4.40 debit card thank you please come again"
    statement = "statementofaccount accountnumber 1234567890 ifsc hdfc0001234 openingbalance 10,000.00 closingbalance 12,500.00"
    assert lacks_own_words("bank_statement", receipt) is True and lacks_own_words("bank_statement", statement) is False
    assert lacks_own_words("bank_statement", "blurred") is False        # unreadable page: no opinion
    assert lacks_own_words("salary_slip", receipt) is False             # only where such phrases are defined
    agent = DocumentIdentityAgent.__new__(DocumentIdentityAgent)
    agent.tool = lambda name, fn, *a, **k: fn(*a, **k)
    made_up = {"account_holder": "Super Mart", "closing_balance": 45.6}  # what the model fills from a receipt
    out = agent.execute({"doc_type": "bank_statement", "parsed": made_up, "page_text": receipt})
    assert out.payload["ok"] is False and out.payload["no_own_words"] is True and "nothing on the page" in out.summary
    assert agent.execute({"doc_type": "bank_statement", "parsed": made_up, "page_text": statement}).payload["ok"] is True


# ── preprocessing: the card outline is chosen among region and edge candidates, or not used at all ──
def test_a_card_is_found_by_its_region_and_nothing_is_used_when_nothing_looks_like_a_card():
    import cv2
    import numpy as np
    from app.card_outline import CONFIDENT, find_card
    rng = np.random.default_rng(3)
    frame = np.full((600, 900, 3), (60, 90, 130), np.uint8)                       # a plain table
    frame = np.clip(frame + rng.normal(0, 4, frame.shape), 0, 255).astype(np.uint8)
    card = np.full((230, 365, 3), 235, np.uint8)                                  # a pale card, 1.59 : 1
    for y in range(20, 220, 14):                                                  # printed lines
        cv2.line(card, (20, y), (int(rng.integers(150, 340)), y), (60, 60, 60), 2)
    m = cv2.getRotationMatrix2D((182, 115), 11, 1.0)
    m[:, 2] += (270, 180)
    mask = cv2.warpAffine(np.full(card.shape[:2], 255, np.uint8), m, (900, 600))
    warped = cv2.warpAffine(card, m, (900, 600))
    frame[mask > 127] = warped[mask > 127]
    found = find_card(frame)
    assert found is not None and found[1] >= CONFIDENT
    got = np.zeros((600, 900), np.uint8)
    cv2.fillPoly(got, [np.round(found[0]).astype(np.int32)], 255)
    iou = ((got > 0) & (mask > 127)).sum() / ((got > 0) | (mask > 127)).sum()
    assert iou >= 0.9
    plain = np.clip(np.full((600, 900, 3), 128.0) + rng.normal(0, 4, (600, 900, 3)), 0, 255).astype(np.uint8)
    assert find_card(plain) is None                                               # no card: no outline, no warp


# ── tampering: the patch detector trained on real forgeries ──
def test_patch_evidence_is_zero_up_to_the_floor_and_never_high_alone():
    from app.tampering.signals import patch_cnn
    assert patch_cnn.evidence(-0.2) == 0.0 and patch_cnn.evidence(0.0) == 0.0     # at or under the genuine floor
    assert patch_cnn.evidence(0.01) > 0.6 and patch_cnn.evidence(5.0) == 1.0
    quiet = dict(metadata=0.15, copy_move=0.1, cnn=0.5, ela=0.2, noise=0.3)
    alone = fusion.fuse(sig(**quiet, patch_cnn=1.0), "pan")
    assert alone["risk_tier"] == "MEDIUM"                               # worth a look, never HIGH on its own
    assert fusion.fuse(sig(**quiet, patch_cnn=0.0), "pan")["risk_tier"] == "LOW"
    backed = fusion.fuse(sig(**{**quiet, "metadata": 0.9}, patch_cnn=0.8), "salary_slip")
    assert backed["risk_tier"] == "HIGH"                                # an editor tag AND an edited-looking region
    with_ela = fusion.fuse(sig(**{**quiet, "ela": 1.0}, patch_cnn=0.8), "salary_slip")
    assert with_ela["risk_tier"] == "HIGH"                              # the patch hit is what backs a lone ELA hit


def test_the_patch_signal_needs_both_models_and_names_every_area_it_means():
    from app.tampering import locate as L
    from app.tampering.signals import patch_cnn
    one_model = patch_cnn.verdict({"family": "id", "over": [0.7, -0.4], "boxes": [[287, 16, 335, 64]]})
    assert one_model.score == 0.0 and "region" not in one_model.findings[0]        # the models disagree: no evidence, no box
    both = patch_cnn.verdict({"family": "id", "over": [0.4, 1.2], "boxes": [[10, 20, 58, 68], [300, 40, 348, 88]]})
    assert both.score == 0.76                                                      # the WEAKER model sets the evidence
    assert L.region_boxes(both.findings[0]) == [(10, 20, 58, 68), (300, 40, 348, 88)]
    assert not both.artifacts                                                      # artifacts are images for the UI only


def test_separate_hot_areas_are_reported_strongest_first():
    import numpy as np
    from app.tampering.signals.patch_model import PATCH, STRIDE, areas
    m = np.full((20, 30), -5.0, np.float32)
    m[4, 6], m[4, 7], m[15, 25] = 9.0, 8.0, 6.0                                    # two neighbours are ONE area
    got = areas(m)
    assert [round(v) for _, _, v in got] == [9, 6, -5]
    x, y, _ = got[0]
    assert 6 * STRIDE + PATCH / 2 <= x <= 7 * STRIDE + PATCH / 2 and y == 4 * STRIDE + PATCH / 2


def test_a_region_a_detector_states_is_drawn_as_stated():
    from app.tampering import locate as L
    finding = "Looks edited at region [120, 40, 168, 88] - it stands out from the rest of the page"
    assert L.region_boxes(finding) == [(120, 40, 168, 88)]
    assert L.region_boxes(finding, 0.5) == [(60, 20, 84, 44)]           # scaled to the rendered image
    assert L.region_boxes("Net salary 75,000 is higher than gross salary 60,000") == []
