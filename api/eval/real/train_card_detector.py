"""A learned card outline for phone photos of ID cards (development data only).

Why: the cleanup finds the card by edges and contours. That works on a table or a keyboard (88-97% on the
test frames) and fails when a hand or clutter breaks the outline (30-36%). A small segmentation network
that has seen cards on busy backgrounds and under fingers does not need a clean edge.

Training data is synthetic - and has to be: the MIDV-500 frames of this evaluation come from 7-10 videos
of one card, so development and test frames show the same scenes, and training on them would leak the
test set. Instead real ID-card templates from SIDTD's development groups are composited onto varied
backgrounds (tables, cloth, keyboards, paper, clutter of other cards and papers), in perspective, under
synthetic fingers, with blur, noise, shadows and JPEG compression. The MIDV development frames are used
ONLY to judge the result (card outline IoU >= 0.85 with the true outline, as in the test).

    python -m eval.real.train_card_detector [--save]
"""
from __future__ import annotations

import io
import json
import math
import random
import sys
import time

import numpy as np

from eval import common as C
from eval.real import core as R

NET = 160                       # the network sees a 160 x 160 letterboxed frame
MODEL_PATH = C.API_DIR / "app" / "models" / "card_outline_v1.pt"


# ── network ─────────────────────────────────────────────────────────────────
def unet(base: int = 12):
    import torch
    import torch.nn as nn

    def cbr(a, b):
        return nn.Sequential(nn.Conv2d(a, b, 3, padding=1), nn.BatchNorm2d(b), nn.ReLU(inplace=True),
                             nn.Conv2d(b, b, 3, padding=1), nn.BatchNorm2d(b), nn.ReLU(inplace=True))

    class UNet(nn.Module):
        def __init__(self):
            super().__init__()
            c = [base, base * 2, base * 4, base * 8]
            self.e1, self.e2, self.e3, self.e4 = cbr(3, c[0]), cbr(c[0], c[1]), cbr(c[1], c[2]), cbr(c[2], c[3])
            self.pool = nn.MaxPool2d(2)
            self.u3, self.d3 = nn.ConvTranspose2d(c[3], c[2], 2, 2), cbr(c[3], c[2])
            self.u2, self.d2 = nn.ConvTranspose2d(c[2], c[1], 2, 2), cbr(c[2], c[1])
            self.u1, self.d1 = nn.ConvTranspose2d(c[1], c[0], 2, 2), cbr(c[1], c[0])
            self.out = nn.Conv2d(c[0], 1, 1)

        def forward(self, x):
            e1 = self.e1(x)
            e2 = self.e2(self.pool(e1))
            e3 = self.e3(self.pool(e2))
            e4 = self.e4(self.pool(e3))
            d3 = self.d3(torch.cat([self.u3(e4), e3], 1))
            d2 = self.d2(torch.cat([self.u2(d3), e2], 1))
            d1 = self.d1(torch.cat([self.u1(d2), e1], 1))
            return self.out(d1)
    return UNet()


