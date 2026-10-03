"""Renderers for synthetic documents.

Two output kinds, matching what FinSight really receives:
  * card / scan IMAGES (PIL) — PAN, Aadhaar, and photographed paper documents;
  * born-digital PDFs — payslips and statements as an employer/bank system
    would emit them. Written by a tiny PDF writer below (text + rules only), so
    the Producer/Creator metadata and the save history are fully controlled:
    that is exactly what the pdf_forensics signal inspects.
"""
from __future__ import annotations

import io
from datetime import datetime

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .identities import Identity, fmt_inr, stable

_FONT_CACHE: dict = {}


def font(size: int, bold: bool = False, mono: bool = False):
    name = "consola.ttf" if mono else ("arialbd.ttf" if bold else "arial.ttf")
    key = (name, size)
    if key not in _FONT_CACHE:
        try:
            _FONT_CACHE[key] = ImageFont.truetype(name, size)
        except OSError:
            _FONT_CACHE[key] = ImageFont.load_default()
    return _FONT_CACHE[key]


def _silhouette(w: int, h: int) -> Image.Image:
    """Grey head-and-shoulders placeholder (used when no face image is given).
    Deliberately NOT a face: face detection must report 'no face' on it."""
    im = Image.new("RGB", (w, h), (214, 219, 226))
    d = ImageDraw.Draw(im)
    d.ellipse([w * 0.3, h * 0.12, w * 0.7, h * 0.55], fill=(160, 168, 180))
    d.rectangle([w * 0.18, h * 0.62, w * 0.82, h], fill=(160, 168, 180))
    return im


def _paste_photo(card: Image.Image, face: Image.Image | None, box: tuple[int, int, int, int]):
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    photo = face.convert("RGB").resize((w, h), Image.LANCZOS) if face is not None else _silhouette(w, h)
    card.paste(photo, (x0, y0))
    ImageDraw.Draw(card).rectangle(box, outline=(90, 90, 90), width=2)


def _grain(im: Image.Image, amount: int = 5, seed: int = 0) -> Image.Image:
    """Mild sensor-like noise + slight softening so a rendered card behaves like
    a scan/photo (a perfectly clean render is unrealistically easy for OCR and
    unrealistically 'suspicious' for noise forensics)."""
    import numpy as np
    rng = np.random.default_rng(seed)
    arr = np.asarray(im, dtype=np.int16)
    arr = arr + rng.normal(0, amount, arr.shape).astype(np.int16)
    out = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))
    return out.filter(ImageFilter.GaussianBlur(0.4))


def pan_card(i: Identity, face: Image.Image | None = None, seed: int = 0) -> Image.Image:
    W, H = 1012, 638
    im = Image.new("RGB", (W, H), (205, 226, 240))
    d = ImageDraw.Draw(im)
    for y in range(0, H, 6):  # faint guilloche-like background lines
        d.line([(0, y), (W, y + 40)], fill=(196, 218, 234), width=1)
    d.rectangle([0, 0, W, 92], fill=(28, 70, 128))
    d.text((34, 16), "INCOME TAX DEPARTMENT", font=font(34, True), fill="white")
    d.text((620, 22), "GOVT. OF INDIA", font=font(30, True), fill="white")
    d.text((34, 60), "Permanent Account Number Card", font=font(22), fill=(220, 230, 245))
    _paste_photo(im, face, (40, 128, 250, 388))
    x = 290
    d.text((x, 130), "Permanent Account Number / PAN", font=font(20), fill=(60, 60, 60))
    d.text((x, 158), i.pan, font=font(46, True, mono=True), fill=(15, 15, 15))
    d.text((x, 230), "Name", font=font(20), fill=(60, 60, 60))
    d.text((x, 254), i.full_name.upper(), font=font(32, True), fill=(15, 15, 15))
    d.text((x, 310), "Father's Name", font=font(20), fill=(60, 60, 60))
    d.text((x, 334), i.father_name.upper(), font=font(30, True), fill=(15, 15, 15))
    d.text((x, 390), "Date of Birth", font=font(20), fill=(60, 60, 60))
    d.text((x, 414), i.dob, font=font(32, True), fill=(15, 15, 15))
    d.rectangle([40, 470, 380, 560], outline=(90, 90, 90), width=2)
    d.text((52, 530), "Signature", font=font(18), fill=(90, 90, 90))
    d.rectangle([820, 440, 972, 592], outline=(30, 30, 30), width=3)  # QR stand-in
    for k in range(0, 140, 14):
        d.rectangle([830 + k % 70, 450 + k, 840 + k % 70, 460 + k], fill=(30, 30, 30))
    return _grain(im, seed=seed)


