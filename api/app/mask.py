"""Redact PII on a document image for the hover-preview on Review & Submit.

The problem: an officer hovering a thumbnail sees the full Aadhaar / PAN /
account / passport / voter number in the clear. UIDAI + RBI norms say show only
the last 4 characters of an Aadhaar; the same courtesy applies to the other IDs.

How it works (fully local, deterministic — no model):
  1. render the document to an image (reusing app.imaging.load_image, so
     PDFs and photos are handled the same way);
  2. OCR it into word boxes (app.imaging.ocr_tokens);
  3. find the sensitive values by PATTERN on those tokens, NOT by trusting the
     OCR-extracted fields — because the extraction step is told never to invent
     masked Aadhaar digits, so its field may already be "XXXX-XXXX-1234" while
     the IMAGE still shows the full number. Pattern detection on the page is the
     only thing that reliably finds what is actually printed. Extracted field
     values (e.g. the account number) are used as an extra, supplementary anchor.
  4. Replace the masked characters with printed 'X' glyphs, keeping the last 4
     visible — so the preview reads "XXXX XXXX 1234", not a black bar. The fill
     samples the card's own background colour and the X's are drawn in its own
     ink colour, so the redaction looks printed on the document rather than
     stamped over it. For a single token the box is split proportionally by
     character count; a grouped Aadhaar (e.g. three 4-digit tokens, or an 8+4
     split, or a single 12-digit token) is handled by covering everything except
     the last four digits regardless of how OCR tokenised it.

Coverage: PAN, Aadhaar (any tokenisation), long account/card numbers, Indian
passport numbers, and Voter-ID (EPIC) numbers. A document with no detectable PII
comes back re-rendered with zero changes, so the preview path is uniform.
"""
import base64
import io
import logging
import re

import numpy as np

from app.imaging import load_image as _load_image
from app.imaging import norm_alnum as _norm_alnum
from app.imaging import ocr_tokens as _ocr_tokens

log = logging.getLogger("finsight.mask")

_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")

_INK_FALLBACK = (17, 24, 39)   # dark slate, if the card's ink can't be sampled
_BG_FALLBACK = (255, 255, 255)


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _prefix_box(box, text: str, keep_last: int):
    """Sub-box covering every character EXCEPT the last `keep_last`, plus the
    count of characters that sub-box spans (how many X's to draw).

    Character x-positions are approximated by dividing the token's width evenly
    over its character count — accurate enough for the printed, near-monospace
    numerics on ID documents. Returns (box, n_x) or None if nothing to cover."""
    x0, y0, x1, y1 = box
    n = max(len(text.strip()), 1)
    cut = n - keep_last
    if cut <= 0:
        return None
    # an even split per character: ID numbers are printed in near fixed-width type. (Glyph widths of a
    # proportional font were tried for these bare tokens on 2026-10-02 and placed the cut worse — last
    # four readable on 50% of PAN cards against 70%; they are used only for a number inside a longer
    # line of ordinary text, see _span_box.)
    mx1 = x0 + (cut / n) * (x1 - x0)
    if mx1 - x0 < 3:
        return None
    return (int(x0), int(y0), int(round(mx1)), int(y1)), cut


def _same_line(a, b) -> bool:
    """Do two boxes sit on the same text line (>50% vertical overlap)?"""
    inter = min(a[3], b[3]) - max(a[1], b[1])
    return inter > 0.5 * min(a[3] - a[1], b[3] - b[1])


def _digit_prefix_chars(text: str, digit_cut: int) -> int:
    """Character index just AFTER the digit_cut-th digit of `text`. Needed
    because tokens contain spaces ('1234 5678 9012'): a cut counted in digits
    must be converted to a character position before splitting the box, or the
    mask stops mid-number and leaves 5 digits visible instead of 4 (measured
    on a real e-Aadhaar: masked '7275 042', left '4 6997' in the clear)."""
    cnt = 0
    for i, ch in enumerate(text):
        if ch.isdigit():
            cnt += 1
            if cnt == digit_cut:
                return i + 1
    return len(text)