def letterbox(img: np.ndarray, size: int = NET):
    """(size x size image, scale, x offset, y offset) - the frame scaled to fit, centred, grey padding."""
    import cv2
    h, w = img.shape[:2]
    s = size / max(h, w)
    nw, nh = max(1, round(w * s)), max(1, round(h * s))
    out = np.full((size, size, 3), 127, np.uint8)
    ox, oy = (size - nw) // 2, (size - nh) // 2
    out[oy:oy + nh, ox:ox + nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    return out, s, ox, oy


# ── synthetic scenes ────────────────────────────────────────────────────────
SKIN = [(198, 134, 66), (224, 172, 105), (141, 85, 36), (255, 205, 148), (234, 192, 134), (115, 70, 40)]


def _background(rng, w, h, pages, cards):
    import cv2
    kind = rng.choice(["wood", "solid", "cloth", "keyboard", "paper", "clutter", "clutter"])
    base = np.array([rng.randint(30, 230) for _ in range(3)], np.float32)
    bg = np.ones((h, w, 3), np.float32) * base
    if kind == "wood":
        y = np.arange(h)[:, None] + 8 * np.sin(np.arange(w)[None, :] / rng.uniform(20, 80))
        bg *= (0.85 + 0.15 * np.sin(y / rng.uniform(3, 12)))[..., None]
    elif kind == "cloth":
        n = cv2.GaussianBlur(np.random.default_rng(rng.randint(0, 1 << 30)).normal(0, 25, (h, w)).astype(np.float32), (0, 0), rng.uniform(1, 4))
        bg += n[..., None]
    elif kind == "keyboard":
        bg[:] = rng.randint(15, 60)
        k = rng.randint(18, 40)
        for yy in range(rng.randint(0, k), h, k):
            for xx in range(rng.randint(0, k), w, k):
                cv2.rectangle(bg, (xx + 3, yy + 3), (xx + k - 3, yy + k - 3), (rng.randint(40, 90),) * 3, -1)
                if rng.random() < 0.6:
                    cv2.putText(bg, rng.choice("QWERTYASDFGZXCVB1234"), (xx + 6, yy + k - 8), cv2.FONT_HERSHEY_SIMPLEX,
                                0.4, (200, 200, 200), 1)
    elif kind == "paper" and pages:
        p = pages[rng.randrange(len(pages))]
        bg = cv2.resize(p, (w, h)).astype(np.float32)
    else:                                                     # clutter: papers, other cards, objects
        for _ in range(rng.randint(3, 9)):
            src = rng.choices(["page", "card", "shape"], weights=[0.45, 0.1, 0.45])[0]
            pw, ph = int(w * rng.uniform(0.1, 0.5)), int(h * rng.uniform(0.1, 0.5))
            if src == "card":                                 # another card nearby: smaller than the one to find
                pw = int(w * rng.uniform(0.1, 0.22))
            x, y = rng.randint(-pw // 3, w - pw // 2), rng.randint(-ph // 3, h - ph // 2)
            if src == "page" and pages:
                patch = cv2.resize(pages[rng.randrange(len(pages))], (pw, ph)).astype(np.float32)
            elif src == "card" and cards:
                patch = cv2.resize(cards[rng.randrange(len(cards))], (pw, int(pw / 1.585))).astype(np.float32)
                ph = patch.shape[0]
            else:
                patch = np.ones((ph, pw, 3), np.float32) * np.array([rng.randint(0, 255) for _ in range(3)])
            x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + pw, w), min(y + ph, h)
            if x1 > x0 and y1 > y0:
                bg[y0:y1, x0:x1] = patch[y0 - y:y1 - y, x0 - x:x1 - x]
    return np.clip(bg + np.random.default_rng(rng.randint(0, 1 << 30)).normal(0, 4, bg.shape), 0, 255)


def _fingers(rng, img, w, h, quad):
    """Skin-coloured fingers reaching in from a frame edge over the card's edge (the label is unchanged:
    the outline to find is the whole card's)."""
    import cv2
    tone = np.array(rng.choice(SKIN), np.float32)[::-1]       # BGR
    edge_pt = quad[rng.randrange(4)]
    side = rng.choice(["left", "right", "bottom", "top"])
    for _ in range(rng.randint(1, 4)):
        tip = edge_pt + np.array([rng.uniform(-60, 60), rng.uniform(-60, 60)])
        start = {"left": (0, tip[1] + rng.uniform(-80, 80)), "right": (w, tip[1] + rng.uniform(-80, 80)),
                 "bottom": (tip[0] + rng.uniform(-80, 80), h), "top": (tip[0] + rng.uniform(-80, 80), 0)}[side]
        thick = int(min(w, h) * rng.uniform(0.04, 0.08))
        layer = np.zeros((h, w), np.uint8)
        cv2.line(layer, tuple(int(v) for v in start), tuple(int(v) for v in tip), 255, thick)
        cv2.circle(layer, tuple(int(v) for v in tip), thick // 2, 255, -1)
        a = cv2.GaussianBlur(layer, (0, 0), 2).astype(np.float32)[..., None] / 255
        shade = 0.8 + 0.2 * np.random.default_rng(rng.randint(0, 1 << 30)).random((h, w, 1))
        img[:] = img * (1 - a) + (tone * shade) * a


def scene(rng, cards, pages):
    """(frame BGR uint8, card mask uint8) at a random phone aspect."""
    import cv2
    w, h = rng.choice([(384, 216), (320, 240), (216, 384), (240, 320)])
    img = _background(rng, w, h, pages, cards)
    card = cards[rng.randrange(len(cards))]
    ch, cw = card.shape[:2]
    scale = rng.uniform(0.35, 0.85) * min(w / cw, h / ch) if rng.random() < 0.7 else rng.uniform(0.35, 0.85) * max(w, h) / cw
    tw, th = cw * scale, ch * scale
    ang = math.radians(rng.choice([rng.uniform(-25, 25), rng.uniform(-25, 25), rng.uniform(65, 115), rng.uniform(155, 205)]))
    cx = rng.uniform(0.25, 0.75) * w if rng.random() > 0.15 else rng.uniform(-0.05, 1.05) * w   # 15% partly out
    cy = rng.uniform(0.25, 0.75) * h if rng.random() > 0.15 else rng.uniform(-0.05, 1.05) * h
    corners = np.array([[-tw / 2, -th / 2], [tw / 2, -th / 2], [tw / 2, th / 2], [-tw / 2, th / 2]])
    rot = np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]])
    quad = corners @ rot.T + [cx, cy]
    quad += np.array([[rng.uniform(-0.08, 0.08) * tw, rng.uniform(-0.08, 0.08) * th] for _ in range(4)])   # perspective
    M = cv2.getPerspectiveTransform(np.float32([[0, 0], [cw, 0], [cw, ch], [0, ch]]), np.float32(quad))
    warped = cv2.warpPerspective(card, M, (w, h)).astype(np.float32)
    mask = cv2.warpPerspective(np.full((ch, cw), 255, np.uint8), M, (w, h))
    a = cv2.GaussianBlur(mask, (3, 3), 0).astype(np.float32)[..., None] / 255
    img = img * (1 - a) + warped * a
    if rng.random() < 0.45:
        _fingers(rng, img, w, h, quad)
    # light, camera
    img = img * rng.uniform(0.6, 1.3) + rng.uniform(-30, 30)
    img *= np.array([rng.uniform(0.85, 1.15) for _ in range(3)])
    if rng.random() < 0.5:                                    # a shadow across part of the frame
        g = np.linspace(rng.uniform(0.5, 1), rng.uniform(0.8, 1.1), w)[None, :, None] if rng.random() < 0.5 else \
            np.linspace(rng.uniform(0.5, 1), rng.uniform(0.8, 1.1), h)[:, None, None]
        img *= g
    img = np.clip(img, 0, 255).astype(np.uint8)
    if rng.random() < 0.5:
        img = cv2.GaussianBlur(img, (0, 0), rng.uniform(0.3, 1.8))
    if rng.random() < 0.25:
        k = rng.randint(3, 9)
        kern = np.zeros((k, k), np.float32)
        kern[k // 2] = 1.0 / k
        img = cv2.filter2D(img, -1, kern)
    ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(35, 90)])
    img = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    return img, (mask > 127).astype(np.uint8)