def aadhaar_card(i: Identity, face: Image.Image | None = None, seed: int = 0) -> Image.Image:
    W, H = 1012, 638
    im = Image.new("RGB", (W, H), (252, 252, 250))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 26], fill=(255, 153, 51))
    d.rectangle([0, 26, W, 52], fill=(255, 255, 255))
    d.rectangle([0, 52, W, 78], fill=(19, 136, 8))
    d.text((300, 90), "Government of India", font=font(34, True), fill=(20, 20, 20))
    _paste_photo(im, face, (48, 150, 258, 410))
    x = 300
    d.text((x, 160), i.full_name, font=font(34, True), fill=(10, 10, 10))
    d.text((x, 214), f"DOB: {i.dob}", font=font(30), fill=(10, 10, 10))
    d.text((x, 262), i.gender, font=font(30), fill=(10, 10, 10))
    addr = i.address
    d.text((x, 312), "Address:", font=font(22, True), fill=(40, 40, 40))
    line1, line2 = (addr[:38], addr[38:]) if len(addr) > 38 else (addr, "")
    d.text((x, 340), line1, font=font(22), fill=(40, 40, 40))
    if line2:
        d.text((x, 368), line2.strip(), font=font(22), fill=(40, 40, 40))
    num = f"{i.aadhaar[:4]} {i.aadhaar[4:8]} {i.aadhaar[8:]}"
    d.text((300, 470), num, font=font(52, True), fill=(10, 10, 10))
    d.line([(40, 560), (W - 40, 560)], fill=(200, 30, 30), width=3)
    d.text((330, 574), "Aadhaar - Aam Aadmi ka Adhikar", font=font(24, True), fill=(200, 30, 30))
    return _grain(im, seed=seed)


def paper_document(lines: list[tuple], size=(1240, 1754), seed: int = 0) -> Image.Image:
    """Photographed/scanned paper document. `lines` = [(x, y, text, size, bold)]."""
    im = Image.new("RGB", size, (250, 250, 247))
    d = ImageDraw.Draw(im)
    for x, y, text, sz, bold in lines:
        if text == "__RULE__":
            d.line([(x, y), (size[0] - x, y)], fill=(90, 90, 90), width=2)
        else:
            d.text((x, y), text, font=font(sz, bold), fill=(20, 20, 20))
    return _grain(im, amount=4, seed=seed)


# ── Layouts shared by the image and PDF renderers ───────────────────────────
def salary_slip_lines(i: Identity, overrides: dict | None = None,
                      placeholders: bool = False) -> list[tuple]:
    o = overrides or {}
    emp = "ABC Company Pvt Ltd" if placeholders else i.employer
    name = "John Doe" if placeholders else i.full_name
    basic, hra, spl = o.get("basic", i.basic), o.get("hra", i.hra), o.get("special", i.special_allowance)
    gross = o.get("gross", i.gross)
    net = o.get("net", i.net)
    y = 80
    L = [(80, y, emp, 34, True), (80, y + 48, f"{i.city}, {i.state}", 20, False),
         (80, y + 100, f"Salary Slip for the month of {i.month_year}", 26, True),
         (80, y + 146, "__RULE__", 0, False)]
    y += 170
    info = [("Employee Name", name), ("Employee ID", "123456" if placeholders else f"EMP{stable(i.uid) % 90000 + 10000}"),
            ("Designation", i.designation), ("PAN", i.pan),
            ("UAN", "" if placeholders else f"10{stable(i.uid + 'u') % 10**10:010d}"),
            ("Bank A/c", "123456789" if placeholders else f"XXXXXX{i.account_number[-4:]}")]
    for k, v in info:
        L.append((80, y, k, 22, False)); L.append((420, y, str(v), 22, True)); y += 38
    y += 16
    L.append((80, y, "__RULE__", 0, False)); y += 20
    L.append((80, y, "Earnings", 24, True)); L.append((640, y, "Deductions", 24, True)); y += 44
    earn = [("Basic Pay", basic), ("HRA", hra), ("Special Allowance", spl)]
    ded = [("Provident Fund", i.pf), ("Professional Tax", i.professional_tax), ("TDS", i.tds)]
    for (ek, ev), (dk, dv) in zip(earn, ded):
        L.append((80, y, ek, 22, False)); L.append((400, y, fmt_inr(ev), 22, False))
        L.append((640, y, dk, 22, False)); L.append((960, y, fmt_inr(dv), 22, False)); y += 38
    y += 10
    L.append((80, y, "__RULE__", 0, False)); y += 20
    L.append((80, y, "Gross Salary", 24, True)); L.append((400, y, fmt_inr(gross), 24, True))
    L.append((640, y, "Total Deductions", 24, True))
    L.append((960, y, fmt_inr(i.pf + i.professional_tax + i.tds), 24, True)); y += 60
    L.append((80, y, "Net Salary", 28, True)); L.append((400, y, fmt_inr(net), 28, True)); y += 90
    L.append((80, y, "This is a computer generated payslip and does not require a signature.", 18, False))
    return L


