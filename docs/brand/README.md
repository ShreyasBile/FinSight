# FinSight AI — logo kit

The mark is an "F" built from two rounded leaves with three rising bars: finance (the bars) plus
insight (the leaves reading upward). `logo-source.png` is the original file; everything else is
generated from it by `python docs/brand/build_assets.py` (needs Pillow + numpy; Roboto Bold for the
wordmark).

| File | Use it for |
|---|---|
| `logo-mark.png` | Mark on **light** backgrounds (1024 px, transparent) |
| `logo-mark-light.png` | Mark on **dark** backgrounds — the navy parts turn white (1024 px, transparent) |
| `logo-lockup.png` | Mark + "FinSight AI", light backgrounds (~1030 × 290, transparent) |
| `logo-lockup-light.png` | Mark + "FinSight AI", dark backgrounds |
| `logo-icon.png` | Square app icon: navy rounded tile + light mark (512 px) |
| `finsight.ico` | The same tile as a Windows icon (16-256 px) — used by the desktop shortcut |

Also generated: `src/assets/brand/` (tight-cropped marks and lockups the app imports) and `public/`
(`favicon.ico`, `favicon-32.png`, `apple-touch-icon.png`, `icon-192.png`, `icon-512.png`).

**Colours** — logo blue `#0770FF` · logo navy `#052A8C` · icon tile / dark surfaces `#0F172A`.
The app's own accent is indigo `#4F46E5` (`--blue` in `src/styles/index.css`), which differs from the
logo blue.

**Rules** — use the `-light` files on dark surfaces (the navy parts of the original disappear on
navy); keep clear space of at least half the mark's width around it; don't recolour, stretch, or add
effects; below about 20 px use `logo-icon.png` rather than the bare mark.

**Where it is used** — app sidebar and role picker, browser tab / home-screen icons, the architecture
page, `howtouse.md`, `DESIGN.md`, `api/README.md`, the page header of every Word report in
`docs/reports/`, the project deck (title, section dividers, footer, closing slide), and the Desktop launcher shortcut.