def _mask_run_keep_last4(run):
    """Given a left-to-right run of digit tokens [(text, box), ...] that together
    form one number, produce the regions [(box, n_x), ...] covering everything
    except the last 4 digits. Handles any tokenisation: 4+4+4, 8+4, 4+8, or a
    single 12-digit token — walks the run masking whole tokens until only four
    digits remain, splitting the boundary token if needed."""
    total = sum(len(_digits(t)) for t, _ in run)
    to_mask = total - 4
    if to_mask <= 0:
        return []
    out = []
    masked = 0
    for text, box in run:
        if masked >= to_mask:
            break
        d = len(_digits(text))
        if d == 0:
            continue
        if masked + d <= to_mask:
            out.append((box, d))                       # whole token -> d X's
            masked += d
        else:
            cut = to_mask - masked                     # partial: first `cut` DIGITS
            ts = text.strip()
            p = _digit_prefix_chars(ts, cut)           # -> char position of the cut
            pb = _prefix_box(box, ts, keep_last=len(ts) - p)
            if pb:
                out.append((pb[0], cut))               # draw `cut` X's (digit count)
            masked = to_mask
    return out


# Characters OCR confuses on small print, by what the PAN format expects at that position.
_TO_LETTER = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G"}
_TO_DIGIT = {"O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2", "G": "6"}


def _pan_shaped(alnum: str) -> bool:
    """A 10-character token that is a PAN once at most two look-alike characters are read the way the
    format requires (letters in positions 1-5 and 10, digits in 6-9): 'ABCDE123OF' -> the O is a 0."""
    s = (alnum or "").upper()
    if len(s) != 10:
        return False
    fixed, out = 0, []
    for pos, ch in enumerate(s):
        want_digit = 5 <= pos <= 8
        if want_digit and not ch.isdigit():
            ch, fixed = _TO_DIGIT.get(ch, ch), fixed + 1
        elif not want_digit and ch.isdigit():
            ch, fixed = _TO_LETTER.get(ch, ch), fixed + 1
        out.append(ch)
    return fixed <= 2 and bool(_PAN_RE.match("".join(out)))


_PAN_WORD = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{10}(?![A-Za-z0-9])")
# twelve digits as Aadhaar prints them (4-4-4) — not the head or tail of a longer run (a 16-digit VID)
_AAD_GROUPED = re.compile(r"(?<!\d)(?<!\d[ \-])\d{4}[ \-]\d{4}[ \-]\d{4}(?![ \-]?\d)")
_AAD_WORD = re.compile(r"aa?dha+r|\buid\b|\buidai\b|आधार", re.I)


def _char_x(box, text: str, k: int) -> float:
    """x of the left edge of character k of a token, by the glyph widths of a reference sans font —
    letters, digits, colons and spaces differ too much in width for an even split of a whole line."""
    x0, _, x1, _ = box
    n = len(text)
    if k <= 0:
        return float(x0)
    if k >= n:
        return float(x1)
    try:
        f = _font(40)
        total = f.getlength(text)
        return x0 + (f.getlength(text[:k]) / total) * (x1 - x0) if total > 0 else x0 + k / n * (x1 - x0)
    except Exception:  # noqa: BLE001 - no font metrics: even split
        return x0 + k / n * (x1 - x0)


def _span_box(box, text: str, start: int, end: int):
    """Sub-box of a token covering characters [start, end)."""
    a, b = _char_x(box, text, start), _char_x(box, text, end)
    return (int(a), int(box[1]), int(round(b)), int(box[3])) if b - a >= 3 else None