def statement_lines(i: Identity, tx: list[dict] | None = None,
                    summary: dict | None = None, placeholders: bool = False) -> list[tuple]:
    tx = tx if tx is not None else i.transactions
    s = summary or {}
    bank = "ABC Bank" if placeholders else i.bank_name
    acct = "1234567890" if placeholders else i.account_number
    L = [(80, 70, bank, 34, True), (80, 116, f"Branch: {i.city}   IFSC: {i.ifsc}", 20, False),
         (80, 160, "Statement of Account", 26, True),
         (80, 204, f"Account Holder: {i.full_name}", 22, False),
         (80, 238, f"Account Number: {acct}", 22, False),
         (80, 272, f"Period: {s.get('statement_period', i.statement_period)}", 22, False),
         (80, 306, f"Opening Balance: {fmt_inr(s.get('opening_balance', i.opening_balance))}", 22, False),
         (80, 336, "__RULE__", 0, False),
         (80, 350, "Date", 20, True), (240, 350, "Description", 20, True),
         (720, 350, "Debit", 20, True), (880, 350, "Credit", 20, True), (1040, 350, "Balance", 20, True)]
    y = 386
    for t in tx:
        L += [(80, y, t["date"], 18, False), (240, y, t["description"][:34], 18, False),
              (720, y, fmt_inr(t["debit"]) if t["debit"] else "", 18, False),
              (880, y, fmt_inr(t["credit"]) if t["credit"] else "", 18, False),
              (1040, y, fmt_inr(t["balance"]), 18, False)]
        y += 28
    y += 12
    L.append((80, y, "__RULE__", 0, False)); y += 18
    L.append((80, y, f"Total Debits: {fmt_inr(s.get('total_debits', i.total_debits))}", 20, True))
    L.append((600, y, f"Total Credits: {fmt_inr(s.get('total_credits', i.total_credits))}", 20, True)); y += 32
    L.append((80, y, f"Closing Balance: {fmt_inr(s.get('closing_balance', i.closing_balance))}", 20, True))
    L.append((600, y, f"Average Balance: {fmt_inr(s.get('average_balance', i.average_balance))}", 20, True))
    return L


def itr_lines(i: Identity) -> list[tuple]:
    return [(80, 80, "INDIAN INCOME TAX RETURN ACKNOWLEDGEMENT", 30, True),
            (80, 130, f"Assessment Year: {i.assessment_year[3:]}", 22, False),
            (80, 170, "__RULE__", 0, False),
            (80, 190, f"Name: {i.full_name}", 22, False), (80, 228, f"PAN: {i.pan}", 22, False),
            (80, 266, "Form: ITR-1 (SAHAJ)", 22, False),
            (80, 320, "Gross Total Income", 22, True), (600, 320, fmt_inr(i.gross_total_income), 22, True),
            (80, 358, "Total TDS", 22, True), (600, 358, fmt_inr(i.annual_tds), 22, True),
            (80, 420, "Verified electronically.", 18, False)]


def form16_lines(i: Identity) -> list[tuple]:
    return [(80, 80, "FORM NO. 16", 30, True),
            (80, 124, "Certificate under Section 203 of the Income-tax Act, 1961", 20, False),
            (80, 170, "__RULE__", 0, False),
            (80, 190, f"Name of Employer: {i.employer}", 22, False),
            (80, 228, f"TAN of Employer: {i.employer_tan}", 22, False),
            (80, 266, f"Name of Employee: {i.full_name}", 22, False),
            (80, 304, f"PAN of Employee: {i.pan}", 22, False),
            (80, 342, f"Assessment Year: {i.assessment_year[3:]}", 22, False),
            (80, 400, "Gross Salary", 22, True), (600, 400, fmt_inr(i.gross * 12), 22, True),
            (80, 438, "Total Tax Deducted", 22, True), (600, 438, fmt_inr(i.annual_tds), 22, True)]


