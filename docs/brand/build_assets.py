"""Builds every FinSight logo asset from docs/brand/logo-source.png.

    python docs/brand/build_assets.py

The source is the two-tone F mark (navy #052A8C + blue #0770FF) on a transparent
background. Nothing here redraws the logo; it only cleans, crops, recolours the
navy for dark surfaces, and composes lockups / icon tiles around it.

Outputs
  docs/brand/    masters: logo-mark(.png|-light.png) · logo-icon.png · logo-lockup(.png|-light.png) · finsight.ico
  src/assets/brand/   the same marks, tight-cropped and sized for the app
  public/        favicon.ico · favicon-32.png · apple-touch-icon.png · icon-192.png · icon-512.png
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
BRAND = ROOT / "docs" / "brand"
APP = ROOT / "src" / "assets" / "brand"
PUBLIC = ROOT / "public"

BLUE = (7, 112, 255)          # logo blue
TILE = (15, 23, 42)           # dark tile behind the mark on icons (matches the sidebar / deck navy)
INK = (15, 23, 42)            # "FinSight" on light backgrounds
FONT = "C:/Windows/Fonts/Roboto-Bold.ttf"


def load_mark() -> Image.Image:
    """Source -> tight-cropped RGBA. The generator left a faint halo (alpha 1-31) around
    the shape; it is invisible on white but tints dark backgrounds, so it is removed."""
    a = np.array(Image.open(BRAND / "logo-source.png").convert("RGBA"))
    a[..., 3] = np.where(a[..., 3] < 32, 0, a[..., 3])
    im = Image.fromarray(a)
    return im.crop(im.getchannel("A").getbbox())


def light_variant(mark: Image.Image) -> Image.Image:
    """For dark backgrounds: the navy parts (green channel ~42) become white; the blue stays."""
    a = np.array(mark).copy()
    navy = (a[..., 3] > 0) & (a[..., 1] < 76)
    a[navy, 0:3] = 255
    return Image.fromarray(a)


def fit_height(im: Image.Image, h: int) -> Image.Image:
    return im.resize((round(im.width * h / im.height), h), Image.LANCZOS)


def square(mark: Image.Image, size: int, fill: float) -> Image.Image:
    """Mark centred on a transparent square canvas, `fill` = mark height / canvas."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    m = fit_height(mark, round(size * fill))
    canvas.alpha_composite(m, ((size - m.width) // 2, (size - m.height) // 2))
    return canvas


def tile(mark_light: Image.Image, size: int, rounded: bool = True, fill: float = 0.66) -> Image.Image:
    """App-icon tile: dark rounded square with the light mark. Drawn at 4x, then reduced."""
    s = size * 4
    canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    bg = Image.new("RGBA", (s, s), TILE + (255,))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=round(s * 0.225) if rounded else 0, fill=255)
    canvas.paste(bg, (0, 0), mask)
    m = fit_height(mark_light, round(s * fill))
    canvas.alpha_composite(m, ((s - m.width) // 2, (s - m.height) // 2))
    return canvas.resize((size, size), Image.LANCZOS)


def lockup(mark: Image.Image, dark: bool) -> Image.Image:
    """Horizontal logo: mark + "FinSight" (navy or white) + "AI" (blue). Full-resolution master (~1030 x 290)."""
    mh, pad = 260, 30
    font = ImageFont.truetype(FONT, 150)
    m = fit_height(light_variant(mark) if dark else mark, mh)
    cap = font.getbbox("F")[3] - font.getbbox("F")[1]
    gap = 62
    text_w = int(font.getlength("FinSight AI"))
    W, H = pad + m.width + gap + text_w + pad, mh + 2 * pad
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    canvas.alpha_composite(m, (pad, pad))
    d = ImageDraw.Draw(canvas)
    base = H // 2 + cap // 2                    # baseline that centres the capitals on the mark
    x = pad + m.width + gap
    d.text((x, base), "FinSight", font=font, fill=((255, 255, 255) if dark else INK) + (255,), anchor="ls")
    d.text((x + font.getlength("FinSight "), base), "AI", font=font, fill=BLUE + (255,), anchor="ls")
    canvas = canvas.crop(canvas.getchannel("A").getbbox())
    out = Image.new("RGBA", (canvas.width + 2 * pad // 2, canvas.height + pad), (0, 0, 0, 0))
    out.alpha_composite(canvas, (pad // 2, pad // 2))
    return out


def main() -> None:
    for d in (BRAND, APP, PUBLIC):
        d.mkdir(parents=True, exist_ok=True)
    mark = load_mark()
    light = light_variant(mark)

    # masters
    square(mark, 1024, 0.80).save(BRAND / "logo-mark.png", optimize=True)
    square(light, 1024, 0.80).save(BRAND / "logo-mark-light.png", optimize=True)
    tile(light, 512).save(BRAND / "logo-icon.png", optimize=True)
    lockup(mark, dark=False).save(BRAND / "logo-lockup.png", optimize=True)
    lockup(mark, dark=True).save(BRAND / "logo-lockup-light.png", optimize=True)

    # app assets: tight-cropped, 256 px tall (shown at 36-56 px, so still sharp at 3x)
    fit_height(mark, 256).save(APP / "mark.png", optimize=True)
    fit_height(light, 256).save(APP / "mark-light.png", optimize=True)
    for name, dark in (("lockup.png", False), ("lockup-light.png", True)):
        fit_height(lockup(mark, dark), 160).save(APP / name, optimize=True)

    # favicons / app icons
    t512 = tile(light, 512)
    t512.save(PUBLIC / "icon-512.png", optimize=True)
    tile(light, 192).save(PUBLIC / "icon-192.png", optimize=True)
    tile(light, 180, rounded=False, fill=0.62).save(PUBLIC / "apple-touch-icon.png", optimize=True)   # iOS applies its own mask
    tile(light, 32, fill=0.72).save(PUBLIC / "favicon-32.png", optimize=True)
    ico = [tile(light, n, fill=0.72) for n in (48, 32, 16)]
    ico[0].save(PUBLIC / "favicon.ico", format="ICO", sizes=[(48, 48), (32, 32), (16, 16)], append_images=ico[1:])
    # Windows desktop shortcut icon (multi-size)
    t256 = tile(light, 256, fill=0.72)
    t256.save(BRAND / "finsight.ico", format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)])
    print("brand assets written")


if __name__ == "__main__":
    main()