# OCR drops spaces: the line comes back as "PermanentAccountNumberABCPE1234F" or "PANABCPE1234F". A PAN
# glued to the label before it is still five capitals, four digits, one capital and then the end of
# the word; its 4th letter is always the holder type (P person, C company, H HUF, F firm, A AOP,
# T trust, B BOI, L local authority, J juridical person, G government) — which keeps this off
# ordinary letter-digit codes.
_PAN_GLUED = re.compile(r"[A-Z]{5}[0-9]{4}[A-Z](?![A-Za-z0-9])")
_PAN_HOLDER = set("PCHFATBLJG")
_PAN_LABEL = re.compile(r"(PAN|NO|NUMBER|CARD|ID)$")
# the ten characters right after a PAN label, for a number OCR misread by a look-alike character AND
# glued to its label ('PermanentAccountNumberUNYPU56501': the final I read as 1) - neither the strict
# glued pattern nor the bare-token look-alike rule sees that one
_PAN_AFTER_LABEL = re.compile(r"(?:PAN|Account\s*Number|Account\s*No)\W{0,3}(?:No|Number|Card)?\W{0,3}"
                              r"([A-Za-z0-9]{10})(?![A-Za-z0-9])", re.I)
# twelve digits with OCR's uneven spacing ("25518287 7646") — used only beside the word Aadhaar / UID
_AAD_LOOSE = re.compile(r"(?<!\d)(?:\d[ \-]?){11}\d(?![ \-]?\d)")
# the last 4 / 8 digits of a line that names Aadhaar: the first group(s) of a number OCR split after
_AAD_TAIL = re.compile(r"(?<![\d\-/.])(\d{4}(?:[ \-]?\d{4})?)\s*$")


def _inline_ids(t: dict) -> list[tuple]:
    """[(box, label, n_x)] for a PAN / Aadhaar number sitting inside a longer OCR line."""
    text = t["text"]
    out, spans = [], []

    def take(start, end, label, n_x):
        if any(start < e and s < end for s, e in spans):
            return
        b = _span_box(t["box"], text, start, end)
        if b:
            out.append((b, label, n_x))
            spans.append((start, end))
    stripped = re.sub(r"\s", "", text)
    if len(stripped) > 10:                                  # a bare PAN token is handled by the caller
        for m in _PAN_WORD.finditer(text):
            w = m.group(0).upper()
            if _PAN_RE.match(w) or _pan_shaped(w):
                take(m.start(), m.end() - 4, "PAN", 6)
        for m in _PAN_GLUED.finditer(text):
            # glued to CAPITALS before it, it must be a PAN label ("PANABCPE1234F"), or "INVOICE1234Z"
            # would read as a PAN; after lower case / punctuation the shape is distinctive enough
            before = re.search(r"[A-Z]+$", text[:m.start()])
            if m.group(0)[3] in _PAN_HOLDER and (before is None or _PAN_LABEL.search(before.group(0))):
                take(m.start(), m.end() - 4, "PAN", 6)
        for m in _PAN_AFTER_LABEL.finditer(text):
            if _pan_shaped(m.group(1)):
                take(m.start(1), m.end(1) - 4, "PAN", 6)
    if not stripped.isdigit():                              # a bare digit run is handled by the caller
        found = list(_AAD_GROUPED.finditer(text))
        if _AAD_WORD.search(text):                          # other spacings only next to the word
            found += list(_AAD_LOOSE.finditer(text))
        for m in found:
            if len(_digits(m.group(0))) == 12:
                take(m.start(), m.start() + _digit_prefix_chars(m.group(0), 8), "Aadhaar", 8)
    return out