def dataset(n, rng, cards, pages, log=print):
    X = np.zeros((n, 3, NET, NET), np.uint8)
    Y = np.zeros((n, 1, NET, NET), np.uint8)
    t = time.time()
    for i in range(n):
        img, m = scene(rng, cards, pages)
        lb, s, ox, oy = letterbox(img)
        import cv2
        mm = np.zeros((NET, NET), np.uint8)
        nh, nw = round(m.shape[0] * s), round(m.shape[1] * s)
        mm[oy:oy + nh, ox:ox + nw] = cv2.resize(m, (nw, nh), interpolation=cv2.INTER_NEAREST)
        X[i] = lb.transpose(2, 0, 1)
        Y[i, 0] = mm
    log(f"  {n} synthetic scenes in {time.time() - t:.0f} s")
    return X, Y


def train(X, Y, epochs=14, log=print):
    import torch
    import torch.nn.functional as F
    torch.manual_seed(R.SEED)
    torch.set_num_threads(8)
    net = unet()
    opt = torch.optim.Adam(net.parameters(), lr=2e-3)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
    n = len(X)
    for ep in range(epochs):
        net.train()
        perm = np.random.default_rng(ep).permutation(n)
        tot, t = 0.0, time.time()
        for i in range(0, n, 32):
            idx = perm[i:i + 32]
            xb = torch.from_numpy(X[idx].astype(np.float32) / 255.0)
            yb = torch.from_numpy(Y[idx].astype(np.float32))
            logit = net(xb)
            p = torch.sigmoid(logit)
            dice = 1 - (2 * (p * yb).sum((1, 2, 3)) + 1) / (p.sum((1, 2, 3)) + yb.sum((1, 2, 3)) + 1)
            loss = F.binary_cross_entropy_with_logits(logit, yb) + dice.mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += loss.item() * len(idx)
        sched.step()
        log(f"    epoch {ep + 1}/{epochs} loss {tot / n:.4f} ({time.time() - t:.0f} s)")
    net.eval()
    return net


