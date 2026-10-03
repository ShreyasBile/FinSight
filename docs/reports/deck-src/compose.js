// Compose the two photos into slide artwork (navy overlays so text and the logo stay readable).
// Sources (Unsplash licence), saved as photos/title_src.jpg and photos/problem_src.jpg:
//   unsplash.com/photos/ZH4FUYiaczY  (Dimitri Karastelev)   ·   unsplash.com/photos/snNHKZ-mGfE  (Wesley Tingey)
// The composed results are already in img/, so this only needs running to change the artwork.
const sharp = require("sharp");
const path = require("path");
const P = path.join(__dirname, "photos"), IMG = path.join(__dirname, "img");
const NAVY = "#0F172A";

async function main() {
  // title: 16:9 canvas, photo right-aligned, navy fading out from the left
  const W = 2560, H = 1440, pw = 2160;
  const photo = await sharp(path.join(P, "title_src.jpg")).resize(pw, H, { fit: "cover", position: "centre" }).modulate({ brightness: 1.25 }).toBuffer();
  const grad = Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}">
    <defs>
      <linearGradient id="h" x1="0" x2="1" y1="0" y2="0">
        <stop offset="0" stop-color="${NAVY}" stop-opacity="1"/><stop offset="0.34" stop-color="${NAVY}" stop-opacity="0.97"/>
        <stop offset="0.58" stop-color="${NAVY}" stop-opacity="0.62"/><stop offset="1" stop-color="${NAVY}" stop-opacity="0.22"/>
      </linearGradient>
      <linearGradient id="v" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stop-color="${NAVY}" stop-opacity="0.35"/><stop offset="0.25" stop-color="${NAVY}" stop-opacity="0"/>
        <stop offset="0.78" stop-color="${NAVY}" stop-opacity="0"/><stop offset="1" stop-color="${NAVY}" stop-opacity="0.8"/>
      </linearGradient>
    </defs>
    <rect width="100%" height="100%" fill="url(#h)"/><rect width="100%" height="100%" fill="url(#v)"/></svg>`);
  await sharp({ create: { width: W, height: H, channels: 3, background: NAVY } })
    .composite([{ input: photo, left: W - pw, top: 0 }, { input: grad, left: 0, top: 0 }])
    .jpeg({ quality: 88 }).toFile(path.join(IMG, "title_bg.jpg"));

  // problem slide: a tall panel for the right side of the slide
  const w = 1340, h = 1800;
  const tall = await sharp(path.join(P, "problem_src.jpg")).resize(w, h, { fit: "cover", position: "centre" }).toBuffer();
  const g2 = Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">
    <defs><linearGradient id="v" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="${NAVY}" stop-opacity="0.18"/><stop offset="0.62" stop-color="${NAVY}" stop-opacity="0.22"/>
      <stop offset="1" stop-color="${NAVY}" stop-opacity="0.9"/></linearGradient></defs>
    <rect width="100%" height="100%" fill="url(#v)"/></svg>`);
  await sharp(tall).composite([{ input: g2, left: 0, top: 0 }]).jpeg({ quality: 88 }).toFile(path.join(IMG, "problem_panel.jpg"));
  console.log("composed");
}
main().catch((e) => { console.error(e); process.exit(1); });