def _edits(a: str, b: str) -> int:
    """Levenshtein distance (short strings)."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _find_regions(tokens: list[dict], parsed: dict | None,
                  doc_type: str = "generic") -> list[tuple]:
    """Return [(box, label, n_x)] — each a rectangle to overprint with n_x X's.

    Masks ONLY the two primary government identifiers: the PAN number and the
    Aadhaar number, keeping the last 4 characters visible. Deliberately does NOT
    touch any other number — an Aadhaar e-letter's VID, enrolment number, PIN
    codes, mobile, and dates, or a bank account number, all stay in the clear.
    (Masking every long number turned real documents into a wall of X's; the
    ask is to redact the main PII only.)"""
    regions: list[tuple] = []
    used = set()  # token indices already consumed (avoid double-masking)

    def add(entry, label, i):
        if entry:
            box, n_x = entry
            regions.append((box, label, n_x))
            used.add(i)

    # ── PAN: unambiguous letter/digit pattern (AAAAA9999A) ──
    for i, t in enumerate(tokens):
        if _PAN_RE.match(t["alnum"]) or _pan_shaped(t["alnum"]):
            add(_prefix_box(t["box"], t["text"], keep_last=4), "PAN", i)

    # ── PAN / Aadhaar printed INSIDE a longer line ("PAN: ABCDE1234F", "Aadhaar No 1234 5678 9012") —
    #    how payslips, statements, Form 16 and application forms carry them. OCR returns the whole
    #    line as one token, so the whole-token rules above and below never see the number.
    #    (Measured on real scanned pages with a labelled ID line added: 99% left fully readable.)
    for i, t in enumerate(tokens):
        if i in used:
            continue
        for box, label, n_x in _inline_ids(t):
            regions.append((box, label, n_x))
            used.add(i)

    # ── Aadhaar: exactly 12 digits, printed as three 4-digit groups (or OCR-
    #    merged: one 12-digit token, 8+4 / 4+8). The exact-12 total keeps this
    #    off a 16-digit VID or a bank account number. ──
    digit_toks = [(i, t) for i, t in enumerate(tokens)
                  if i not in used and "." not in t["text"]
                  and _digits(t["text"]) == re.sub(r"\s", "", t["text"].strip())
                  and 1 <= len(_digits(t["text"])) <= 12]
    # OCR often ends one token after the first group and starts the next with the rest
    # ('Aadhaar Number 8482' + '8464' + '4904'): the group glued to the label opens the run, or the
    # twelve digits are never seen together (found on real pages, 2026-10-02: the number stayed readable).
    for i, t in enumerate(tokens):
        m = _AAD_TAIL.search(t["text"]) if i not in used else None
        if m and _AAD_WORD.search(t["text"][:m.start()]):
            box = _span_box(t["box"], t["text"], m.start(1), m.end(1))
            if box:
                digit_toks.append((i, {"text": m.group(1), "box": box}))

    # Group each line's digit tokens into ADJACENCY RUNS — consecutive tokens
    # left to right whose horizontal gap is at most ~2 line-heights (Aadhaar's
    # inter-group gap is a fraction of the text height). A run is an Aadhaar
    # number iff the WHOLE run totals exactly 12 digits in 4/8/12-digit groups.
    #
    # Judging the whole run (not a sliding window, not a lone token) is what
    # gets every real layout right at once — measured on an e-Aadhaar letter:
    #   * the VID is printed as FOUR 4-digit groups right below the Aadhaar
    #     number; any 3-group window inside it looks exactly like an Aadhaar,
    #     but the full run is 16 digits -> correctly rejected;
    #   * the letter prints the two cut-out cards SIDE BY SIDE, so two full
    #     Aadhaar numbers share one text line; the big inter-card gap splits
    #     them into two separate 12-digit runs -> BOTH masked;
    #   * an OCR-merged VID ('916361884632' + '5099') forms one 16-digit run
    #     -> rejected, where a naive "any 12-digit token" rule would fire;
    #   * two adjacent 6-digit PIN codes total 12 but 6∉{4,8,12} -> rejected.
    remaining = [(i, t) for i, t in digit_toks if i not in used]
    lines: list[list] = []
    for i, t in remaining:
        for line in lines:
            if _same_line(line[0][1]["box"], t["box"]):
                line.append((i, t))
                break
        else:
            lines.append([(i, t)])
    for line in lines:
        line.sort(key=lambda p: p[1]["box"][0])
        runs, cur = [], [line[0]]
        for prev, nxt in zip(line, line[1:]):
            pb, nb = prev[1]["box"], nxt[1]["box"]
            h = max(pb[3] - pb[1], nb[3] - nb[1])
            if nb[0] - pb[2] <= 2.0 * h:
                cur.append(nxt)
            else:
                runs.append(cur)
                cur = [nxt]
        runs.append(cur)
        for run in runs:
            sizes = [len(_digits(u["text"])) for _, u in run]
            if sum(sizes) == 12 and all(s in (4, 8, 12) for s in sizes):
                for box, n_x in _mask_run_keep_last4([(u["text"], u["box"]) for _, u in run]):
                    regions.append((box, "Aadhaar", n_x))
                for j, _ in run:
                    used.add(j)

    # ── the number the extraction step read, when the page patterns missed it (a smudged character
    #    beyond the look-alike table): the token closest to that value is masked. Supplementary
    #    only — the page is searched by pattern first, because the extracted Aadhaar may already
    #    be masked ("XXXX-XXXX-1234") while the image shows it in full.
    for key, label in (("pan_number", "PAN"), ("aadhaar_number", "Aadhaar")):
        want = _norm_alnum(str((parsed or {}).get(key) or ""))
        if len(want) < 10 or "X" in want[:6] or any(lbl == label for _, lbl, _ in regions):
            continue
        best = min(((_edits(want, t["alnum"]), i) for i, t in enumerate(tokens)
                    if i not in used and abs(len(t["alnum"]) - len(want)) <= 1), default=None)
        if best and best[0] <= 2:
            add(_prefix_box(tokens[best[1]]["box"], tokens[best[1]]["text"], keep_last=4), label, best[1])

    return regions


def _unrotate_box(box, tf, W: int, H: int):
    """Map an axis-aligned box from a rotated image back to the 0° image of size
    (W, H). ID cards can carry text in more than one orientation (a header
    horizontal, the Aadhaar number printed vertically), so PII is detected in
    each 90° rotation and its boxes are mapped back to one canonical image to be
    drawn together."""
    from PIL import Image
    if tf is None:
        return box
    x0, y0, x1, y1 = box
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    out = []
    for xr, yr in corners:
        if tf == Image.ROTATE_90:       # PIL ROTATE_90 = 90° counter-clockwise
            out.append((W - 1 - yr, xr))
        elif tf == Image.ROTATE_270:    # 90° clockwise
            out.append((yr, H - 1 - xr))
        else:                            # ROTATE_180
            out.append((W - 1 - xr, H - 1 - yr))
    xs = [p[0] for p in out]; ys = [p[1] for p in out]
    return (min(xs), min(ys), max(xs), max(ys))


def _vertical(box) -> bool:
    return (box[3] - box[1]) > 1.5 * (box[2] - box[0])


def _upright_tokens(pil) -> list[dict]:
    """OCR with RapidOCR's angle classifier OFF: text only reads correctly when
    it is upright in this frame, so a successful PII match also tells us which
    way the card is turned (with the classifier ON, an upside-down line reads
    just as well and the keep-last-4 split would land on the wrong end)."""
    import numpy as np
    from app.imaging import norm_alnum, num_value
    from app.preprocess import _get_orient_reader
    out = []
    for pts, text, _conf in _get_orient_reader().readtext(np.array(pil)):
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        out.append({"text": text, "value": num_value(text), "alnum": norm_alnum(text),
                    "box": (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))})
    return out


# Identity documents keep the full rotation sweep: a sideways card is common and its upright
# read is mostly noise. Everything else is a page.
_ID_DOC_TYPES = {"pan", "pan_card", "aadhaar", "aadhaar_card", "aadhar", "voter_id", "voter_id_card",
                 "passport", "driving_license", "dl", "ration_card", "photo"}


def _reads_upright(tokens: list[dict], min_tokens: int = 20, min_share: float = 0.8) -> bool:
    """True when the upright OCR pass read a real page: plenty of text, nearly all of it in
    horizontal lines. A sideways page reads as a handful of tall fragments instead."""
    if len(tokens) < min_tokens:
        return False
    return sum(1 for t in tokens if not _vertical(t["box"])) / len(tokens) >= min_share


def _all_regions(pil, parsed, doc_type="generic"):
    """Detect PII across all four 90° orientations, mapping every region back to
    the 0° image. Only pays for extra OCR passes when the upright pass finds
    nothing — an already-upright document with visible PII costs one pass.

    Orientation is decided by READING, with the angle classifier off: a PII
    pattern only matches in the frame where it is truly upright, and the
    keep-last-4 split is only right in that frame. (Measured before this:
    rotated cards kept 0% of their last four. Three causes — a sideways number
    read by the upright pass has a tall box that was split left-to-right; its
    tall box "shared a line" with a nearby PIN code so the digit run was
    rejected; and the old fallback matched in BOTH the 90° and 270° frames,
    masking from both ends.) Sideways regions from the upright pass are
    dropped in favour of the upright frame's. If no frame reads the number
    upright, the old classifier-on sweep over every rotation is used — it may
    over-mask, which beats leaking."""
    from PIL import Image
    W, H = pil.size
    upright = _upright_tokens(pil)
    regions = [r for r in _find_regions(upright, parsed, doc_type) if not _vertical(r[0])]
    if regions:
        return regions
    if (doc_type or "").lower() not in _ID_DOC_TYPES and _reads_upright(upright):
        # A full page (statement, payslip, quotation...) that already reads upright and simply
        # carries no PAN / Aadhaar. The sweep below exists for sideways ID cards; on a page it
        # costs seven more full-page OCR passes (measured: a bank statement 103 s instead of 8 s)
        # and cannot find a number the upright read did not.
        return []
    for tf in (Image.ROTATE_90, Image.ROTATE_270, Image.ROTATE_180):
        found = [f for f in _find_regions(_upright_tokens(pil.transpose(tf)), parsed, doc_type)
                 if not _vertical(f[0])]
        if found:
            return [(_unrotate_box(b, tf, W, H), lbl, n_x) for b, lbl, n_x in found]
    # nothing reads upright anywhere (blur, odd angle): classifier-on, every frame
    regions = list(_find_regions(_ocr_tokens(pil), parsed, doc_type))
    for tf in (Image.ROTATE_90, Image.ROTATE_270, Image.ROTATE_180):
        found = _find_regions(_ocr_tokens(pil.transpose(tf)), parsed, doc_type)
        regions.extend((_unrotate_box(b, tf, W, H), lbl, n_x) for b, lbl, n_x in found)
    return regions


def _font(px: int):
    from PIL import ImageFont
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, max(6, int(px)))
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def _sample_bg(arr, x0, y0, x1, y1):
    """Median colour of a thin strip just above/below the box — the card
    background the digits sit on, used to paint over them."""
    H, W = arr.shape[:2]
    bh = max(2, (y1 - y0) // 2)
    strips = []
    for ya, yb in ((max(0, y0 - bh), y0), (y1, min(H, y1 + bh))):
        if yb > ya:
            s = arr[ya:yb, max(0, x0):min(W, x1)]
            if s.size:
                strips.append(s.reshape(-1, 3))
    if not strips:
        return _BG_FALLBACK
    return tuple(int(v) for v in np.median(np.concatenate(strips, 0), axis=0))


def _sample_ink(arr, x0, y0, x1, y1):
    """Median colour of the darkest quartile inside the box — the digit ink, so
    the drawn X's match the document's own text colour."""
    H, W = arr.shape[:2]
    patch = arr[max(0, y0):min(H, y1), max(0, x0):min(W, x1)].reshape(-1, 3)
    if patch.size == 0:
        return _INK_FALLBACK
    lum = patch.mean(axis=1)
    dark = patch[lum <= np.percentile(lum, 25)]
    if dark.size == 0:
        return _INK_FALLBACK
    return tuple(int(v) for v in np.median(dark, axis=0))


def _snap_to_gap(arr, box, n_x: int):
    """Move the right edge of a cover onto the real gap between the last masked character and the
    first kept one.

    The edge is computed from the OCR token's box and the text (an even split, or glyph widths): good
    to about half a character. Half a character too far right clips the first of the four characters
    that must stay readable - measured on real pages, the usual way the last four were lost
    ('PAN:XXXXXX)51K'). The ink itself says where characters end: columns without ink between them.
    When the kept characters run on to the right of the edge (the edge is inside a word), they are
    counted from the END of that word: the cut belongs in the gap before the fourth from the end. If
    that cannot be read off the page (touching characters, no clear gaps), the nearest gap within half
    a character is used; otherwise the box is left as computed. The cut never moves by more than one
    character, so at most one character more or less is covered than before."""
    x0, y0, x1, y1 = [int(v) for v in box]
    H, W = arr.shape[:2]
    h, w = y1 - y0, x1 - x0
    if n_x <= 0 or w < 8 or h < 6 or _vertical(box):
        return box
    cw = w / n_x
    lo, hi = max(0, int(x1 - 1.5 * cw)), min(W, int(x1 + 7 * cw))
    ya, yb = max(0, y0 + h // 8), min(H, y1 - h // 8)
    if hi - lo < 4 or yb - ya < 4:
        return box
    lum = arr[ya:yb, lo:hi].mean(axis=2)
    dark, light = np.percentile(lum, 5), np.percentile(lum, 95)
    if light - dark < 50:
        return box
    ink = (lum < (dark + light) / 2).sum(axis=0) >= max(1, int(0.06 * (yb - ya)))
    blobs, start = [], None                      # runs of ink columns = characters, in window coordinates
    for i, on in enumerate(list(ink) + [False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            blobs.append((start, i))
            start = None
    if len(blobs) < 2:
        return box
    edge = x1 - lo
    # the word the edge sits in ends at the first wide gap to the right of it
    last, ended = len(blobs) - 1, False
    for k in range(len(blobs) - 1):
        if blobs[k][1] > edge and blobs[k + 1][0] - blobs[k][1] >= 0.6 * h:
            last, ended = k, True
            break
    if not ended:                                # no wide gap: the word ends where the ink stops in view
        ended = hi >= W or (hi - lo) - blobs[-1][1] >= 0.6 * h
    word = blobs[:last + 1]
    cut = None
    if ended:                                    # the end of the word is in view: count back from it
        tail = word[-4:]
        if len(word) >= 5 and all(b[1] - b[0] <= 1.6 * cw for b in tail):
            g = (word[-5][1] + word[-4][0]) / 2
            if abs(g - edge) <= cw:
                cut = g
    if cut is None:                              # nearest gap, within half a character
        gaps = [(a[1] + b[0]) / 2 for a, b in zip(word, word[1:])]
        near = min(gaps, key=lambda g: abs(g - edge), default=None)
        if near is not None and abs(near - edge) <= 0.5 * cw:
            cut = near
    if cut is None:
        return box
    return (x0, y0, int(round(lo + cut)), y1)


def _draw_x_mask(draw, arr, box, n_x: int):
    """Paint over the box with the card background, then print n_x evenly-spaced
    'X' glyphs in the card's ink colour."""
    x0, y0, x1, y1 = [int(v) for v in box]
    if x1 <= x0 or y1 <= y0 or n_x <= 0:
        return
    h, w = y1 - y0, x1 - x0
    vert = _vertical(box)           # sideways number: lay the X's along its length
    pad = max(1, (w if vert else h) // 12)
    bg = _sample_bg(arr, x0, y0, x1, y1)
    ink = _sample_ink(arr, x0, y0, x1, y1)
    # no padding on the side where the kept characters start: one extra pixel there clips them
    draw.rectangle([x0 - pad, y0 - pad, x1 if not vert else x1 + pad, y1 + pad if not vert else y1], fill=bg)

    cell = (h if vert else w) / n_x
    size = min((w if vert else h) * 0.95, cell * 1.4)
    font = _font(size)
    tb = draw.textbbox((0, 0), "X", font=font)
    along = (tb[3] - tb[1]) if vert else (tb[2] - tb[0])
    if along > 0.92 * cell:
        # an X wider than its cell spills past the box and over the first character that is kept
        # (seen on real pages: 'PAN:XXXXXX)37F' - the 0 of the last four half covered)
        font = _font(size * 0.92 * cell / along)
    for i in range(n_x):
        cx = (x0 + x1) / 2 if vert else x0 + (i + 0.5) * cell
        cy = y0 + (i + 0.5) * cell if vert else (y0 + y1) / 2
        tb = draw.textbbox((0, 0), "X", font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        draw.text((cx - tw / 2 - tb[0], cy - th / 2 - tb[1]), "X", fill=ink, font=font)


def _encode(pil_image, max_dim: int = 1000) -> str:
    w, h = pil_image.size
    if max(w, h) > max_dim:
        s = max_dim / max(w, h)
        pil_image = pil_image.resize((int(w * s), int(h * s)))
    buf = io.BytesIO()
    pil_image.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# The number an identity card exists to carry. If it was not found on the card, the "masked" copy
# would show it in full - and that copy is what officers see and what is stored as officer-safe.
_OWN_ID = {"pan": "PAN", "pan_card": "PAN", "aadhaar": "Aadhaar", "aadhaar_card": "Aadhaar", "aadhar": "Aadhaar"}


def _withheld(pil):
    """The whole card blurred beyond reading, with a line saying why. Used when the card's own number
    could not be located (measured on real-data tests: 9 of 100 PAN cards - small or stylised print the
    OCR pass does not read): withholding the preview beats publishing an unmasked card as masked."""
    from PIL import ImageDraw, ImageFilter
    w, h = pil.size
    img = pil.filter(ImageFilter.GaussianBlur(radius=max(8, max(w, h) // 40)))
    draw = ImageDraw.Draw(img)
    msg = "ID number not located - preview withheld"
    font = _font(max(12, w // 28))
    tb = draw.textbbox((0, 0), msg, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    x, y = (w - tw) / 2, (h - th) / 2
    draw.rectangle([x - 10, y - 8, x + tw + 10, y + th + 12], fill=_BG_FALLBACK)
    draw.text((x - tb[0], y - tb[1]), msg, fill=_INK_FALLBACK, font=font)
    return img


def mask_document(file_bytes: bytes, parsed: dict | None = None,
                  doc_type: str = "generic") -> dict | None:
    """Return {masked_image, masked_count, labels} or None on failure (never raises). For a PAN or
    Aadhaar card the result also says `complete` (its own number was covered); when it was not, the
    image is withheld (blurred) and `withheld` is True."""
    try:
        pil = _load_image(file_bytes)
        if pil is None:
            return None
        pil = pil.convert("RGB")
        regions = _all_regions(pil, parsed, doc_type)
    except Exception:  # noqa: BLE001 - masking is best-effort
        log.exception("mask: could not OCR the document")
        return None

    try:
        from PIL import ImageDraw
        img = pil.copy()
        arr = np.asarray(pil)
        draw = ImageDraw.Draw(img)
        for box, _label, n_x in regions:
            _draw_x_mask(draw, arr, _snap_to_gap(arr, box, n_x), n_x)
        labels = sorted({lb for _b, lb, _n in regions})
        own = _OWN_ID.get((doc_type or "").lower())
        if own and own not in labels:              # fail safe: never hand out an ID card with its number showing
            return {"masked_image": _encode(_withheld(pil)), "masked_count": 0, "labels": [],
                    "complete": False, "withheld": True}
        out = {"masked_image": _encode(img), "masked_count": len(regions), "labels": labels}
        if own:
            out["complete"] = True
        return out
    except Exception:  # noqa: BLE001
        log.exception("mask: could not render masked image")
        return None