# ── from a mask to the card's four corners ──────────────────────────────────
def quad_from_prob(prob: np.ndarray, s: float, ox: int, oy: int, frame_hw):
    """Four corners in frame coordinates, or None when the network does not see one clear card."""
    import cv2
    m = (prob > 0.5).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n < 2:
        return None
    k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    comp = (lab == k).astype(np.uint8)
    if comp.sum() < 0.04 * NET * NET:
        return None
    cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    c = max(cnts, key=cv2.contourArea)
    hull = cv2.convexHull(c)
    approx = cv2.approxPolyDP(hull, 0.03 * cv2.arcLength(hull, True), True)
    pts = approx.reshape(-1, 2).astype(np.float32) if len(approx) == 4 else cv2.boxPoints(cv2.minAreaRect(hull))
    pts = (pts - [ox, oy]) / s
    h, w = frame_hw
    return np.clip(pts, [0, 0], [w - 1, h - 1]).astype(np.float32)


def predict(net, frame_bgr: np.ndarray):
    import torch
    lb, s, ox, oy = letterbox(frame_bgr)
    with torch.no_grad():
        prob = torch.sigmoid(net(torch.from_numpy(lb.transpose(2, 0, 1)[None].astype(np.float32) / 255.0)))[0, 0].numpy()
    return quad_from_prob(prob, s, ox, oy, frame_bgr.shape[:2])


def judge(net, frames) -> dict:
    import cv2
    from PIL import Image
    from eval.real.intake_real import _quad_iou
    by = {}
    for it in frames:
        img = cv2.cvtColor(np.array(Image.open(io.BytesIO(it["image"])).convert("RGB")), cv2.COLOR_RGB2BGR)
        q = predict(net, img)
        iou = _quad_iou(q, it["mask"]) if q is not None else 0.0
        b = by.setdefault(it["condition"], [0, 0])
        b[0] += iou >= 0.85
        b[1] += 1
    tot = [sum(v[0] for v in by.values()), sum(v[1] for v in by.values())]
    return {"located": round(tot[0] / tot[1], 4), "n": tot[1],
            "by_background": {k: {"located": round(v[0] / v[1], 4), "n": v[1]} for k, v in sorted(by.items())}}


def main():
    C.setup_env(provider="ollama", db=False)
    import cv2
    from PIL import Image
    from eval.real import evidence_real as E
    from eval.real import intake_real as I
    rng = random.Random(R.SEED)
    cards = []
    for it in E.sidtd_items("dev", 10000):
        im = cv2.imread(str(it["path"]))
        if im is not None:
            cards.append(cv2.resize(im, (480, int(480 * im.shape[0] / im.shape[1]))))
    pages = [cv2.resize(cv2.cvtColor(np.array(Image.open(io.BytesIO(I._png(it["image"]))).convert("RGB")), cv2.COLOR_RGB2BGR), (320, 420))
             for it in I.sroie_items("dev", 60)]
    print(f"  {len(cards)} development ID templates, {len(pages)} development pages", flush=True)
    X, Y = dataset(int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 5000, rng, cards, pages)
    net = train(X, Y)
    res = judge(net, I.midv_items("dev"))
    print("  MIDV development frames:", res, flush=True)
    (R.RESULTS / "card_outline_dev.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    if "--save" in sys.argv:
        import torch
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        torch.save(net.state_dict(), MODEL_PATH)
        print("  saved", MODEL_PATH)


if __name__ == "__main__":
    main()