def gst_lines(i: Identity, turnover: int | None = None) -> list[tuple]:
    t = turnover if turnover is not None else i.turnover
    return [(80, 80, "GSTR-3B", 30, True), (80, 124, f"Return Period: {i.gst_period}", 22, False),
            (80, 170, "__RULE__", 0, False),
            (80, 190, f"GSTIN: {i.gstin}", 22, False), (80, 228, f"Legal Name: {i.full_name}", 22, False),
            (80, 266, f"Trade Name: {i.trade_name}", 22, False),
            (80, 320, "Total Taxable Turnover", 22, True), (600, 320, fmt_inr(t), 22, True),
            (80, 358, "Tax Paid", 22, True), (600, 358, fmt_inr(int(t * 0.18)), 22, True)]


# ── Minimal born-digital PDF writer ─────────────────────────────────────────
def _pdf_escape(s: str) -> str:
    s = s.replace("₹", "Rs ")
    s = s.encode("latin-1", "replace").decode("latin-1")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def lines_to_pdf(lines: list[tuple], producer: str = "SAP NetWeaver 7.5 PDF Generator",
                 creator: str = "SAP HCM Payroll", created: datetime | None = None,
                 modified: datetime | None = None, page_px=(1240, 1754)) -> bytes:
    """Render layout `lines` (pixel coordinates on an A4-at-150dpi canvas) as a
    single-page text PDF. Producer/Creator/dates are written verbatim."""
    pw, ph = 595.0, 842.0
    sx, sy = pw / page_px[0], ph / page_px[1]
    ops = []
    for x, y, text, sz, bold in lines:
        if text == "__RULE__":
            yy = ph - y * sy
            ops.append(f"0.35 G 1 w {x * sx:.2f} {yy:.2f} m {pw - x * sx:.2f} {yy:.2f} l S")
            continue
        size = sz * sx * 1.02
        yy = ph - (y * sy) - size
        ops.append(f"BT /{'F2' if bold else 'F1'} {size:.2f} Tf {x * sx:.2f} {yy:.2f} Td ({_pdf_escape(str(text))}) Tj ET")
    content = "\n".join(ops).encode("latin-1")
    created = created or datetime(2026, 4, 2, 10, 15, 0)
    modified = modified or created
    d = lambda t: t.strftime("D:%Y%m%d%H%M%S+05'30'")  # noqa: E731
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.0f} {ph:.0f}] "
        f"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> /Contents 4 0 R >>".encode(),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        f"<< /Producer ({_pdf_escape(producer)}) /Creator ({_pdf_escape(creator)}) "
        f"/CreationDate ({d(created)}) /ModDate ({d(modified)}) >>".encode(),
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info 7 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def append_incremental_update(pdf: bytes, new_producer: str | None = None,
                              modified: datetime | None = None) -> bytes:
    """Simulate a post-issue edit: append a new Info revision + xref section
    after the original %%EOF, exactly how PDF editors save changes."""
    modified = modified or datetime(2026, 5, 20, 18, 3, 0)
    d = modified.strftime("D:%Y%m%d%H%M%S+05'30'")
    prod = _pdf_escape(new_producer or "SAP NetWeaver 7.5 PDF Generator")
    start = len(pdf)
    body = (f"7 0 obj\n<< /Producer ({prod}) /Creator (SAP HCM Payroll) "
            f"/CreationDate (D:20260402101500+05'30') /ModDate ({d}) >>\nendobj\n").encode()
    prev = int(pdf.rsplit(b"startxref", 1)[1].split()[0])
    xref = start + len(body)
    tail = (f"xref\n7 1\n{start:010d} 00000 n \ntrailer\n<< /Size 8 /Root 1 0 R /Info 7 0 R "
            f"/Prev {prev} >>\nstartxref\n{xref}\n%%EOF\n").encode()
    return pdf + body + tail


def image_to_pdf(im: Image.Image, quality: int = 88) -> bytes:
    """Scan-wrapper PDF: one full-page JPEG, like a phone scanner app emits."""
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "PDF", resolution=150.0, quality=quality)
    return buf.getvalue()


def to_jpeg(im: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def to_png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "PNG")
    return buf.getvalue()
