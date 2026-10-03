// FinSight AI — project presentation (pptxgenjs). Native shapes for every
// diagram so the deck stays editable after import into Canva.
//
//   npm i pptxgenjs react react-dom react-icons sharp
//   node build_deck.js ../07-FinSight-Project-Presentation.pptx
//
// The PDF next to it is that file saved as PDF from PowerPoint. Scores on the
// slides are the test-split figures of api/eval/results_real (report 08).
// img/ holds app screenshots (seeded demo data, synthetic applicant) and two
// photos composed by compose.js: title_bg.jpg (photo: Dimitri Karastelev) and
// problem_panel.jpg (photo: Wesley Tingey), both from Unsplash.
const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const fs = require("fs");
const path = require("path");
const lu = require("react-icons/lu");

const OUT = process.argv[2] || path.join(__dirname, "FinSight-Project-Presentation.pptx");
const IMG = path.join(__dirname, "img");

// ── design tokens ──────────────────────────────────────────────────────────
const F = "Roboto", FM = "Consolas";
const NAVY = "0F172A", NAVY2 = "1E293B", NAVY3 = "334155";
const INDIGO = "4F46E5", INDIGO_D = "3730A3", INDIGO_T = "EEF2FF", INDIGO_M = "C7D2FE";
const INK = "0F172A", BODY = "334155", MUTED = "64748B", LINE = "E2E8F0", BG = "F8FAFC", WHITE = "FFFFFF";
const GREEN = "16A34A", AMBER = "D97706", RED = "DC2626", GREEN_T = "DCFCE7", AMBER_T = "FEF3C7", RED_T = "FEE2E2";
const CREW = {
  intake:       { name: "Intake",                   c: "4F46E5", t: "EEF2FF", icon: "LuFileText" },
  privacy:      { name: "Privacy",                  c: "0891B2", t: "ECFEFF", icon: "LuEyeOff" },
  fraud:        { name: "Identity & fraud evidence", c: "E11D48", t: "FFF1F2", icon: "LuShieldAlert" },
  compliance:   { name: "Compliance",               c: "059669", t: "ECFDF5", icon: "LuScale" },
  underwriting: { name: "Underwriting",             c: "D97706", t: "FFFBEB", icon: "LuCalculator" },
  ops:          { name: "Operations",               c: "7C3AED", t: "F5F3FF", icon: "LuActivity" },
};
const W = 13.333, M = 0.6, CW = W - 2 * M;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.author = "Shreyas Bile";
pres.company = "FinSight AI";
pres.title = "FinSight AI — Agentic Loan Underwriting (final presentation)";

// ── helpers ────────────────────────────────────────────────────────────────
const iconCache = {};
async function icon(name, color, px = 256) {
  const k = name + color;
  if (!iconCache[k]) {
    const svg = ReactDOMServer.renderToStaticMarkup(React.createElement(lu[name], { color: "#" + color, size: px }));
    const buf = await sharp(Buffer.from(svg)).resize(px, px).png().toBuffer();
    iconCache[k] = "image/png;base64," + buf.toString("base64");
  }
  return iconCache[k];
}
function pngSize(file) {
  const b = fs.readFileSync(file);
  return { w: b.readUInt32BE(16), h: b.readUInt32BE(20) };
}
// brand logo (docs/brand in the repo): dark-on-light and light-on-dark variants
function logoImg(s, file, x, y, h) {
  const f = path.join(IMG, file), sz = pngSize(f), w = h * sz.w / sz.h;
  s.addImage({ path: f, x, y, w, h });
  return w;
}
function footer(s, dark) {
  const h = 0.26, numW = 0.4, f = path.join(IMG, dark ? "logo_lockup_light.png" : "logo_lockup.png"), sz = pngSize(f), w = h * sz.w / sz.h;
  s.addImage({ path: f, x: W - M - numW - 0.08 - w, y: 7.07, w, h });
  T(s, String(pageNo), { x: W - M - numW, y: 7.08, w: numW, h: 0.25, fontSize: 9, color: dark ? "64748B" : "94A3B8", align: "right" });
}
const shadow = () => ({ type: "outer", color: "0F172A", blur: 10, offset: 2, angle: 90, opacity: 0.12 });
const T = (s, text, o) => s.addText(text, { fontFace: F, margin: 0, isTextBox: true, color: BODY, ...o });

function rect(s, x, y, w, h, fill, o = {}) {
  s.addShape(o.round === false ? pres.shapes.RECTANGLE : pres.shapes.ROUNDED_RECTANGLE, {
    x, y, w, h, fill: { color: fill }, line: { color: o.line || fill, width: o.lw || 1, dashType: o.dash || "solid" },
    rectRadius: o.r ?? 0.1, shadow: o.shadow ? shadow() : undefined,
  });
}
async function iconCircle(s, x, y, d, name, bg, fg) {
  s.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: bg }, line: { color: bg } });
  const ip = d * 0.56;
  s.addImage({ data: await icon(name, fg), x: x + (d - ip) / 2, y: y + (d - ip) / 2, w: ip, h: ip });
}
// diagram node: rounded box with a bold title and an optional muted sub line
function node(s, x, y, w, h, title, sub, o = {}) {
  rect(s, x, y, w, h, o.fill || WHITE, { line: o.line || LINE, lw: o.lw || 1, r: o.r ?? 0.08, shadow: o.shadow, dash: o.dash });
  const runs = [{ text: title, options: { bold: true, fontSize: o.ts || 12, color: o.tc || INK, breakLine: !!sub } }];
  if (sub) runs.push({ text: sub, options: { fontSize: o.ss || 10, color: o.sc || MUTED } });
  T(s, runs, { x: x + 0.08, y: y + 0.02, w: w - 0.16, h: h - 0.04, align: o.align || "center", valign: "middle", paraSpaceAfter: 2 });
}
function arrow(s, x1, y1, x2, y2, o = {}) {
  s.addShape(pres.shapes.LINE, {
    x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1),
    flipH: x2 < x1, flipV: y2 < y1,
    line: { color: o.color || "94A3B8", width: o.w || 1.5, dashType: o.dash || "solid", endArrowType: o.head === false ? undefined : "triangle" },
  });
}
function chip(s, text, x, y, w, h, fill, color, o = {}) {
  rect(s, x, y, w, h, fill, { line: o.line || fill, r: o.r ?? h / 2 });
  T(s, text, { x, y, w, h, align: "center", valign: "middle", fontSize: o.fs || 10.5, bold: o.bold ?? true, color, fontFace: o.font || F });
}
// chips laid out left→right, wrapping onto new lines; returns bottom y
function chipFlow(s, items, x, y, maxW, o = {}) {
  const h = o.h || 0.36, gap = o.gap || 0.1, fs = o.fs || 10.5, cw = o.cw || 0.07 * (fs / 10.5);
  let cx = x, cy = y;
  for (const it of items) {
    const w = Math.max(o.minW || 0.6, 0.28 + it.length * cw);
    if (cx + w > x + maxW + 0.001 && cx > x) { cx = x; cy += h + gap; }
    chip(s, it, cx, cy, w, h, o.fill || WHITE, o.color || BODY, { line: o.line || LINE, fs, bold: o.bold ?? false, font: o.font });
    cx += w + gap;
  }
  return cy + h;
}
// screenshot in a dark "browser" frame; fits inside w (and h if given)
function frameSize(key, w, h = null) {
  const sz = pngSize(path.join(IMG, key + ".png")), r = sz.w / sz.h, bar = 0.26, pad = 0.05;
  let iw = w - 2 * pad, ih = iw / r;
  if (h && ih + bar + pad > h) { ih = h - bar - pad; iw = ih * r; }
  return { iw, ih, bar, pad, w: iw + 2 * pad, h: ih + bar + pad };
}
function frame(s, key, x, y, w, h = null) {
  const file = path.join(IMG, key + ".png");
  const { iw, ih, bar, pad, w: fw, h: fh } = frameSize(key, w, h);
  rect(s, x, y, fw, fh, NAVY2, { r: 0.07, shadow: true });
  ["F87171", "FBBF24", "34D399"].forEach((c, i) =>
    s.addShape(pres.shapes.OVAL, { x: x + 0.13 + i * 0.15, y: y + 0.085, w: 0.09, h: 0.09, fill: { color: c }, line: { color: c } }));
  s.addImage({ path: file, x: x + pad, y: y + bar, w: iw, h: ih });
  return { w: fw, h: fh };
}
let pageNo = 0;
function content(section, title, lead, notes) {
  const s = pres.addSlide();
  pageNo++;
  s.background = { color: WHITE };
  T(s, section.toUpperCase(), { x: M, y: 0.38, w: 9, h: 0.28, fontSize: 11, bold: true, color: INDIGO, charSpacing: 2 });
  T(s, title, { x: M, y: 0.64, w: CW, h: 0.62, fontSize: 28, bold: true, color: INK });
  if (lead) T(s, lead, { x: M, y: 1.26, w: CW, h: 0.4, fontSize: 14, color: MUTED });
  footer(s, false);
  if (notes) s.addNotes(notes);
  return s;
}
async function divider(num, title, summary, items, iconName, notes) {
  const s = pres.addSlide();
  pageNo++;
  s.background = { color: NAVY };
  T(s, num, { x: M, y: 1.55, w: 4, h: 1.3, fontSize: 88, bold: true, color: "818CF8" });
  T(s, title, { x: M, y: 2.95, w: 8.5, h: 0.9, fontSize: 40, bold: true, color: WHITE });
  T(s, summary, { x: M, y: 3.85, w: 7.8, h: 0.9, fontSize: 17, color: "CBD5E1", valign: "top" });
  T(s, "IN THIS SECTION", { x: M, y: 5.2, w: 4, h: 0.3, fontSize: 10.5, bold: true, color: "94A3B8", charSpacing: 2 });
  chipFlow(s, items, M, 5.58, 8.2, { fill: NAVY2, line: NAVY3, color: "E2E8F0", h: 0.42, fs: 12, gap: 0.14 });
  s.addShape(pres.shapes.OVAL, { x: 9.35, y: 1.75, w: 3.4, h: 3.4, fill: { color: NAVY2 }, line: { color: NAVY3, width: 1 } });
  s.addImage({ data: await icon(iconName, "818CF8"), x: 10.2, y: 2.6, w: 1.7, h: 1.7 });
  logoImg(s, "logo_mark_light.png", M, 0.55, 0.5);
  footer(s, true);
  if (notes) s.addNotes(notes);
  return s;
}
function metricRow(s, x, y, w, label, value, sub, color = GREEN, h = 0.78) {
  rect(s, x, y, w, h, BG, { line: LINE, r: 0.08 });
  T(s, value, { x: x + 0.15, y, w: 1.45, h, fontSize: 20, bold: true, color, valign: "middle" });
  T(s, [{ text: label, options: { bold: true, fontSize: 11.5, color: INK, breakLine: true } },
        { text: sub, options: { fontSize: 9.5, color: MUTED } }],
    { x: x + 1.65, y, w: w - 1.75, h, valign: "middle" });
}
const bullets = (items, o = {}) => items.map((t, i) => ({
  text: t, options: { bullet: { indent: 14 }, breakLine: i < items.length - 1, paraSpaceAfter: o.gap ?? 5 },
}));

// ── slides ─────────────────────────────────────────────────────────────────
async function build() {
  // 1 ─ Title
  {
    // a loan file is paper someone has to read: the photo shows the problem, not the product
    const s = pres.addSlide(); pageNo++;
    s.background = { color: NAVY };
    s.addImage({ path: path.join(IMG, "title_bg.jpg"), x: 0, y: 0, w: W, h: 7.5 });
    const lw = logoImg(s, "logo_lockup_light.png", M, 0.6, 0.62);
    T(s, "NBFC PLATFORM  ·  FINAL PROJECT PRESENTATION", { x: M, y: 1.62, w: 7, h: 0.3, fontSize: 11, bold: true, color: "A5B4FC", charSpacing: 2 });
    T(s, "Every loan file is a stack of paper someone has to read, check and judge.", { x: M, y: 2.0, w: 6.7, h: 1.75, fontSize: 34, bold: true, color: WHITE, valign: "top" });
    T(s, "FinSight AI — agentic loan underwriting for Indian NBFCs", { x: M, y: 3.85, w: 6.6, h: 0.45, fontSize: 18, bold: true, color: "C7D2FE" });
    T(s, "29 CrewAI agents read the documents, check them for fraud and RBI policy, score the risk and draft the credit memo — on a local model, with a person making every decision.",
      { x: M, y: 4.35, w: 6.2, h: 0.95, fontSize: 13, color: "CBD5E1", valign: "top" });
    const st = [["29", "agents in 6 crews"], ["12", "loan products"], ["25 / 29", "targets met on real data"], ["130", "automated tests"]];
    st.forEach(([v, l], i) => {
      const x = M + i * 1.62;
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: 5.5, w: 1.5, h: 0.98, fill: { color: NAVY2, transparency: 12 }, line: { color: NAVY3, width: 1 }, rectRadius: 0.1 });
      T(s, v, { x, y: 5.55, w: 1.5, h: 0.5, fontSize: 21, bold: true, color: "A5B4FC", align: "center", valign: "middle" });
      T(s, l, { x: x + 0.05, y: 6.03, w: 1.4, h: 0.38, fontSize: 9.5, color: "CBD5E1", align: "center", valign: "top" });
    });
    T(s, "Shreyas Bile  ·  4 October 2026", { x: M, y: 6.85, w: 6, h: 0.3, fontSize: 11, color: "94A3B8" });
    T(s, "Photo: Dimitri Karastelev / Unsplash", { x: W - M - 4, y: 6.9, w: 4, h: 0.25, fontSize: 8.5, color: "94A3B8", align: "right" });
    s.addNotes("Start with the problem: a loan file is paper that a person has to read, verify and judge. FinSight AI turns that desk work into 29 specialised agents. This deck walks through the product, the architecture, every agent, the workflows, the controls that keep the AI honest, and how each agent scored on real data.");
  }

  // 2 ─ Agenda
  {
    const s = content("Contents", "What this deck covers", "Six parts, from what the product does to how well each agent works.",
      "Use this as the map for the talk. Each numbered part has its own divider slide.");
    const items = [
      ["00", "The product", "The problem, the three users, a tour of the app", "LuLandmark"],
      ["01", "Architecture", "Layers, tech stack, how one agent runs", "LuLayers"],
      ["02", "The 29 agents", "Six crews, what each agent does and how it scored on real data", "LuBot"],
      ["03", "Workflows", "Loan lifecycle, the end-to-end journey, a real run", "LuWorkflow"],
      ["04", "Trust & control", "Model gateway, guardrails, tracing, the database", "LuShieldCheck"],
      ["05", "Evaluation", "Real-data method, scorecard, what testing changed, limits", "LuChartBar"],
    ];
    for (let i = 0; i < items.length; i++) {
      const [n, t, d, ic] = items[i];
      const x = M + (i % 3) * 4.1, y = 1.95 + Math.floor(i / 3) * 2.45;
      rect(s, x, y, 3.9, 2.2, BG, { line: LINE, r: 0.12 });
      await iconCircle(s, x + 0.3, y + 0.3, 0.62, ic, INDIGO_T, INDIGO);
      T(s, n, { x: x + 2.7, y: y + 0.28, w: 0.95, h: 0.6, fontSize: 30, bold: true, color: INDIGO_M, align: "right" });
      T(s, t, { x: x + 0.3, y: y + 1.08, w: 3.4, h: 0.42, fontSize: 18, bold: true, color: INK });
      T(s, d, { x: x + 0.3, y: y + 1.5, w: 3.4, h: 0.55, fontSize: 12, color: MUTED, valign: "top" });
    }
  }

  // 3 ─ Divider 00
  await divider("00", "The product", "What FinSight does, who uses it, and what it looks like.",
    ["The problem", "Three roles", "Twelve loan products", "App tour"], "LuLandmark");

  // 4 ─ The problem (photo panel on the right)
  {
    const s = pres.addSlide(); pageNo++;
    s.background = { color: WHITE };
    const PX = 7.75;
    s.addImage({ path: path.join(IMG, "problem_panel.jpg"), x: PX, y: 0, w: W - PX, h: 7.5 });
    T(s, "THE PRODUCT  ·  THE PROBLEM", { x: M, y: 0.38, w: 6.5, h: 0.28, fontSize: 11, bold: true, color: INDIGO, charSpacing: 2 });
    T(s, "A loan desk that still runs on paper", { x: M, y: 0.64, w: 6.9, h: 0.62, fontSize: 28, bold: true, color: INK });
    T(s, "Every NBFC loan file is a stack of Indian documents that someone has to read, verify and judge.", { x: M, y: 1.28, w: 6.7, h: 0.6, fontSize: 14, color: MUTED, valign: "top" });
    const pains = [
      ["LuFileText", "Manual document reading", "Officers retype PAN, Aadhaar, payslip and statement fields by hand."],
      ["LuTriangleAlert", "Forgeries are hard to see", "An edited payslip or a pasted ID photo looks genuine to the eye."],
      ["LuClock", "Slow, uneven turnaround", "In the seeded process data a file waits 77 h on average before its documents are read."],
      ["LuScale", "Rules keep changing", "RBI Digital Lending Directions 2025, KFS and penal-charge rules apply to every loan."],
    ];
    for (let i = 0; i < pains.length; i++) {
      const [ic, t, d] = pains[i], y = 2.2 + i * 1.16;
      rect(s, M, y, 6.75, 1.0, BG, { line: LINE, r: 0.1 });
      await iconCircle(s, M + 0.2, y + 0.2, 0.6, ic, RED_T, RED);
      T(s, [{ text: t, options: { bold: true, fontSize: 14, color: INK, breakLine: true } }, { text: d, options: { fontSize: 11.5, color: MUTED } }],
        { x: M + 1.0, y: y + 0.06, w: 5.6, h: 0.88, valign: "middle", paraSpaceAfter: 2 });
    }
    T(s, "What it costs: slow decisions, uneven checks, and forgeries that get through.", { x: M, y: 6.88, w: 6.75, h: 0.3, fontSize: 11.5, italic: true, color: BODY });
    T(s, "Photo: Wesley Tingey / Unsplash", { x: PX + 0.25, y: 7.08, w: 3, h: 0.25, fontSize: 8.5, color: "CBD5E1" });
    footer(s, true);
    s.addNotes("The problem in one picture: files. An officer reads every document by hand, cannot see a careful edit, works through a queue that waits days, and has to keep up with RBI rules that change every year.");
  }

  // 5 ─ What FinSight does
  {
    const s = content("The product", "Agents do the reading and checking. People decide.",
      "The same loan file, handled by 29 specialised agents that produce evidence and drafts — never a decision.",
      "Read the flow left to right, then the six jobs below. The key design choice is the last box: the agents produce evidence, scores and drafts; a person makes every decision, and any loan above 5 lakh needs the Head Manager.");
    const fl = [["Documents uploaded", "PAN · Aadhaar · payslip · statement", BG, LINE, INK],
      ["29 agents read and check", "one job, one set of tools each", INDIGO_T, INDIGO_M, INDIGO_D],
      ["Evidence, scores, drafts", "fraud tier · policy check · memo · offer", INDIGO_T, INDIGO_M, INDIGO_D],
      ["A person decides", "officer · Head above ₹5 lakh", GREEN_T, "86EFAC", "166534"]];
    const fw = 2.75, fg = (CW - 4 * fw) / 3;
    fl.forEach(([t, d, fill, ln, tc], i) => {
      const x = M + i * (fw + fg);
      node(s, x, 1.95, fw, 0.95, t, d, { fill, line: ln, tc, ts: 13.5, ss: 10.5 });
      if (i < 3) arrow(s, x + fw + 0.04, 2.425, x + fw + fg - 0.04, 2.425, { color: INDIGO });
    });
    const does = [
      ["LuFileSearch", "Reads every document", "Fields extracted, formats and checksums validated."],
      ["LuScanSearch", "Checks for tampering", "Ten signals, incl. a detector trained on real forgeries."],
      ["LuScanFace", "Matches the person", "Faces across documents, plus a live selfie."],
      ["LuShieldAlert", "Fuses the fraud evidence", "One advisory score and tier for the case."],
      ["LuScale", "Tests RBI and internal rules", "Every rule with its citation; missing evidence is 'need info'."],
      ["LuCalculator", "Scores risk, drafts the memo", "Risk grade, credit memo, offer and the next step."],
    ];
    for (let i = 0; i < does.length; i++) {
      const [ic, t, d] = does[i], x = M + (i % 3) * 4.1, y = 3.2 + Math.floor(i / 3) * 1.28;
      rect(s, x, y, 3.9, 1.12, BG, { line: LINE, r: 0.1 });
      await iconCircle(s, x + 0.2, y + 0.26, 0.6, ic, INDIGO_T, INDIGO);
      T(s, [{ text: t, options: { bold: true, fontSize: 13.5, color: INK, breakLine: true } }, { text: d, options: { fontSize: 10.5, color: MUTED } }],
        { x: x + 1.0, y: y + 0.08, w: 2.8, h: 0.96, valign: "middle", paraSpaceAfter: 2 });
    }
    rect(s, M, 5.85, CW, 0.95, INDIGO_T, { line: INDIGO_M, r: 0.1 });
    await iconCircle(s, M + 0.25, 6.0, 0.64, "LuUserCheck", WHITE, INDIGO);
    T(s, [{ text: "People decide. ", options: { bold: true, color: INDIGO_D } },
          { text: "Nothing is auto-rejected, the fraud tier is advice to a reviewer, and any loan above ₹5 lakh needs the Head Manager's approval.", options: { color: BODY } }],
      { x: M + 1.15, y: 5.85, w: CW - 1.4, h: 0.95, fontSize: 14, valign: "middle" });
  }

  // 5 ─ Roles & products
  {
    const s = content("The product", "Three roles, twelve loan products",
      "One app, three views. Each product has its own document checklist and its own lifecycle branch.",
      "The role picker is the entry point. The borrower applies; the underwriting officer works the queue; the head manager approves large loans and controls the AI settings and RBI rules.");
    frame(s, "rolepicker", M, 1.9, 5.5);
    const roles = [
      ["LuUser", "Borrower", "Applies, uploads documents, confirms what the agents read, takes a selfie and submits."],
      ["LuUserCheck", "Underwriting Officer", "Works the queue: evidence, fraud report, policy check, credit memo — then recommends."],
      ["LuCrown", "Head Manager", "Approves loans above ₹5 lakh, runs fraud triage, manages AI models and RBI rules."],
    ];
    for (let i = 0; i < roles.length; i++) {
      const [ic, t, d] = roles[i], y = 1.95 + i * 1.18;
      rect(s, 6.45, y, 6.28, 1.02, BG, { line: LINE, r: 0.1 });
      await iconCircle(s, 6.65, y + 0.2, 0.62, ic, INDIGO_T, INDIGO);
      T(s, [{ text: t, options: { bold: true, fontSize: 14, color: INK, breakLine: true } }, { text: d, options: { fontSize: 11, color: MUTED } }],
        { x: 7.45, y: y + 0.08, w: 5.15, h: 0.86, valign: "middle", paraSpaceAfter: 2 });
    }
    T(s, "12 LOAN PRODUCTS", { x: 6.45, y: 5.58, w: 4, h: 0.28, fontSize: 10.5, bold: true, color: MUTED, charSpacing: 2 });
    chipFlow(s, ["Two Wheeler", "Tractor", "Gold", "Personal", "Home", "MSME / Business", "Car", "Commercial Vehicle",
      "Loan Against Property", "Microfinance / Rural", "Working Capital", "Used Vehicle"], 6.45, 5.92, 6.3,
      { fill: INDIGO_T, line: INDIGO_T, color: INDIGO_D, h: 0.3, fs: 9.5, gap: 0.08, bold: true });
  }

  // 6 ─ App tour
  {
    const s = content("The product", "A quick tour of the app",
      "Screens captured from the running app (the application data shown is seeded demo data).",
      "Three views of the same platform. The borrower sees a simple portal; the officer sees a work queue with risk and fraud columns; the head sees the portfolio.");
    const shots = [
      ["borrower_home", "Borrower portal", "Status of every application and the 12 products to apply for.", "LuUser",
        ["Apply for any of the 12 products", "Upload, confirm fields, take a selfie", "Track status and offers"]],
      ["officer_queue", "Officer queue", "Assigned cases with risk, fraud, SLA timer and next action.", "LuUserCheck",
        ["Risk and fraud column for every case", "SLA timer and the next action", "Open a case: 8 tabs of evidence"]],
      ["head_dashboard", "Head dashboard", "Portfolio view: approvals waiting, fraud alerts, model health.", "LuCrown",
        ["Approvals waiting above ₹5 lakh", "Fraud alerts and ring triage", "AI model and RBI rule control"]],
    ];
    for (let i = 0; i < shots.length; i++) {
      const [k, t, d, ic, bl] = shots[i];
      const x = M + i * 4.1;
      const f = frame(s, k, x, 1.95, 3.9);
      const ty = 1.95 + f.h + 0.2;
      await iconCircle(s, x, ty, 0.44, ic, INDIGO_T, INDIGO);
      T(s, t, { x: x + 0.58, y: ty, w: 3.3, h: 0.44, fontSize: 15, bold: true, color: INK, valign: "middle" });
      T(s, d, { x, y: ty + 0.5, w: 3.9, h: 0.46, fontSize: 11, color: MUTED, valign: "top" });
      rect(s, x, ty + 1.04, 3.9, 1.0, BG, { line: LINE, r: 0.1 });
      T(s, bullets(bl, { gap: 3 }), { x: x + 0.15, y: ty + 1.08, w: 3.65, h: 0.92, fontSize: 11, color: BODY, valign: "middle" });
    }
  }

  // 7 ─ Divider 01
  await divider("01", "Architecture", "Six layers, one gateway to the language model, and one rule for every agent: tools decide, the model only explains.",
    ["System layers", "Tech stack", "How one agent runs"], "LuLayers");

  // 8 ─ Architecture layers
  {
    const s = content("Architecture", "System architecture", null,
      "Read top to bottom. Users work in a React single-page app. The FastAPI server keeps every pre-agent endpoint contract unchanged; behind each endpoint the work now runs as CrewAI flows over 29 registered agents. Agents call deterministic engines, and reach a language model only through one gateway. Everything lands in SQL Server and file storage.");
    const bands = [
      ["Users", "3 roles", INDIGO, ["Borrower", "Underwriting Officer", "Head Manager"]],
      ["Frontend", "React 18 · Vite 5", "0891B2", ["Loan wizard", "Application detail (8 tabs)", "Agent Activity", "AI model settings", "RBI compliance & policy"]],
      ["API", "FastAPI · Uvicorn :8000", "0F766E", ["/extract", "/document/integrity", "/document/mask", "/face/verify", "/liveness/check", "/agents/*", "/settings/llm"]],
      ["Agent layer", "CrewAI 1.15 flows", "E11D48", ["IntakeFlow", "CaseAssessmentFlow", "UnderwritingFlow + crew", "Triage · process", "29-agent registry", "Grounding gate", "Tracer"]],
    ];
    let y = 1.5;
    const BH = 0.66, LX = M, LW = 2.15, IX = M + 2.35, IW = CW - 2.35;
    for (const [t, sub, c, items] of bands) {
      rect(s, LX, y, LW, BH, c, { r: 0.08 });
      T(s, [{ text: t, options: { bold: true, fontSize: 13, color: WHITE, breakLine: true } }, { text: sub, options: { fontSize: 9.5, color: "E2E8F0" } }],
        { x: LX + 0.15, y, w: LW - 0.2, h: BH, valign: "middle" });
      rect(s, IX, y, IW, BH, BG, { line: LINE, r: 0.08 });
      chipFlow(s, items, IX + 0.15, y + 0.14, IW - 0.3, { h: 0.38, fs: t === "API" || t === "Agent layer" ? 10 : 10.5, cw: t === "API" ? 0.075 : undefined, gap: t === "Agent layer" ? 0.07 : 0.1, font: t === "API" ? FM : F, color: INK });
      arrow(s, IX + IW / 2, y + BH + 0.02, IX + IW / 2, y + BH + 0.16, { color: "94A3B8" });
      y += BH + 0.18;
    }
    // tools + gateway band
    const EH = 1.02;
    rect(s, LX, y, LW, EH, "D97706", { r: 0.08 });
    T(s, [{ text: "Engines & model", options: { bold: true, fontSize: 13, color: WHITE, breakLine: true } }, { text: "deterministic tools · one LLM gateway", options: { fontSize: 9.5, color: "FEF3C7" } }],
      { x: LX + 0.15, y, w: LW - 0.2, h: EH, valign: "middle" });
    rect(s, IX, y, 6.35, EH, BG, { line: LINE, r: 0.08 });
    chipFlow(s, ["OpenCV + RapidOCR", "Tampering cascade (10 signals)", "YuNet + SFace faces", "MiniFASNet liveness", "Risk engine + XGBoost", "Compliance RAG", "Statement parser"],
      IX + 0.12, y + 0.1, 6.15, { h: 0.37, fs: 10, gap: 0.08, color: INK });
    const GX = IX + 6.5, GW = IX + IW - GX;
    rect(s, GX, y, GW, EH, AMBER_T, { line: "FCD34D", r: 0.08 });
    T(s, "LLM gateway", { x: GX + 0.15, y: y + 0.06, w: 3, h: 0.3, fontSize: 12, bold: true, color: "92400E" });
    chip(s, "Ollama · qwen2.5vl:7b", GX + 0.15, y + 0.44, 1.7, 0.44, WHITE, INK, { line: "FCD34D", fs: 9.5, r: 0.08 });
    chip(s, "Claude (optional)", GX + 1.95, y + 0.44, GW - 2.1, 0.44, WHITE, MUTED, { line: "FCD34D", fs: 9.5, r: 0.08, bold: false });
    arrow(s, IX + IW / 2, y + EH + 0.02, IX + IW / 2, y + EH + 0.16, { color: "94A3B8" });
    y += EH + 0.18;
    rect(s, LX, y, LW, BH, NAVY2, { r: 0.08 });
    T(s, [{ text: "Data", options: { bold: true, fontSize: 13, color: WHITE, breakLine: true } }, { text: "SQL Server · files", options: { fontSize: 9.5, color: "CBD5E1" } }],
      { x: LX + 0.15, y, w: LW - 0.2, h: BH, valign: "middle" });
    rect(s, IX, y, IW, BH, BG, { line: LINE, r: 0.08 });
    chipFlow(s, ["SQL Server LocalDB 'FinSight' — 9 schemas · 82 tables · 17 views", "Uploads & masked copies", "Agent traces (JSON)"],
      IX + 0.15, y + 0.14, IW - 0.3, { h: 0.38, fs: 10.5, gap: 0.1, color: INK });
  }

  // 9 ─ Tech stack
  {
    const s = content("Architecture", "Tech stack", "Everything runs on one laptop — the local model uses an RTX 4060 (8 GB).",
      "Versions are the pinned ones from package.json and api/requirements-agents.txt.");
    const cards = [
      ["LuMonitor", "Frontend", "React 18 · Vite 5 · lucide icons", "Role-based single-page app"],
      ["LuServer", "API", "FastAPI 0.115 · Uvicorn · Pydantic 2", "Python 3.13"],
      ["LuBot", "Agents", "CrewAI 1.15.22", "Flows, crews and 29 registered agents"],
      ["LuSparkles", "Language models", "Ollama · qwen2.5vl:7b (default)", "qwen3-vl:4b option · Claude optional"],
      ["LuScanLine", "Vision & OCR", "OpenCV 4.10 · RapidOCR (ONNX)", "PDFium (pypdfium2) for PDFs"],
      ["LuScanFace", "Face & liveness", "YuNet · SFace · MiniFASNet", "ONNX Runtime 1.27, all local"],
      ["LuChartLine", "ML & scoring", "XGBoost 3.3 · scikit-learn · PyTorch", "Two-wheeler default model · patch forgery detector"],
      ["LuDatabase", "Data", "SQL Server LocalDB · pyodbc", "9 schemas · 82 tables · 17 views"],
      ["LuFlaskConical", "Quality", "pytest · two evaluation harnesses", "130 tests · all 29 agents scored on real data"],
    ];
    for (let i = 0; i < cards.length; i++) {
      const [ic, t, a, b] = cards[i];
      const x = M + (i % 3) * 4.1, y = 1.95 + Math.floor(i / 3) * 1.68;
      rect(s, x, y, 3.9, 1.48, BG, { line: LINE, r: 0.1 });
      await iconCircle(s, x + 0.25, y + 0.28, 0.66, ic, INDIGO_T, INDIGO);
      T(s, [{ text: t, options: { bold: true, fontSize: 14, color: INK, breakLine: true } },
            { text: a, options: { fontSize: 11.5, color: BODY, breakLine: true } },
            { text: b, options: { fontSize: 10.5, color: MUTED } }],
        { x: x + 1.1, y: y + 0.12, w: 2.7, h: 1.25, valign: "middle", paraSpaceAfter: 2 });
    }
  }

  // 10 ─ How one agent runs
  {
    const s = content("Architecture", "How one agent runs: tools decide, the model only explains",
      "Every agent follows the same four steps. A score is fixed before any language model is involved.",
      "The deterministic tool is the source of truth for every number. If an agent needs words (an explanation, a memo), it makes ONE structured call. The grounding gate then checks every number of 10 or more in that text exists in the evidence; if not, the deterministic text is shown instead. Every step is traced.");
    const y = 1.95, h = 1.35, o1 = { ts: 13.5, ss: 11 };
    node(s, M, y, 1.7, h, "Inputs", "a document, or the case's evidence", { fill: BG, ...o1 });
    arrow(s, M + 1.72, y + h / 2, M + 2.0, y + h / 2);
    node(s, 2.62, y, 2.35, h, "1 · Deterministic tool", "execute() sets the score, tier or verdict — locked", { fill: INDIGO_T, line: INDIGO_M, tc: INDIGO_D, ...o1 });
    arrow(s, 4.99, y + h / 2, 5.27, y + h / 2);
    node(s, 5.3, y, 2.35, h, "2 · One LLM call", "optional · narrate or judge · JSON schema enforced", { fill: AMBER_T, line: "FCD34D", tc: "92400E", ...o1 });
    arrow(s, 7.67, y + h / 2, 7.95, y + h / 2);
    s.addShape(pres.shapes.DIAMOND, { x: 7.98, y: y - 0.12, w: 2.2, h: h + 0.24, fill: { color: WHITE }, line: { color: INDIGO, width: 1.5 } });
    T(s, [{ text: "3 · Grounding gate", options: { bold: true, fontSize: 11.5, color: INK, breakLine: true } }, { text: "is every number ≥ 10 in the evidence?", options: { fontSize: 9.5, color: MUTED } }],
      { x: 8.33, y, w: 1.5, h, align: "center", valign: "middle" });
    arrow(s, 10.2, y + h / 2, 10.47, y + h / 2, { color: GREEN });
    T(s, "yes", { x: 10.18, y: y + h / 2 - 0.32, w: 0.4, h: 0.25, fontSize: 10, bold: true, color: GREEN });
    node(s, 10.5, y, 2.23, h, "4 · Result + trace", "model text shown · run, step and tool call recorded", { fill: GREEN_T, line: "86EFAC", tc: "166534", ...o1 });
    arrow(s, 9.08, y + h + 0.13, 9.08, y + h + 0.42, { color: RED });
    T(s, "no", { x: 9.18, y: y + h + 0.15, w: 0.4, h: 0.25, fontSize: 10, bold: true, color: RED });
    node(s, 7.98, y + h + 0.44, 2.2, 0.5, "Keep the tool's own text", null, { fill: RED_T, line: "FCA5A5", tc: "991B1B", ts: 11 });
    // agent kinds
    T(s, "THREE KINDS OF AGENT", { x: M, y: 4.2, w: 5, h: 0.28, fontSize: 10.5, bold: true, color: MUTED, charSpacing: 2 });
    const kinds = [
      ["18", "Deterministic", "Tool only — no model at all.", "Validation, masking, face match, tampering signals, fraud fusion, policy rules, risk, offer…", INDIGO],
      ["7", "Hybrid", "Tool decides, model narrates or judges within limits.", "Authenticity, tamper explainer, fraud patterns, triage, rule authoring, income, process mining", AMBER],
      ["4", "LLM", "The model does the core task inside a fenced call.", "Extraction, regulatory Q&A, credit memo, underwriting supervisor", "E11D48"],
    ];
    kinds.forEach(([n, t, a, b, c], i) => {
      const x = M + i * 4.1;
      rect(s, x, 4.58, 3.9, 2.25, BG, { line: LINE, r: 0.1 });
      T(s, n, { x: x + 0.25, y: 4.7, w: 1.0, h: 0.75, fontSize: 36, bold: true, color: c });
      T(s, t, { x: x + 1.2, y: 4.78, w: 2.5, h: 0.55, fontSize: 16, bold: true, color: INK, valign: "middle" });
      T(s, [{ text: a, options: { fontSize: 11.5, color: INK, breakLine: true } }, { text: b, options: { fontSize: 10, color: MUTED } }],
        { x: x + 0.25, y: 5.5, w: 3.45, h: 1.2, valign: "top", paraSpaceAfter: 4 });
    });
  }

  // 11 ─ Divider 02
  await divider("02", "The 29 agents", "Six crews. Each agent has one job, one set of tools and a pass mark fixed before it was tested. Scores shown are from real and public data.",
    ["Agent map", "Intake", "Privacy & identity", "Tampering", "Fraud fusion", "Patterns & triage", "Compliance", "Underwriting", "Risk", "Operations"], "LuBot");

  // 12 ─ Agent map
  const AGENTS = [
    [1, "Preprocessing", "intake", "D"], [2, "Extraction", "intake", "L"], [3, "Validation", "intake", "D"], [4, "Document Identity", "intake", "D"],
    [5, "PII Masking", "privacy", "D"],
    [6, "Face Verification", "fraud", "D"], [7, "Tampering Forensics", "fraud", "D"], [8, "Authenticity", "fraud", "H"], [9, "Tamper Explainer", "fraud", "H"],
    [10, "Tamper Locator", "fraud", "D"], [11, "Cross-Doc Verification", "fraud", "D"], [12, "Dedupe", "fraud", "D"], [13, "Fraud Fusion", "fraud", "D"],
    [14, "Fraud Patterns", "fraud", "H"], [15, "Fraud Triage", "fraud", "H"], [29, "Liveness", "fraud", "D"],
    [16, "KB Ingestion", "compliance", "D"], [17, "Rule Authoring", "compliance", "H"], [18, "Regulatory Q&A", "compliance", "L"], [19, "Policy Check", "compliance", "D"],
    [20, "Income Analysis", "underwriting", "H"], [21, "Risk Scoring", "underwriting", "D"], [22, "Credit Memo", "underwriting", "L"],
    [23, "Underwriting Supervisor", "underwriting", "L"], [24, "Offer Generation", "underwriting", "D"], [25, "Workflow Router", "underwriting", "D"],
    [26, "Notifier", "ops", "D"], [27, "Audit / Storage", "ops", "D"], [28, "Process-Mining Analyst", "ops", "H"],
  ];
  {
    const s = content("The 29 agents", "The agent map: 29 agents in six crews", null,
      "Colour marks the crew throughout the deck. 'Hybrid' agents decide with a tool and use the model only to explain or judge; 'LLM' agents use the model for their core task inside a single fenced call. Liveness (29) was added last and sits with identity and fraud evidence.");
    const KIND = { D: "deterministic", H: "hybrid", L: "LLM" };
    let y = 1.5;
    const PX = M + 2.25, GAP = 0.09, PW = (W - M - PX - 5 * GAP) / 6, PH = 0.64;
    for (const key of Object.keys(CREW)) {
      const cr = CREW[key], list = AGENTS.filter((a) => a[2] === key);
      const lines = Math.ceil(list.length / 6), rowH = lines * PH + (lines - 1) * 0.08;
      await iconCircle(s, M, y + (rowH - 0.5) / 2, 0.5, cr.icon, cr.t, cr.c);
      T(s, [{ text: cr.name, options: { bold: true, fontSize: 12, color: INK, breakLine: true } }, { text: `${list.length} agent${list.length > 1 ? "s" : ""}`, options: { fontSize: 10, color: MUTED } }],
        { x: M + 0.62, y, w: 1.65, h: rowH, valign: "middle" });
      list.forEach((a, i) => {
        const x = PX + (i % 6) * (PW + GAP), yy = y + Math.floor(i / 6) * (PH + 0.08);
        rect(s, x, yy, PW, PH, cr.t, { line: cr.c, lw: 0.75, r: 0.08 });
        T(s, [{ text: `#${a[0]}  `, options: { bold: true, fontSize: 9, color: cr.c } },
              { text: KIND[a[3]], options: { fontSize: 8.5, color: a[3] === "D" ? MUTED : a[3] === "H" ? "B45309" : "BE123C", bold: a[3] !== "D", breakLine: true } },
              { text: a[1], options: { bold: true, fontSize: 11, color: INK } }],
          { x: x + 0.1, y: yy + 0.03, w: PW - 0.14, h: PH - 0.06, valign: "middle" });
      });
      y += rowH + 0.15;
    }
  }

  // 13 ─ Intake crew
  {
    const s = content("The 29 agents · Intake crew", "Intake: every upload becomes checked, structured fields",
      "Four agents run on each document — about 7 seconds for a PAN card on the local model.",
      "Preprocessing repairs the photo (orientation, crop, lighting) but reverts if it made the page less readable. Extraction is the only model call on the upload path. In parallel the page's own words are read, which Document Identity uses to catch a payslip uploaded as a PAN. Validation checks formats and checksums, e.g. Aadhaar's Verhoeff digit. Real-data scores: extraction rose from 84.7% to 94.4% after three fixes (first and last page for statements, totals from the statement itself, PAN number checked against the card's OCR text). Preprocessing is the one weak agent here: the card outline is found in 69% of phone frames - a pale card on a pale surface is still missed - although its cleanup no longer harms scans (31% to 6%).");
    const c = CREW.intake, y = 1.95, h = 1.05;
    node(s, M, y, 1.45, h, "Upload", "photo or PDF", { fill: BG });
    arrow(s, 2.07, y + h / 2, 2.32, y + h / 2);
    node(s, 2.35, y, 2.35, h, "1 · Preprocessing", "orient · crop · repair light · revert if worse", { fill: c.t, line: c.c, tc: c.c });
    arrow(s, 4.72, y + h / 2, 4.97, y + 0.24); arrow(s, 4.72, y + h / 2, 4.97, y + h - 0.24);
    node(s, 5.0, y - 0.05, 2.5, 0.52, "2 · Extraction", null, { fill: c.t, line: c.c, tc: c.c, ts: 11.5 });
    node(s, 5.0, y + 0.58, 2.5, 0.52, "page read (same OCR words)", null, { fill: BG, ts: 10.5, tc: BODY });
    arrow(s, 7.52, y + 0.21, 7.8, y + 0.21); arrow(s, 7.52, y + 0.84, 7.8, y + 0.84);
    node(s, 7.83, y - 0.05, 2.45, 0.52, "3 · Validation", null, { fill: c.t, line: c.c, tc: c.c, ts: 11.5 });
    node(s, 7.83, y + 0.58, 2.45, 0.52, "4 · Document Identity", null, { fill: c.t, line: c.c, tc: c.c, ts: 11.5 });
    arrow(s, 10.3, y + h / 2, 10.58, y + h / 2);
    node(s, 10.6, y, 2.13, h, "Person confirms", "\"Please check & confirm\"", { fill: GREEN_T, line: "86EFAC", tc: "166534" });
    T(s, "qwen2.5vl reads every requested field into JSON, inventing nothing  ·  Verhoeff, PAN, IFSC, GSTIN formats  ·  the page's own phrases must match the requested type",
      { x: M, y: 3.17, w: CW, h: 0.3, fontSize: 10.5, color: MUTED, italic: true });
    frame(s, "wizard_extract", M, 3.6, 7.3, 3.35);
    const mx = 8.2, mw = 4.53;
    metricRow(s, mx, 3.6, mw, "Extraction", "94.4%", "printed fields correct · 150 PAN cards, 60 statements");
    metricRow(s, mx, 4.45, mw, "Document Identity", "94.4%", "wrong uploads caught · right ones refused 1.6%");
    metricRow(s, mx, 5.3, mw, "Validation", "100%", "183,214 real IFSC codes accepted, mistakes rejected");
    metricRow(s, mx, 6.15, mw, "Preprocessing", "69.1%", "card outline found in phone photos · target 80% (Weak)", RED);
  }

  // 14 ─ Privacy & identity
  {
    const s = content("The 29 agents · Privacy · Identity", "Privacy & identity: mask the numbers, match the faces",
      "PII Masking protects the officer's view; Face Verification and Liveness make sure one real person is applying.",
      "Masking finds the rotation where the ID number reads upright before splitting it — the fix for a real sideways PAN that leaked in testing. Face verification uses YuNet to find faces and SFace to compare them (cosine threshold 0.363). A low-quality face can only say 'uncertain', never 'mismatch'. On real data: masking went from 42% to 98.4% of documents with no ID readable once IDs printed inside a line of text were found; the three remaining documents are why the target of 100% is not met. Face verification was tested on AgeDB-30, CALFW, CPLFW and LFW, also with the photos printed into an ID-card template. Liveness is strong on full webcam frames (NUAA) but weak on tight crops, and screen-replay attacks were not in the test set.");
    const cols = [
      { cr: CREW.privacy, ic: "LuEyeOff", t: "PII Masking  #5", m: "98.4%", ms: "no full ID readable on 187 of 190 documents · target 100%", mc: AMBER,
        b: ["Finds the ID number, even inside a line of text, and masks all but the last four", "Reads the card upright first, so sideways photos can't leak", "A card whose number can't be located is withheld, never shown unmasked"] },
      { cr: CREW.fraud, ic: "LuScanFace", t: "Face Verification  #6", m: "95.9%", ms: "correct verdicts · 4 public benchmarks, also printed on ID cards", mc: GREEN,
        b: ["YuNet finds each face, SFace turns it into an embedding", "Cosine similarity ≥ 0.363 → same person (0.3% false matches)", "A blurry or tiny face can only be 'uncertain', never 'mismatch'"] },
      { cr: CREW.fraud, ic: "LuCamera", t: "Liveness  #29", m: "0.4%", ms: "ACER on 2,000 webcam frames (target ≤ 10%) · full frames only", mc: GREEN,
        b: ["Selfie checked by MiniFASNet for printed-photo attacks", "Judges the whole frame — a tight face crop loses what it looks at", "The picture is checked and discarded"] },
    ];
    for (let i = 0; i < cols.length; i++) {
      const cdef = cols[i], x = M + i * 4.1;
      rect(s, x, 1.95, 3.9, 4.95, BG, { line: LINE, r: 0.12 });
      await iconCircle(s, x + 0.25, 2.15, 0.62, cdef.ic, cdef.cr.t, cdef.cr.c);
      T(s, cdef.t, { x: x + 1.0, y: 2.2, w: 2.8, h: 0.5, fontSize: 15, bold: true, color: INK, valign: "middle" });
      // mini visual
      if (i === 0) {
        rect(s, x + 0.25, 2.95, 3.4, 1.3, WHITE, { line: LINE, r: 0.08 });
        T(s, "PAN", { x: x + 0.4, y: 3.03, w: 1, h: 0.25, fontSize: 9, bold: true, color: MUTED });
        T(s, [{ text: "MCQPD5333A", options: { color: "94A3B8", strike: "sngStrike" } }, { text: "  →  ", options: { color: MUTED } }, { text: "XXXXXX333A", options: { color: cdef.cr.c, bold: true } }],
          { x: x + 0.4, y: 3.28, w: 3.2, h: 0.35, fontSize: 12.5, fontFace: FM });
        T(s, "AADHAAR", { x: x + 0.4, y: 3.62, w: 1.2, h: 0.25, fontSize: 9, bold: true, color: MUTED });
        T(s, [{ text: "XXXX XXXX ", options: { color: cdef.cr.c, bold: true } }, { text: "5784", options: { color: INK, bold: true } }],
          { x: x + 0.4, y: 3.86, w: 3.2, h: 0.3, fontSize: 12.5, fontFace: FM });
      } else {
        const steps = i === 1 ? ["YuNet", "SFace", "≥ 0.363"] : ["Selfie", "MiniFASNet", "ID match"];
        steps.forEach((st, j) => {
          node(s, x + 0.25 + j * 1.18, 3.25, 0.98, 0.7, st, null, { fill: WHITE, line: cdef.cr.c, tc: cdef.cr.c, ts: 10.5 });
          if (j < 2) arrow(s, x + 1.24 + j * 1.18, 3.6, x + 1.42 + j * 1.18, 3.6, { color: cdef.cr.c });
        });
      }
      T(s, bullets(cdef.b), { x: x + 0.25, y: 4.42, w: 3.45, h: 1.35, fontSize: 11, color: BODY, valign: "top" });
      rect(s, x + 0.25, 5.88, 3.4, 0.82, WHITE, { line: LINE, r: 0.08 });
      T(s, cdef.m, { x: x + 0.38, y: 5.88, w: 1.2, h: 0.82, fontSize: 20, bold: true, color: cdef.mc, valign: "middle" });
      T(s, cdef.ms, { x: x + 1.55, y: 5.88, w: 2.05, h: 0.82, fontSize: 9.5, color: MUTED, valign: "middle" });
    }
  }

  // 15 ─ Tampering cascade
  {
    const s = content("The 29 agents · Identity & fraud evidence", "Tampering: a ten-signal cascade, fused into one honest tier",
      "Cheap checks first; pixel forensics only when needed (ID cards always get them). Two helper agents explain and locate.",
      "Stage 1 runs fast checks. If they all score under 0.25 and business rules pass, the document is fast-tracked as LOW — except ID cards, which always get stage 2. Stage 2 now includes a patch detector trained on real forged receipts and ID images; it also names the region it suspects. Fusion takes the larger of the weighted average and the strongest single signal times its confidence, so one near-proof finding is not averaged away; a pixel signal counts only above what 95% of genuine documents reach. The explainer rewrites findings in plain words (grounded); the locator draws the region. On real data the detector first flagged 84% of genuine documents; after calibration and the new detector it finds 89% of forgeries and rates 0.8% of genuine documents HIGH. The screenshot shows how a flagged document appears to the officer - a synthetic card, captured before that calibration.");
    const c = CREW.fraud, y = 1.95, h = 1.2;
    node(s, M, y, 1.25, h, "Document", "image or PDF", { fill: BG });
    arrow(s, 1.87, y + h / 2, 2.1, y + h / 2);
    node(s, 2.12, y, 2.55, h, "Stage 1 · fast", "metadata · business rules · copy-move · PDF forensics · authenticity (VLM, capped 0.60)", { fill: c.t, line: c.c, tc: c.c, ss: 9.5 });
    arrow(s, 4.69, y + h / 2, 4.9, y + h / 2);
    s.addShape(pres.shapes.DIAMOND, { x: 4.92, y: y - 0.05, w: 1.5, h: h + 0.1, fill: { color: WHITE }, line: { color: c.c, width: 1.25 } });
    T(s, [{ text: "Gate", options: { bold: true, fontSize: 10.5, color: INK, breakLine: true } }, { text: "all < 0.25?", options: { fontSize: 9, color: MUTED } }],
      { x: 5.05, y, w: 1.24, h, align: "center", valign: "middle" });
    arrow(s, 6.44, y + h / 2, 6.65, y + h / 2);
    node(s, 6.67, y, 2.2, h, "Stage 2 · pixels", "ELA · noise · CNN · patch detector (real forgeries) · photo-region", { fill: c.t, line: c.c, tc: c.c, ss: 9.5 });
    arrow(s, 8.89, y + h / 2, 9.1, y + h / 2);
    node(s, 9.12, y, 1.85, h, "Fusion", "max(weighted avg, strongest × confidence)", { fill: WHITE, line: c.c, tc: c.c, ss: 9.5 });
    arrow(s, 10.99, y + h / 2, 11.2, y + h / 2);
    node(s, 11.22, y, 1.51, h, "Tier", "LOW · MEDIUM ≥ 0.30 · HIGH ≥ 0.60", { fill: RED_T, line: "FCA5A5", tc: "991B1B", ss: 9.5 });
    arrow(s, 5.67, y + h + 0.06, 5.67, y + h + 0.36, { color: GREEN });
    T(s, "yes → LOW, fast-tracked (never for ID cards)", { x: 5.8, y: y + h + 0.1, w: 3.3, h: 0.28, fontSize: 9.5, color: GREEN, bold: true });
    arrow(s, 11.55, y + h + 0.02, 10.05, y + h + 0.32);
    arrow(s, 11.97, y + h + 0.02, 11.97, y + h + 0.32);
    node(s, 9.12, y + h + 0.34, 1.85, 0.55, "#9 Tamper Explainer", "plain words, grounded", { fill: WHITE, line: LINE, ts: 10, ss: 8.5 });
    node(s, 11.12, y + h + 0.34, 1.61, 0.55, "#10 Tamper Locator", "draws the region", { fill: WHITE, line: LINE, ts: 10, ss: 8.5 });
    const tf = frame(s, "integrity_pan", M, 4.2, 7.6, 2.45);
    T(s, "How a flagged document is shown to the officer (synthetic card, captured before the real-data calibration).", { x: M, y: 4.2 + tf.h + 0.08, w: 7.6, h: 0.26, fontSize: 9.5, color: MUTED, italic: true });
    const mx = 8.45, mw = 4.28;
    metricRow(s, mx, 4.2, mw, "Forged documents flagged", "88.9%", "real forged receipts + ID images · was 17%", GREEN, 0.7);
    metricRow(s, mx, 4.96, mw, "Genuine rated HIGH", "0.8%", "genuine ID phone photos flagged at all: 0.5%", GREEN, 0.7);
    metricRow(s, mx, 5.72, mw, "Locator · Explainer", "74.6%", "forged region boxed · explanations 98.3% grounded", AMBER, 0.7);
    T(s, "Authenticity (#8, the vision model asked 'was this edited?') flags 11% — Weak: a 7B model cannot see a local edit.", { x: mx, y: 6.5, w: mw, h: 0.5, fontSize: 9.5, color: RED, italic: true, valign: "top" });
  }

  // 16 ─ Case assessment
  {
    const s = content("The 29 agents · Identity & fraud evidence", "Case assessment: all the evidence, one advisory route",
      "Runs when the file is complete — no language model at all, about 100 ms.",
      "The case-assessment flow runs three checks in parallel on the whole case, fuses them with the per-document tampering and face results into one fraud tier, then checks RBI policy. The result is only ever a recommendation: proceed, or refer for a closer human review. The screenshot is a real run on a synthetic applicant who was submitted twice — Dedupe found the same files, so two floors pushed the tier to HIGH.");
    const c = CREW.fraud, X = M, Wd = 5.7;
    node(s, X, 1.95, Wd, 0.72, "Evidence bundle", "extracted fields · tampering tiers · face result · statement rows", { fill: BG });
    const three = [["#11 Cross-Doc", "names · DOB · gender"], ["#12 Dedupe", "HMAC keys · file hash · face"], ["#14 Patterns", "statement rows"]];
    three.forEach(([t, d], i) => {
      const x = X + i * 1.95;
      arrow(s, X + Wd / 2, 2.69, x + 0.9, 2.95);
      node(s, x, 2.97, 1.8, 0.85, t, d, { fill: c.t, line: c.c, tc: c.c, ts: 11, ss: 9 });
      arrow(s, x + 0.9, 3.84, X + Wd / 2, 4.12);
    });
    node(s, X, 4.14, Wd, 0.72, "#13 Fraud Fusion", "one advisory score and tier (LOW · MEDIUM · HIGH)", { fill: c.t, line: c.c, tc: c.c });
    arrow(s, X + Wd / 2, 4.88, X + Wd / 2, 5.1);
    node(s, X, 5.12, Wd, 0.72, "#19 Policy Check", "15 active RBI / internal rules → PASS · FAIL · NEED INFO", { fill: CREW.compliance.t, line: CREW.compliance.c, tc: CREW.compliance.c });
    arrow(s, X + Wd / 2, 5.86, X + Wd / 2, 6.08);
    node(s, X, 6.1, Wd, 0.72, "Advisory route", "proceed · or refer for a closer human review — never reject", { fill: GREEN_T, line: "86EFAC", tc: "166534" });
    const f = frame(s, "assessment_full", 6.6, 1.95, 6.13, 4.6);
    T(s, "Real run, synthetic applicant submitted twice: Dedupe finds the same files → two floors → HIGH.",
      { x: 6.6, y: 1.95 + f.h + 0.12, w: 6.13, h: 0.45, fontSize: 10.5, color: MUTED, italic: true });
  }

  // 17 ─ Fraud fusion math
  {
    const s = content("The 29 agents · Fraud Fusion", "Fraud Fusion v2: how one score is built",
      "A weighted average measures agreement; a dominant path and floors stop one strong red flag being averaged away.",
      "v1 was the weighted sum plus floors that was originally chosen; it scored 60% on 400 held-out labelled scenarios. Calibrating the weights reached 79%. v2 adds the same dominant-and-corroborated path the tampering fusion uses and scored 93%. Scores never fall when evidence gets worse. Weights are versioned and snapshotted on every assessment; v1 is a one-line switch. On real data the fusion was tested on 600 composite cases assembled from real component outputs: 96.7% of fraud cases are flagged, and a forged document on its own now moves a case (87% flagged, against 55% before the tampering detector could find real forgeries). About half of clean cases still come out MEDIUM, because a genuine file with an editor tag is flagged - an open decision.");
    s.addChart(pres.charts.BAR, [{ name: "Weight", labels: ["Document tampering", "Face match", "Cross-document checks", "Field validation", "Duplicate identity", "Statement patterns"], values: [35, 20, 20, 15, 10, 10] }], {
      x: M, y: 1.9, w: 5.4, h: 3.0, barDir: "bar", chartColors: [CREW.fraud.c], showValue: true, dataLabelFormatCode: '0"%"', dataLabelColor: INK, dataLabelFontSize: 10,
      catAxisLabelColor: BODY, catAxisLabelFontSize: 10.5, catAxisLabelFontFace: F, valAxisHidden: true, valGridLine: { style: "none" }, catGridLine: { style: "none" },
      catAxisOrientation: "maxMin", showTitle: true, title: "Component weights (normalised over what's available)", titleFontSize: 11.5, titleColor: INK, titleFontFace: F, showLegend: false, barGapWidthPct: 45,
    });
    const steps = [
      ["1", "Weighted average", "Each component scores 0–1; only components with evidence count."],
      ["2", "Dominant path", "fused = max(average, strongest signal × its confidence) — plus 0.1 if another signal agrees."],
      ["3", "Floors", "Confident face mismatch · exact duplicate identity · tampering HIGH. One floor → at least MEDIUM; two → HIGH."],
      ["4", "Tiers, advisory only", "MEDIUM ≥ 0.20 · HIGH ≥ 0.60. The score never rejects on its own."],
    ];
    steps.forEach(([n, t, d], i) => {
      const y = 5.05 + i * 0.47;
      chip(s, n, M, y + 0.04, 0.34, 0.34, CREW.fraud.c, WHITE, { fs: 10.5 });
      T(s, [{ text: t + "  ", options: { bold: true, color: INK } }, { text: d, options: { color: MUTED } }], { x: M + 0.48, y, w: 5.3, h: 0.44, fontSize: 10.5, valign: "middle" });
    });
    rect(s, 6.55, 1.9, 6.18, 5.0, BG, { line: LINE, r: 0.12 });
    T(s, "Held-out tier accuracy · 400 labelled scenarios", { x: 6.8, y: 2.05, w: 5.7, h: 0.35, fontSize: 13, bold: true, color: INK });
    s.addChart(pres.charts.BAR, [{ name: "Accuracy", labels: ["v1 · weighted sum + floors", "weights calibrated", "v2 · + dominant path (shipped)"], values: [60.3, 78.8, 93.0] }], {
      x: 6.75, y: 2.45, w: 5.8, h: 3.2, barDir: "col", chartColors: ["A5B4FC"], showValue: true, dataLabelFormatCode: '0.0"%"', dataLabelColor: INK, dataLabelFontSize: 12, dataLabelFontBold: true,
      catAxisLabelColor: BODY, catAxisLabelFontSize: 10, catAxisLabelFontFace: F, valAxisMinVal: 0, valAxisMaxVal: 100, valAxisHidden: true,
      valGridLine: { color: "E2E8F0", size: 0.5 }, catGridLine: { style: "none" }, showLegend: false, barGapWidthPct: 60,
    });
    T(s, [{ text: "96.7%  ", options: { bold: true, color: GREEN, fontSize: 22 } }, { text: "of 300 fraud cases built from real forged documents, real faces and names are rated MEDIUM or HIGH (target ≥ 85%); one clean case in 300 is rated HIGH. On the 400 synthetic scenarios above, v2 scores 93%.", options: { color: BODY, fontSize: 11 } }],
      { x: 6.8, y: 5.8, w: 5.7, h: 0.95, valign: "middle" });
  }

  // 18 ─ Patterns & triage
  {
    const s = content("The 29 agents · Identity & fraud evidence", "Statement patterns and fraud triage",
      "Patterns read the bank statement row by row; triage looks across all applications for rings.",
      "The extraction prompt only returns a statement summary, so a separate parser recovers every row — from the PDF text layer, or from OCR on a photo with tilt correction — and decides debit vs credit from the balance movement. Rows that don't reconcile never raise a flag. Triage links applications through hashed identity keys and faces, and PROPOSES cases; a person decides what to open.");
    const c = CREW.fraud;
    T(s, "#14 FRAUD PATTERNS", { x: M, y: 1.92, w: 5, h: 0.28, fontSize: 10.5, bold: true, color: c.c, charSpacing: 2 });
    const p = [["PDF text layer", "or OCR words"], ["Tilt-corrected rows", "date + amounts"], ["Reconcile", "debit / credit by balance"]];
    p.forEach(([t, d], i) => {
      node(s, M + i * 1.95, 2.3, 1.75, 0.8, t, d, { fill: WHITE, line: LINE, ts: 10.5, ss: 9 });
      if (i < 2) arrow(s, M + 1.77 + i * 1.95, 2.7, M + 1.93 + i * 1.95, 2.7);
    });
    const pats = [["Cash cycling", "money in and straight back out"], ["Sudden deposit", "a lump sum just before applying"], ["Window dressing", "balance pumped near statement end"],
      ["Income mismatch", "salary credits ≠ payslip net"], ["GST inflation", "credits far below declared turnover"]];
    T(s, "FIVE PATTERNS", { x: M, y: 3.3, w: 4, h: 0.26, fontSize: 9.5, bold: true, color: MUTED, charSpacing: 1 });
    pats.forEach(([t, d], i) => {
      const y = 3.62 + i * 0.52;
      rect(s, M, y, 5.75, 0.45, c.t, { line: "FECDD3", r: 0.08 });
      T(s, [{ text: t + "  ", options: { bold: true, color: INK } }, { text: d, options: { color: MUTED } }], { x: M + 0.15, y, w: 5.5, h: 0.45, fontSize: 11, valign: "middle" });
    });
    metricRow(s, M, 6.3, 5.75, "Pattern detection · 600 real accounts", "F1 0.92", "statement rows recovered on Indian layouts: 4% → 95%", GREEN, 0.66);
    const f = frame(s, "triage", 6.55, 1.95, 6.18);
    const ty = 1.95 + f.h + 0.18;
    T(s, "#15 FRAUD TRIAGE", { x: 6.55, y: ty, w: 5, h: 0.28, fontSize: 10.5, bold: true, color: c.c, charSpacing: 2 });
    T(s, bullets(["Links applications that share one face under several PANs, one bank account, one PAN with two faces, or one file",
      "Proposes cases only — a person decides what to open", "80 planted rings among 1,000 real faces: every proposal was a real ring (71 false rings before the stricter 1:N face threshold)"], { gap: 4 }),
      { x: 6.55, y: ty + 0.32, w: 6.18, h: 1.2, fontSize: 11, color: BODY, valign: "top" });
  }

  // 19 ─ Compliance crew
  {
    const s = content("The 29 agents · Compliance crew", "Compliance: RBI rules as data, answers with citations",
      "Regulations are indexed once; rules are drafted by an agent and approved by a person; every case is checked against them.",
      "KB Ingestion chunks and indexes the regulations (6 documents seeded). Rule Authoring drafts machine-checkable rule candidates from a new regulation; they go to a human review queue before becoming active. Policy Check evaluates the 15 active rules against real evidence only: a field nobody verified shows as 'need info', never as a false pass. Regulatory Q&A answers only from retrieved passages and cites them. The real-data scores come from ObliQA - 40 real financial regulations (ADGM) with 1,000 official test questions - because rbi.org.in blocks scripted downloads; the RBI set waits for six circulars to be downloaded by hand. Policy Check was run on 2,000 real loans.");
    const c = CREW.compliance, y = 1.95, h = 1.0;
    const flow = [["RBI & internal documents", "6 seeded", BG, LINE, INK], ["#16 KB Ingestion", "chunk & index", c.t, c.c, c.c], ["#17 Rule Authoring", "drafts candidates", c.t, c.c, c.c],
      ["Human checker", "review queue", AMBER_T, "FCD34D", "92400E"], ["Active rules", "15 today", BG, LINE, INK], ["#19 Policy Check", "PASS · FAIL · NEED INFO", c.t, c.c, c.c]];
    const bw = 1.82, gp = 0.24;
    flow.forEach(([t, d, fl, ln, tc], i) => {
      node(s, M + i * (bw + gp), y, bw, h, t, d, { fill: fl, line: ln, tc, ts: 11, ss: 9.5 });
      if (i < flow.length - 1) arrow(s, M + bw + i * (bw + gp) + 0.02, y + h / 2, M + (i + 1) * (bw + gp) - 0.02, y + h / 2);
    });
    const qx = M + bw + gp;
    arrow(s, qx + bw / 2, y + h + 0.02, qx + bw / 2, y + h + 0.2);
    node(s, qx - 0.25, y + h + 0.22, bw + 0.5, 0.5, "#18 Regulatory Q&A", null, { fill: c.t, line: c.c, tc: c.c, ts: 11 });
    T(s, "retrieves the top-5 passages, then writes a cited, grounded answer", { x: qx + bw + 0.4, y: y + h + 0.22, w: 5, h: 0.5, fontSize: 10.5, color: MUTED, italic: true, valign: "middle" });
    frame(s, "rbi_chat", M, 3.9, 7.4, 3.1);
    const mx = 8.3, mw = 4.43;
    metricRow(s, mx, 3.9, mw, "KB Ingestion", "92.7%", "right document in top 5 · 1,000 questions", GREEN, 0.7);
    metricRow(s, mx, 4.66, mw, "Rule Authoring", "83.7%", "77 of 92 labelled obligations drafted as rules", GREEN, 0.7);
    metricRow(s, mx, 5.42, mw, "Regulatory Q&A", "79.4%", "an answering passage in the top 10", GREEN, 0.7);
    metricRow(s, mx, 6.18, mw, "Policy Check", "100%", "116,000 verdicts equal an independent check", GREEN, 0.7);
  }

  // 20 ─ Underwriting crew
  {
    const s = content("The 29 agents · Underwriting crew", "Underwriting: from income to a drafted offer",
      "One click runs the chain; the only model calls are the two agents inside the crew. About 20 seconds.",
      "Income Analysis triangulates the monthly income across payslip, ITR/Form 16, bank credits and GST. Risk Scoring grades nine dimensions and adds an XGBoost default probability. The Underwriting Crew then runs two agents: the Supervisor recommends a next step but may only tighten the rule baseline, and the Credit Memo agent writes the memo citing only real figures. Offer Generation prices the loan and computes the EMI with the standard formula. The Workflow Router recommends the next lifecycle step and its owner; the Notifier drafts in-app messages. Real-data scores use 120 real, disbursed two-wheeler loans with their real outcome: loans the supervisor recommends for approval defaulted 13.8% of the time against 19.2% for the sample - the right direction, with a wide interval at 29 approvals. Real data also showed the supervisor could never recommend approval, because loan-terms rules were checked before any offer existed; that was fixed.");
    const c = CREW.underwriting, y = 1.95, h = 1.1;
    node(s, M, y, 1.75, h, "#20 Income", "payslip · ITR · bank · GST", { fill: c.t, line: c.c, tc: "92400E", ss: 9.5 });
    arrow(s, 2.37, y + h / 2, 2.57, y + h / 2);
    node(s, 2.59, y, 1.75, h, "#21 Risk", "9 dimensions + XGBoost", { fill: c.t, line: c.c, tc: "92400E", ss: 9.5 });
    arrow(s, 4.36, y + h / 2, 4.56, y + h / 2);
    rect(s, 4.58, y - 0.2, 3.5, h + 0.4, WHITE, { line: c.c, lw: 1.25, dash: "dash", r: 0.1 });
    T(s, "UNDERWRITING CREW · 2 LLM CALLS", { x: 4.7, y: y - 0.17, w: 3.3, h: 0.22, fontSize: 8.5, bold: true, color: "92400E", charSpacing: 1 });
    node(s, 4.72, y + 0.12, 1.55, 0.95, "#23 Supervisor", "tighten-only", { fill: c.t, line: c.c, tc: "92400E", ts: 11, ss: 9.5 });
    node(s, 6.4, y + 0.12, 1.55, 0.95, "#22 Credit Memo", "grounded", { fill: c.t, line: c.c, tc: "92400E", ts: 11, ss: 9.5 });
    arrow(s, 8.1, y + h / 2, 8.3, y + h / 2);
    node(s, 8.32, y, 1.4, h, "#24 Offer", "rate · EMI", { fill: c.t, line: c.c, tc: "92400E", ss: 9.5 });
    arrow(s, 9.74, y + h / 2, 9.94, y + h / 2);
    node(s, 9.96, y, 1.4, h, "#25 Router", "next step + owner", { fill: c.t, line: c.c, tc: "92400E", ss: 9.5 });
    arrow(s, 11.38, y + h / 2, 11.58, y + h / 2);
    node(s, 11.6, y, 1.13, h, "#26 Notifier", "in-app", { fill: CREW.ops.t, line: CREW.ops.c, tc: CREW.ops.c, ss: 9.5 });
    frame(s, "memo_top", M, 3.55, 6.9, 3.45);
    const mx = 7.8, mw = 4.93;
    metricRow(s, mx, 3.55, mw, "Income Analysis", "F1 1.00", "inflated income flagged on 400 real account histories", GREEN);
    metricRow(s, mx, 4.38, mw, "Credit Memo", "100%", "120 memos on real loans cite only real figures", GREEN);
    metricRow(s, mx, 5.21, mw, "Supervisor", "0.72×", "defaults among loans it would approve vs all loans", GREEN);
    metricRow(s, mx, 6.04, mw, "Offer · Router", "100%", "EMI = formula (2,000 offers) · 147 status moves", GREEN);
  }

  // 21 ─ Risk scoring
  {
    const s = content("The 29 agents · Underwriting crew", "Risk Scoring: nine dimensions plus a machine-learning check",
      "A 12 × 9 weight matrix (one row per product) gives the grade; a trained default model adjusts it within ±25 points.",
      "The rule engine scores nine risk dimensions from 0 to 100 and weights them by product — the chart shows the commercial-vehicle row. The ML overlay is XGBoost trained on a 50,000-loan Indian NBFC book. Testing found the app was building its features differently from training: AUC 0.669. With the training feature builder reused, AUC is 0.771 on the 1,500 held-out loans. Across 750 checks, the score never fell when an input got worse. On real data - Indian two-wheeler loans with real defaults (L&T) - the rule engine alone ranked defaults weakly (AUC 0.595, 66% of what a model trained on that data achieves). A monotonic default model trained on the development split was added as a bounded adjustment, lifting it to 0.621, 83% of that ceiling.");
    s.addChart(pres.charts.BAR, [{ name: "Weight", labels: ["Collateral", "Credit history", "Financial capacity", "Identity", "Loan structure", "Fraud", "External / macro", "Compliance", "Product / segment"], values: [0.25, 0.15, 0.15, 0.12, 0.10, 0.08, 0.05, 0.05, 0.05] }], {
      x: M, y: 1.85, w: 5.3, h: 3.55, barDir: "bar", chartColors: [CREW.underwriting.c], showValue: true, dataLabelFormatCode: "0.00", dataLabelColor: INK, dataLabelFontSize: 10,
      catAxisLabelColor: BODY, catAxisLabelFontSize: 10.5, catAxisLabelFontFace: F, valAxisHidden: true, valGridLine: { style: "none" }, catGridLine: { style: "none" },
      catAxisOrientation: "maxMin", showTitle: true, title: "Dimension weights · Commercial Vehicle row", titleFontSize: 11.5, titleColor: INK, titleFontFace: F, showLegend: false, barGapWidthPct: 40,
    });
    rect(s, M, 5.55, 2.55, 1.35, BG, { line: LINE, r: 0.1 });
    T(s, [{ text: "66% → 83%", options: { bold: true, fontSize: 19, color: GREEN, breakLine: true } }, { text: "of a trained model's skill on 5,000 real two-wheeler loans (target ≥ 75%)", options: { fontSize: 9.5, color: MUTED } }],
      { x: M + 0.15, y: 5.55, w: 2.3, h: 1.35, valign: "middle" });
    rect(s, M + 2.7, 5.55, 2.6, 1.35, BG, { line: LINE, r: 0.1 });
    T(s, [{ text: "0 of 750", options: { bold: true, fontSize: 19, color: GREEN, breakLine: true } }, { text: "checks where a worse input lowered the risk score", options: { fontSize: 9.5, color: MUTED } }],
      { x: M + 2.85, y: 5.55, w: 2.35, h: 1.35, valign: "middle" });
    const f = frame(s, "risk", 6.2, 1.9, 6.53);
    T(s, "Application › Risk Analysis: agent score, ML default probability and what drives each.", { x: 6.2, y: 1.9 + f.h + 0.12, w: 6.53, h: 0.3, fontSize: 10.5, italic: true, color: MUTED });
  }

  // 22 ─ Operations crew
  {
    const s = content("The 29 agents · Operations crew", "Operations: notify, keep the record, watch the process",
      "Three back-office agents. None of them can send anything outside the app.",
      "The Notifier fills templates into in-app notification rows — during its evaluation every outbound connection was blocked and counted, and there were zero attempts. Audit/Storage writes the application, its files and a masked officer view to SQL Server. The Process-Mining Analyst reads the event log through SQL views: every figure is a query; the optional briefing may only restate those figures.");
    const c = CREW.ops;
    const cards = [
      ["LuBell", "#26 Notifier", "Drafts notification rows from templates for borrower, officer and head.", "1,000 notifications, all 16 events: 100% correct · 0 outbound attempts"],
      ["LuArchive", "#27 Audit / Storage", "Persists files and SQL rows; officers see a masked copy.", "120 real files intact · no PAN or Aadhaar on the officer sheet"],
      ["LuChartLine", "#28 Process-Mining Analyst", "Conformance, variants, waits and slow agents from pm.* views; optional grounded briefing.", "38 of 38 figures equal pm4py on a real bank's event log (BPI 2017)"],
    ];
    for (let i = 0; i < cards.length; i++) {
      const [ic, t, d, m] = cards[i], y = 1.95 + i * 1.68;
      rect(s, M, y, 5.6, 1.5, c.t, { line: "DDD6FE", r: 0.1 });
      await iconCircle(s, M + 0.22, y + 0.25, 0.6, ic, WHITE, c.c);
      T(s, [{ text: t, options: { bold: true, fontSize: 13.5, color: INK, breakLine: true } }, { text: d, options: { fontSize: 11, color: BODY, breakLine: true } }, { text: m, options: { fontSize: 10, color: GREEN, bold: true } }],
        { x: M + 1.0, y: y + 0.1, w: 4.45, h: 1.3, valign: "middle", paraSpaceAfter: 3 });
    }
    const f = frame(s, "pm", 6.45, 1.95, 6.28);
    T(s, "Agent Activity › Process Mining — seeded demo process data: 0% conformant paths, 23 cases overdue.", { x: 6.45, y: 1.95 + f.h + 0.12, w: 6.28, h: 0.5, fontSize: 10.5, italic: true, color: MUTED });
  }

  // 23 ─ Divider 03
  await divider("03", "Workflows", "How a loan moves through its lifecycle, who acts at each step, and what a real run looks like.",
    ["Loan lifecycle", "End-to-end journey", "A real run"], "LuWorkflow");

  // 24 ─ Lifecycle
  {
    const s = content("Workflows", "The loan lifecycle", "One path for every product plus its own branch. The Workflow Router only recommends; a person moves the status.",
      "Read row one left to right, then row two right to left. Agents annotate the steps they support. Any state before a decision can end in rejected or cancelled — always by a person.");
    const r1 = [["draft", "borrower"], ["submitted", "system"], ["documentsProcessing", "agents: intake crew"], ["documentsVerified", "officer"], ["product checks", "branch · see below"]];
    const r2 = [["disbursed", "—"], ["sanctioned", "operations"], ["accepted", "officer / head"], ["offerGenerated", "borrower"], ["underReview", "officer · credit committee"]];
    const nw = 2.1, gp = 0.4, nh = 0.85;
    r1.forEach(([t, o], i) => {
      const x = M + i * (nw + gp);
      node(s, x, 2.05, nw, nh, t, o, { fill: i === 4 ? AMBER_T : INDIGO_T, line: i === 4 ? "FCD34D" : INDIGO_M, tc: i === 4 ? "92400E" : INDIGO_D, ts: 11.5, ss: 9.5 });
      if (i < 4) arrow(s, x + nw + 0.02, 2.47, x + nw + gp - 0.02, 2.47, { color: INDIGO });
    });
    arrow(s, M + 4 * (nw + gp) + nw / 2, 2.92, M + 4 * (nw + gp) + nw / 2, 3.53, { color: INDIGO });
    r2.forEach(([t, o], i) => {
      const x = M + i * (nw + gp);
      node(s, x, 3.55, nw, nh, t, o, { fill: i === 0 ? GREEN_T : INDIGO_T, line: i === 0 ? "86EFAC" : INDIGO_M, tc: i === 0 ? "166534" : INDIGO_D, ts: 11.5, ss: 9.5 });
      if (i < 4) arrow(s, x + nw + gp - 0.02, 3.97, x + nw + 0.02, 3.97, { color: INDIGO });
    });
    // agent annotations
    const ann = [[2, 1, "Intake · Privacy"], [3, 1, "Case assessment"], [4, 2, "Underwriting crew · Router"], [3, 2, "Offer Generation"], [2, 2, "Head approves > ₹5 lakh"]];
    ann.forEach(([i, row, t]) => {
      const x = M + i * (nw + gp) + 0.1, y = row === 1 ? 1.73 : 4.45;
      chip(s, t, x, y, nw - 0.2, 0.27, row === 2 && i === 2 ? AMBER_T : CREW.fraud.t, row === 2 && i === 2 ? "92400E" : "9F1239", { fs: 8.5 });
    });
    T(s, "PRODUCT BRANCHES (between documentsVerified and review)", { x: M, y: 4.95, w: 8, h: 0.28, fontSize: 10.5, bold: true, color: MUTED, charSpacing: 1 });
    const br = [["Gold", "valuation scheduled → valued"], ["Tractor · Microfinance", "field verification"], ["Loan Against Property", "property valuation → legal"],
      ["Home", "6 checks: property, legal, income, docs, technical"], ["MSME · Working capital", "bank & GST analysis → committee"], ["Used vehicle", "vehicle inspection"]];
    br.forEach(([t, d], i) => {
      const x = M + (i % 3) * 4.1, y = 5.3 + Math.floor(i / 3) * 0.78;
      rect(s, x, y, 3.9, 0.66, BG, { line: LINE, r: 0.08 });
      T(s, [{ text: t + "  ", options: { bold: true, color: INK } }, { text: d, options: { color: MUTED } }], { x: x + 0.15, y, w: 3.65, h: 0.66, fontSize: 10.5, valign: "middle" });
    });
  }

  // 25 ─ Journey swimlane
  {
    const s = content("Workflows", "End to end: who does what", "Agents work between the human steps; every agent output is evidence for the next person.",
      "Follow the numbers. Agents (red lane) do the reading, checking and drafting between the human steps. The officer triggers underwriting; the head only steps in above ₹5 lakh.");
    const lanes = [["Borrower", "LuUser", INDIGO, INDIGO_T], ["AI agents", "LuBot", CREW.fraud.c, CREW.fraud.t], ["Officer", "LuUserCheck", "0F766E", "CCFBF1"], ["Head", "LuCrown", CREW.underwriting.c, CREW.underwriting.t]];
    const top = 1.85, lh = 1.22, lx = M, lw = 1.55, sx = M + 1.7;
    for (let i = 0; i < lanes.length; i++) {
      const [t, ic, c, tint] = lanes[i], y = top + i * lh;
      rect(s, lx, y + 0.04, CW, lh - 0.08, i % 2 ? WHITE : BG, { line: LINE, r: 0.06 });
      await iconCircle(s, lx + 0.12, y + 0.36, 0.46, ic, tint, c);
      T(s, t, { x: lx + 0.65, y, w: 0.95, h: lh, fontSize: 12, bold: true, color: INK, valign: "middle" });
    }
    const steps = [
      [0, "Picks a product, uploads documents"], [1, "Intake + masking read each file"], [0, "Confirms fields, takes a selfie, submits"],
      [1, "Integrity, face, liveness, case assessment"], [2, "Reviews fraud report and policy check"], [1, "Underwriting crew drafts memo & offer"],
      [2, "Recommends approve or reject"], [3, "Approves above ₹5 lakh"], [0, "Receives and accepts the offer"],
    ];
    // staircase: consecutive steps sit in different lanes, so boxes may overlap
    // horizontally; each arrow runs from the right part of one box to the next
    const bw = 1.64, bh = 0.84, cw = (W - M - sx - bw) / (steps.length - 1);
    const pos = steps.map(([lane], i) => ({ x: sx + i * cw, y: top + lane * lh + (lh - bh) / 2 }));
    steps.forEach(([lane, t], i) => {
      const { x, y } = pos[i];
      if (i < steps.length - 1) {
        const n = pos[i + 1], down = n.y > y;
        arrow(s, x + bw - 0.25, down ? y + bh : y, n.x + 0.25, down ? n.y : n.y + bh, { color: "94A3B8", w: 1.25 });
      }
    });
    steps.forEach(([lane, t], i) => {
      const { x, y } = pos[i], c = lanes[lane][2];
      rect(s, x, y, bw, bh, WHITE, { line: c, lw: 1.25, r: 0.08, shadow: true });
      chip(s, String(i + 1), x + 0.08, y + 0.08, 0.28, 0.28, c, WHITE, { fs: 9 });
      T(s, t, { x: x + 0.44, y: y + 0.04, w: bw - 0.5, h: bh - 0.08, fontSize: 9.5, color: INK, valign: "middle" });
    });
  }

  // 26 ─ A real run
  {
    const s = content("Workflows", "A real run through the wizard", "Captured from the app with a synthetic applicant (generated identity — no real personal data).",
      "1: the product decides the document checklist. 2: each upload is read by the intake crew and shown back for confirmation — here every field of the synthetic PAN and Aadhaar was read exactly. 3: on Review & Submit the tampering cascade explains why a document needs a closer look. 4: the case assessment fuses everything into an advisory tier and a policy result.");
    const cells = [
      ["1", "Product checklist", "wizard_required"], ["2", "Fields read by the intake crew", "wizard_extract"],
      ["3", "Integrity: plain-language findings", "integrity_pan"], ["4", "Fraud & policy assessment", "assessment_top"],
    ];
    cells.forEach(([n, t, k], i) => {
      const cx = M + (i % 2) * 6.15, y = 1.8 + Math.floor(i / 2) * 2.6;
      const fs = frameSize(k, 5.95, 2.1), x = cx + (5.95 - fs.w) / 2;
      chip(s, n, x, y, 0.32, 0.32, INDIGO, WHITE, { fs: 11 });
      T(s, t, { x: x + 0.45, y, w: 5.3, h: 0.32, fontSize: 12.5, bold: true, color: INK, valign: "middle" });
      frame(s, k, x, y + 0.4, 5.95, 2.1);
    });
  }

  // 27 ─ Divider 04
  await divider("04", "Trust & control", "One gateway to every model, guardrails that are tested, and a trace of every step in SQL Server.",
    ["LLM gateway", "Guardrails", "Tracing", "Data model"], "LuShieldCheck");

  // 28 ─ LLM gateway
  {
    const s = content("Trust & control", "One gateway for every model call",
      "The Head Manager picks the model per agent, per lane or by default. The gateway enforces privacy and queues the calls.",
      "Every model call goes through app/agents/llm/gateway.py. It resolves which model to use (agent override, then vision/text lane, then default) and snapshots the setting on every run. A hosted provider needs the Head's explicit acknowledgement, is refused outright when FINSIGHT_FORBID_HOSTED is set, and only receives pseudonymised text. One slot per provider with a priority queue protects the 8 GB GPU; a call that waits more than 180 s fails cleanly.");
    const X = M, Wd = 5.5, c = AMBER;
    const st = [
      ["Agent asks for a model", "gateway.chat(agent_id, …)", BG, LINE, INK],
      ["1 · Resolve the model", "agent override → vision / text lane → default · snapshot per run", AMBER_T, "FCD34D", "92400E"],
      ["2 · Privacy guard", "FORBID_HOSTED switch · Head acknowledgement · pseudonymised text for hosted", AMBER_T, "FCD34D", "92400E"],
      ["3 · LLM gate", "one slot per provider · priority queue · 180 s max wait", AMBER_T, "FCD34D", "92400E"],
    ];
    st.forEach(([t, d, fl, ln, tc], i) => {
      const y = 1.95 + i * 1.0;
      node(s, X, y, Wd, 0.8, t, d, { fill: fl, line: ln, tc, ss: 9.5 });
      arrow(s, X + Wd / 2, y + 0.82, X + Wd / 2, y + 0.98);
    });
    arrow(s, X + Wd / 2, 5.77, X + 1.35, 6.0); arrow(s, X + Wd / 2, 5.77, X + Wd - 1.35, 6.0);
    node(s, X, 6.02, 2.65, 0.9, "Ollama (local)", "127.0.0.1 · pooled · images fitted to context", { fill: GREEN_T, line: "86EFAC", tc: "166534", ss: 9 });
    node(s, X + 2.85, 6.02, 2.65, 0.9, "Anthropic Claude", "optional · not configured today", { fill: BG, line: LINE, tc: MUTED, ss: 9 });
    const f = frame(s, "settings_ai", 6.45, 1.95, 6.28);
    T(s, "System Settings › AI Models (Head only): default, lanes and a per-agent override table.", { x: 6.45, y: 1.95 + f.h + 0.12, w: 6.28, h: 0.3, fontSize: 10.5, italic: true, color: MUTED });
  }

  // 29 ─ Guardrails
  {
    const s = content("Trust & control", "Guardrails — each one pinned by a test",
      "The promises the design makes, and how each was verified.",
      "These are the rules that keep the agents advisory and the data private. Each is enforced in code and checked by an automated test or measured during the evaluation.");
    const g = [
      ["LuLock", "LLM never sets a score", "Scores, tiers and verdicts come from tools only.", "tier unchanged 60 / 60"],
      ["LuSearch", "Grounding gate", "Every number ≥ 10 in model text must be in the evidence.", "ungrounded text dropped"],
      ["LuShieldCheck", "Tighten-only supervisor", "May make the next step stricter, never looser.", "1 loosening attempt blocked"],
      ["LuUserCheck", "Nothing auto-rejects", "Advisory routes only; above ₹5 lakh needs the Head.", "router guard test"],
      ["LuBan", "No side-effect tools", "No agent can approve, reject, file, accept or send.", "checked on all 29"],
      ["LuTimer", "LLM budget per path", "/extract ≤ 1 call; masking, face and assessment 0.", "call-count test"],
      ["LuGauge", "Authenticity capped", "The fabrication signal is capped at 0.60, never alone.", "cap held on all 418"],
      ["LuGlobe", "Hosted models gated", "Head acknowledgement, kill switch, pseudonymised text.", "settings + gateway tests"],
      ["LuKey", "Identity keys hashed", "Dedupe stores HMAC digests, never raw PAN / Aadhaar.", "holds"],
      ["LuPlug", "Contracts unchanged", "Pre-agent endpoints answer exactly as before.", "24 / 24 contract tests"],
    ];
    for (let i = 0; i < g.length; i++) {
      const [ic, t, d, v] = g[i], x = M + (i % 5) * 2.46, y = 1.95 + Math.floor(i / 5) * 2.5;
      rect(s, x, y, 2.3, 2.3, BG, { line: LINE, r: 0.1 });
      await iconCircle(s, x + 0.2, y + 0.2, 0.55, ic, INDIGO_T, INDIGO);
      T(s, t, { x: x + 0.2, y: y + 0.85, w: 1.95, h: 0.5, fontSize: 12.5, bold: true, color: INK, valign: "top" });
      T(s, d, { x: x + 0.2, y: y + 1.3, w: 1.95, h: 0.6, fontSize: 10, color: MUTED, valign: "top" });
      s.addImage({ data: await icon("LuCircleCheck", GREEN), x: x + 0.2, y: y + 1.94, w: 0.2, h: 0.2 });
      T(s, v, { x: x + 0.46, y: y + 1.9, w: 1.8, h: 0.3, fontSize: 9.5, bold: true, color: GREEN });
    }
  }

  // 30 ─ Tracing
  {
    const s = content("Trust & control", "Every step is traced — and becomes process-mining data",
      "Runs and steps go to memory, JSON files and SQL Server. A database outage never fails a request.",
      "The tracer records each agent run and step (inputs summary, timing, model, LLM calls, outcome). In memory it feeds the live p50/p95 on the Agent Activity page; as JSON it is kept under storage/agent_traces; with the SQL sink on it writes agent.run / step / tool_call and logs a business event through pm.usp_log_event into pm.event_log. If SQL Server is down, events are spooled to disk and replayed later.");
    const X = M;
    node(s, X, 2.0, 1.7, 0.9, "Agent step", "run · step · tool call", { fill: BG });
    arrow(s, X + 1.72, 2.45, X + 1.98, 2.45);
    node(s, X + 2.0, 2.0, 1.5, 0.9, "Tracer", "timing · model · outcome", { fill: INDIGO_T, line: INDIGO_M, tc: INDIGO_D });
    const outs = [["In memory", "/agents/stats → live p50 / p95"], ["JSON files", "storage/agent_traces/"], ["SQL sink", "agent.run · step · tool_call"]];
    outs.forEach(([t, d], i) => {
      const y = 1.7 + i * 1.02;
      arrow(s, X + 3.52, 2.45, X + 3.83, y + 0.4);
      node(s, X + 3.85, y, 2.05, 0.8, t, d, { fill: WHITE, line: LINE, ts: 11, ss: 9 });
    });
    arrow(s, X + 4.87, 4.56, X + 4.87, 4.86);
    node(s, X + 2.0, 4.88, 3.9, 0.8, "pm.usp_log_event → pm.event_log", "spool to disk + replay if SQL Server is down", { fill: CREW.ops.t, line: "DDD6FE", tc: CREW.ops.c, ss: 9 });
    arrow(s, X + 3.95, 5.7, X + 3.95, 5.95);
    node(s, X + 2.0, 5.97, 3.9, 0.9, "pm.vw_* views", "→ Process-Mining Analyst · Agent Activity page", { fill: CREW.ops.t, line: "DDD6FE", tc: CREW.ops.c, ss: 9.5 });
    T(s, "Kill switch: AGENTS_ENABLED=false runs the same tools directly, untraced.", { x: X, y: 3.25, w: 1.85, h: 1.2, fontSize: 9.5, italic: true, color: MUTED, valign: "top" });
    const f = frame(s, "agents_tab", 6.75, 1.9, 5.98, 5.0);
    T(s, "Agent Activity › Agents: each agent's kind, model, runs, p50 / p95 and flags.", { x: 6.75, y: 1.9 + f.h + 0.1, w: 5.98, h: 0.3, fontSize: 10.5, italic: true, color: MUTED });
  }

  // 31 ─ Data model
  {
    const s = content("Trust & control", "The database: SQL Server 'FinSight'", "9 schemas · 82 tables · 17 views. Migrations live in docs/mssql/ — 08-agentic-layer.sql adds the agent tables.",
      "core holds the business records; fraud, comp and risk hold each engine's results; agent holds traces and the audit of model-setting changes; pm is a full process-mining model (event log, variants, conformance); ops, audit and ref hold users, notifications, audit trails and reference codes.");
    const sc = [
      ["core", 9, "customers · loan_applications · uploaded_documents · document_extraction_results · loan_offers", INDIGO],
      ["fraud", 9, "fraud_assessments · fraud_cases · fraud_rings · identity_keys · fraud_weight_sets", CREW.fraud.c],
      ["comp", 5, "compliance_documents · document_chunks · rules · rule_candidates · check_results", CREW.compliance.c],
      ["risk", 3, "risk_assessments · pricing_models · risk_thresholds", CREW.underwriting.c],
      ["agent", 4, "run · step · tool_call · llm_setting_audit", "BE123C"],
      ["pm", 20, "event_log · activity · case_metrics · case_variant · conformance_result · deviation", CREW.ops.c],
      ["ops", 8, "users · notifications · notification_templates · loan_products", "0891B2"],
      ["audit", 3, "audit_log · audit_log_details · business_events", MUTED],
      ["ref", 21, "application_status · lifecycle_transition · document_type · fraud_severity …", MUTED],
    ];
    sc.forEach(([n, k, t, c], i) => {
      const x = M + (i % 3) * 4.1, y = 1.95 + Math.floor(i / 3) * 1.68;
      rect(s, x, y, 3.9, 1.5, BG, { line: LINE, r: 0.1 });
      T(s, n, { x: x + 0.22, y: y + 0.15, w: 2, h: 0.45, fontSize: 18, bold: true, color: c, fontFace: FM });
      chip(s, `${k} tables`, x + 2.6, y + 0.2, 1.1, 0.32, WHITE, c, { line: LINE, fs: 9.5 });
      T(s, t, { x: x + 0.22, y: y + 0.65, w: 3.5, h: 0.75, fontSize: 10, color: BODY, valign: "top", fontFace: FM });
    });
  }

  // ─ Divider 05
  await divider("05", "Evaluation", "Tested twice: when each agent was built, and again on real and public data — against pass marks written down before the run.",
    ["Method", "Scorecard", "What real data changed", "Finding real forgeries", "Latency", "Limits & next steps"], "LuChartBar");

  // ─ Method (real-data evaluation, in short)
  {
    const s = content("Evaluation", "How the agents were tested: twice, with the target fixed first",
      "Round 1 as each agent was built, on synthetic data. Round 2 on real and public data — the scores in this deck.",
      "Round 1 (report 06) tested each agent as it was built, mostly on generated documents. Round 2 (report 08) is the one that counts: every agent on its own, on real or public production-like data. The target for each agent was written into eval/targets_real.yaml before the agent saw the data. Each dataset is split 30% development and 70% test by a fixed seed, group-wise, so a person, a document template or a loan never sits in both. A weakness was fixed on the development split only, and the test split was scored once more afterwards. Every model call stayed on this PC.");
    const ch = [["Fix the target", "targets_real.yaml"], ["Split the data", "30% dev · 70% test"], ["Run the app's own path", "what an upload triggers"], ["Fix on dev only", "13 agents changed"], ["Score test once", "95% confidence interval"]];
    ch.forEach(([t, d], i) => {
      const x = M + i * 2.43;
      s.addShape(pres.shapes.CHEVRON, { x, y: 1.95, w: 2.4, h: 0.85, fill: { color: i % 2 ? INDIGO_T : "E0E7FF" }, line: { color: INDIGO_M } });
      T(s, [{ text: t, options: { bold: true, fontSize: 11.5, color: INDIGO_D, breakLine: true } }, { text: d, options: { fontSize: 9, color: MUTED } }],
        { x: x + 0.35, y: 1.95, w: 1.8, h: 0.85, align: "center", valign: "middle" });
    });
    T(s, "RULES OF THE TEST", { x: M, y: 3.0, w: 4, h: 0.28, fontSize: 10.5, bold: true, color: MUTED, charSpacing: 2 });
    chipFlow(s, ["Strong = target met", "Acceptable = within 10%", "Weak = below", "every model call on this PC", "no real PAN or Aadhaar numbers", "aggregate results only"], M, 3.33, CW,
      { fill: INDIGO_T, line: INDIGO_T, color: INDIGO_D, h: 0.36, fs: 11, bold: true });
    T(s, "REAL AND PUBLIC DATA", { x: M, y: 3.95, w: 4, h: 0.28, fontSize: 10.5, bold: true, color: MUTED, charSpacing: 2 });
    const ds = [
      ["LuFileWarning", "Forged documents", "Find it again!: 988 real receipts, 163 forged by people. SIDTD: ID images with replaced or repainted fields."],
      ["LuCamera", "Photos and scans", "MIDV-500 phone frames of ID cards; SROIE, FUNSD and RVL-CDIP real scanned pages."],
      ["LuScanFace", "Faces and liveness", "AgeDB-30, CALFW, CPLFW and LFW pair protocols; NUAA and CelebA-Spoof printed-photo attacks."],
      ["LuBanknote", "Loans and accounts", "233,000 Indian two-wheeler loans with real defaults (L&T); 4,500 real bank accounts (Berka)."],
      ["LuBookOpen", "Regulations and process", "ObliQA: 40 real regulations with gold answers. BPI 2017: a real bank's loan event log."],
      ["LuIdCard", "Indian IDs and statements", "Labelled synthetic PAN / Aadhaar cards and statements in real bank layouts — no lawful public set of real ones exists."],
    ];
    for (let i = 0; i < ds.length; i++) {
      const [ic, t, d] = ds[i], x = M + (i % 3) * 4.1, y = 4.3 + Math.floor(i / 3) * 1.32;
      rect(s, x, y, 3.9, 1.18, BG, { line: LINE, r: 0.1 });
      await iconCircle(s, x + 0.18, y + 0.2, 0.5, ic, INDIGO_T, INDIGO);
      T(s, [{ text: t, options: { bold: true, fontSize: 12, color: INK, breakLine: true } }, { text: d, options: { fontSize: 9.5, color: MUTED } }],
        { x: x + 0.82, y: y + 0.06, w: 2.98, h: 1.06, valign: "middle", paraSpaceAfter: 2 });
    }
  }

  // ─ Scorecard (real data, test splits)
  {
    const s = content("Evaluation", "Scorecard on real data: 25 of 29 agents meet their target", null,
      "Strong means the agent meets the target set before its run; Acceptable is within 10%; Weak is below that. Two agents are Acceptable: PII Masking (98.4% against a target of 100%) and the Tamper Locator (74.6% against 80%). Two are Weak: Authenticity - the local vision model cannot see a local edit even when shown the edited area magnified - and Preprocessing, which finds a card's outline in 69% of phone photos against a target of 80%. All figures are from held-out test splits; the 95% intervals are in report 08.");
    s.addChart(pres.charts.DOUGHNUT, [{ name: "Rating", labels: ["Strong", "Acceptable", "Weak"], values: [25, 2, 2] }], {
      x: M - 0.1, y: 1.45, w: 3.8, h: 3.4, holeSize: 64, chartColors: [GREEN, AMBER, RED], showLegend: false, showValue: false, showPercent: false, dataBorder: { pt: 1.5, color: "FFFFFF" },
    });
    T(s, [{ text: "29", options: { fontSize: 34, bold: true, color: INK, breakLine: true } }, { text: "agents", options: { fontSize: 11, color: MUTED } }],
      { x: M + 0.75, y: 2.65, w: 2.0, h: 1.0, align: "center", valign: "middle" });
    [["25", "Strong — target met", GREEN], ["2", "Acceptable — within 10%", AMBER], ["2", "Weak — below", RED]].forEach(([v, l, c], i) => {
      const y = 4.95 + i * 0.6;
      rect(s, M, y, 3.4, 0.5, BG, { line: LINE, r: 0.08 });
      T(s, v, { x: M + 0.15, y, w: 0.6, h: 0.5, fontSize: 18, bold: true, color: c, valign: "middle" });
      T(s, l, { x: M + 0.8, y, w: 2.5, h: 0.5, fontSize: 12, color: INK, valign: "middle" });
    });
    T(s, "Held-out test splits · 95% intervals in report 08", { x: M, y: 6.78, w: 3.4, h: 0.25, fontSize: 9, italic: true, color: MUTED });
    const sc = [
      [1, "Preprocessing", "69.1%", "≥ 80%", "W"], [2, "Extraction", "94.4%", "≥ 90%", "S"], [3, "Validation", "100%", "100%", "S"], [4, "Document Identity", "94.4%", "≥ 90%", "S"],
      [5, "PII Masking", "98.4%", "100%", "A"], [6, "Face Verification", "95.9%", "≥ 90%", "S"], [7, "Tampering Forensics", "88.9%", "≥ 85%", "S"], [8, "Authenticity", "11.2%", "≥ 70%", "W"],
      [9, "Tamper Explainer", "98.3%", "≥ 95%", "S"], [10, "Tamper Locator", "74.6%", "≥ 80%", "A"], [11, "Cross-Doc Verification", "100%", "≥ 95%", "S"], [12, "Dedupe", "96.3%", "≥ 95%", "S"],
      [13, "Fraud Fusion", "96.7%", "≥ 85%", "S"], [14, "Fraud Patterns", "F1 0.92", "≥ 0.85", "S"], [15, "Fraud Triage", "100%", "≥ 80%", "S"], [16, "KB Ingestion", "92.7%", "≥ 85%", "S"],
      [17, "Rule Authoring", "83.7%", "≥ 70%", "S"], [18, "Regulatory Q&A", "79.4%", "≥ 70%", "S"], [19, "Policy Check", "100%", "100%", "S"], [20, "Income Analysis", "F1 1.00", "≥ 0.90", "S"],
      [21, "Risk Scoring", "skill 0.83", "≥ 0.75", "S"], [22, "Credit Memo", "100%", "≥ 95%", "S"], [23, "UW Supervisor", "0.72×", "≤ 0.85×", "S"], [24, "Offer Generation", "100%", "100%", "S"],
      [25, "Workflow Router", "100%", "100%", "S"], [26, "Notifier", "100%", "100%", "S"], [27, "Audit / Storage", "100%", "100%", "S"], [28, "Process Mining", "100%", "100%", "S"],
      [29, "Liveness", "ACER 0.4%", "≤ 10%", "S"],
    ];
    const RC = { S: [GREEN, "F0FDF4", "BBF7D0"], A: [AMBER, "FFFBEB", "FDE68A"], W: [RED, "FEF2F2", "FECACA"] };
    const gx = 4.3, tw = 1.6, th = 0.84, gpx = 0.08, gpy = 0.08;
    sc.forEach(([n, name, v, tgt, r], i) => {
      const x = gx + (i % 5) * (tw + gpx), y = 1.5 + Math.floor(i / 5) * (th + gpy);
      const [c, fill, ln] = RC[r];
      rect(s, x, y, tw, th, fill, { line: ln, r: 0.07 });
      T(s, [{ text: `${n}  ${name}`, options: { bold: true, fontSize: 8.5, color: INK, breakLine: true } },
            { text: v, options: { bold: true, fontSize: 13, color: c, breakLine: true } },
            { text: "target " + tgt, options: { fontSize: 8, color: MUTED } }],
        { x: x + 0.08, y: y + 0.02, w: tw - 0.12, h: th - 0.04, valign: "middle" });
    });
  }

  // ─ What real data changed
  {
    const s = content("Evaluation", "Real data found weaknesses — 13 agents were fixed",
      "Each fix was developed on the development split only; the test split was scored once afterwards. First run → after the fix.",
      "This is the main result of the second round: the first run on real data exposed weaknesses the synthetic tests had hidden, and thirteen agents were changed because of it. Left, the measures where higher is better; right, where lower is better. The masker missed IDs printed inside a line of text; the statement parser recovered 4% of the rows of real Indian layouts; the locator boxed nothing; face search used a 1:1 threshold for a 1:N search, so four in ten new applicants got a false 'same face' alert. Every one of these is fixed in the app, with tests.");
    const L = ["PII Masking · no ID readable", "Statement parser · rows recovered", "Tamper Locator · region boxed", "Extraction · fields correct",
      "Cross-Doc · name pairs correct", "Preprocessing · card outline found", "Risk Scoring · share of trained skill"];
    s.addChart(pres.charts.BAR, [
      { name: "First run", labels: L, values: [42.1, 4.4, 0, 84.7, 90.9, 47.3, 66] },
      { name: "After the fix", labels: L, values: [98.4, 94.8, 74.6, 94.4, 100, 69.1, 83] },
    ], {
      x: M - 0.05, y: 1.8, w: 7.1, h: 5.1, barDir: "bar", barGrouping: "clustered", chartColors: ["CBD5E1", INDIGO], showValue: true, dataLabelFormatCode: '0"%"', dataLabelFontSize: 10, dataLabelColor: INK,
      catAxisLabelColor: BODY, catAxisLabelFontSize: 10.5, catAxisLabelFontFace: F, catAxisOrientation: "maxMin", valAxisHidden: true, valAxisMinVal: 0, valAxisMaxVal: 122,
      valGridLine: { style: "none" }, catGridLine: { style: "none" }, showLegend: true, legendPos: "b", legendFontSize: 10.5, legendFontFace: F,
      showTitle: true, title: "Higher is better · test split", titleFontSize: 11.5, titleColor: INK, titleFontFace: F, barGapWidthPct: 45,
    });
    const RX = 7.55, RW = W - M - RX;
    T(s, "LOWER IS BETTER", { x: RX, y: 1.9, w: 4, h: 0.28, fontSize: 10.5, bold: true, color: MUTED, charSpacing: 2 });
    const low = [
      ["Dedupe", "new applicants given a false 'same face' alert", "41.8% → 0.4%"],
      ["Tampering Forensics", "genuine documents wrongly rated HIGH", "17.0% → 0.8%"],
      ["Document Identity", "right documents refused", "11.6% → 1.6%"],
      ["Preprocessing", "scans made harder to read by the cleanup", "30.7% → 6.0%"],
      ["Fraud Triage", "false fraud rings proposed", "71 → 0"],
    ];
    low.forEach(([a, d, v], i) => {
      const y = 2.25 + i * 0.78;
      rect(s, RX, y, RW, 0.68, BG, { line: LINE, r: 0.08 });
      T(s, [{ text: a, options: { bold: true, fontSize: 11.5, color: INK, breakLine: true } }, { text: d, options: { fontSize: 9.5, color: MUTED } }],
        { x: RX + 0.15, y, w: RW - 1.95, h: 0.68, valign: "middle" });
      T(s, v, { x: RX + RW - 1.8, y, w: 1.65, h: 0.68, fontSize: 13, bold: true, color: GREEN, align: "right", valign: "middle" });
    });
    rect(s, RX, 6.2, RW, 0.68, INDIGO_T, { line: INDIGO_M, r: 0.08 });
    T(s, [{ text: "Also fixed: ", options: { bold: true, color: INDIGO_D } },
          { text: "the Supervisor could never recommend 'approve' — loan-terms rules were checked before any offer existed.", options: { color: BODY } }],
      { x: RX + 0.15, y: 6.2, w: RW - 0.3, h: 0.68, fontSize: 10.5, valign: "middle" });
  }

  // ─ Spotlight: the tampering detector
  {
    const s = content("Evaluation", "Finding real forgeries: from 17% to 89%",
      "The pixel signals were built on synthetic pages. Real forged documents exposed two problems, fixed one after the other.",
      "The most instructive fix. As first configured the detector flagged 84% of GENUINE real documents - its signals had been scaled on synthetic pages - and it 'found' 75% of forgeries only because it flagged nearly everything. Calibrating each signal on genuine real documents removed the false alarms but left only 17% of forgeries found. The third step was a small patch-level detector trained on the development split of real forged receipts and ID images: two models per document family, each calibrated on documents it never saw, evidence only when both agree, and alone it can only reach MEDIUM. On the test split it finds 89% of forgeries with 24% of genuine documents flagged and 0.8% rated HIGH. Caveats: receipts edited by people are found 54% of the time; the 95% on ID images is for script-made forgeries; and no forged Indian document exists to test on.");
    const steps = [
      ["1", "As first configured", "84% of genuine documents flagged", "Signals scaled on synthetic pages saturate on real scans; forged documents ranked below genuine ones (AUC 0.36).", RED, RED_T],
      ["2", "Calibrated on genuine documents", "False alarms gone — but 17% of forgeries found", "A pixel signal counts only above what 95% of genuine documents reach; ELA and noise need a second signal.", AMBER, AMBER_T],
      ["3", "A detector trained on real forgeries", "89% of forgeries found", "Two small patch models per document family, each calibrated on documents it never saw. Evidence only when both agree; alone it gives MEDIUM, never HIGH. It names the region, so the locator can draw it.", GREEN, GREEN_T],
    ];
    steps.forEach(([n, t, hl, d, c, tint], i) => {
      const y = 1.95 + i * 1.62, h = i === 2 ? 1.7 : 1.48;
      rect(s, M, y, 5.9, h, BG, { line: LINE, r: 0.1 });
      chip(s, n, M + 0.2, y + 0.2, 0.4, 0.4, c, WHITE, { fs: 12 });
      T(s, [{ text: t, options: { bold: true, fontSize: 13, color: INK, breakLine: true } },
            { text: hl, options: { bold: true, fontSize: 12, color: c, breakLine: true } },
            { text: d, options: { fontSize: 10, color: MUTED } }],
        { x: M + 0.8, y: y + 0.08, w: 4.95, h: h - 0.16, valign: "middle", paraSpaceAfter: 3 });
    });
    const SL = ["As first configured", "After calibration", "With the patch detector"];
    s.addChart(pres.charts.BAR, [
      { name: "Forged documents flagged", labels: SL, values: [74.9, 16.6, 88.9] },
      { name: "Genuine documents flagged", labels: SL, values: [83.5, 26.1, 24.0] },
    ], {
      x: 6.75, y: 1.85, w: 5.98, h: 3.5, barDir: "col", barGrouping: "clustered", chartColors: [INDIGO, "CBD5E1"], showValue: true, dataLabelFormatCode: '0"%"', dataLabelFontSize: 11, dataLabelColor: INK,
      catAxisLabelColor: BODY, catAxisLabelFontSize: 10.5, catAxisLabelFontFace: F, valAxisHidden: true, valAxisMinVal: 0, valAxisMaxVal: 100, valGridLine: { color: "E2E8F0", size: 0.5 }, catGridLine: { style: "none" },
      showLegend: true, legendPos: "b", legendFontSize: 10.5, legendFontFace: F, showTitle: true, title: "Test split · 235 forged and 383 genuine documents", titleFontSize: 11.5, titleColor: INK, titleFontFace: F, barGapWidthPct: 60,
    });
    const facts = [["54%", "of receipts edited by people (Paint, GIMP)"], ["95%", "of ID images forged by script"], ["0.5%", "of genuine ID phone photos flagged"]];
    facts.forEach(([v, l], i) => {
      const x = 6.75 + i * 2.02;
      rect(s, x, 5.45, 1.94, 0.82, BG, { line: LINE, r: 0.08 });
      T(s, v, { x: x + 0.1, y: 5.45, w: 0.78, h: 0.82, fontSize: 16, bold: true, color: INDIGO, valign: "middle" });
      T(s, l, { x: x + 0.86, y: 5.45, w: 1.04, h: 0.82, fontSize: 8.5, color: MUTED, valign: "middle" });
    });
    T(s, "It has never seen a forged Indian document — none exists to test on — so its tier stays advice to a reviewer.",
      { x: 6.75, y: 6.36, w: 5.98, h: 0.5, fontSize: 10, italic: true, color: BODY, valign: "top" });
  }

  // ─ Build-time defects (round 1)
  {
    const s = content("Evaluation", "Build-time testing found — and fixed — 17 defects",
      "Round 1. The eight that mattered most; each was caught by an agent's evaluation or the latency benchmark, not by users.",
      "The first two are the most serious: a whole-server crash from two threads inside PDFium at once, and a PII leak of a real PAN photographed sideways. Both are fixed and pinned by tests.");
    const d = [
      ["PDF handling", "Two threads inside PDFium at once crashed the whole server — with no error logged.", "One process-wide PDFium lock."],
      ["PII Masking", "Sideways cards split the wrong way; one real PAN photo leaked in full.", "Upright rotation first: 0 leaks."],
      ["Document Identity", "A payslip showing a PAN passed as a PAN — 54% of wrong uploads caught.", "Also reads the page's own words: 100%."],
      ["LLM gateway", "'localhost' tried IPv6 first on Windows: +2 s on every model call.", "127.0.0.1 and one pooled connection."],
      ["Risk Scoring", "ML features built unlike training: held-out AUC 0.669.", "Training feature builder reused: 0.771."],
      ["Policy Check", "Face rule used another model's 0.62 scale: every genuine match failed.", "SFace threshold 0.363."],
      ["Structured LLM calls", "The model echoed the schema: 92% of written Q&A answers discarded.", "Schema-enforced JSON output."],
      ["Cross-Doc Verification", "A shared surname alone counted as a name match — family members looked like one person.", "Fixed in the browser and Python logic."],
    ];
    d.forEach(([t, b, a], i) => {
      const x = M + (i % 4) * 3.07, y = 1.95 + Math.floor(i / 4) * 2.35;
      rect(s, x, y, 2.9, 2.18, BG, { line: LINE, r: 0.1 });
      chip(s, t, x + 0.15, y + 0.15, Math.min(2.6, 0.35 + t.length * 0.075), 0.32, RED_T, "991B1B", { fs: 9.5 });
      T(s, b, { x: x + 0.18, y: y + 0.58, w: 2.58, h: 0.95, fontSize: 10.5, color: BODY, valign: "top" });
      T(s, [{ text: "Fix  ", options: { bold: true, color: GREEN } }, { text: a, options: { color: INK } }], { x: x + 0.18, y: y + 1.55, w: 2.58, h: 0.55, fontSize: 10.5, valign: "top" });
    });
    T(s, "+ 9 more: card rules applied to full pages · images overflowing the model context · regulation titles ignored by grounding · unreadable photo rows treated as real · SQL inserts refused on tables with triggers · a parser error result · review statuses in the router · a stale Agent Activity tab · PDFs rasterised needlessly",
      { x: M, y: 6.66, w: CW, h: 0.38, fontSize: 9.5, color: MUTED, italic: true });
  }

  // ─ Latency
  {
    const s = content("Evaluation", "Latency on one laptop", "Median seconds per document on an idle machine, local model already loaded (RTX 4060, 8 GB).",
      "Timed on development documents, one at a time, with nothing else running. The model upload paths take 7 to 16 seconds; everything without a model call is around a second, and the integrity check of an ID photo is three seconds with the new patch detector included. Against the pre-agent build, the two uploads that use the model got 9 to 11% faster and no other endpoint slowed by more than 56 ms - the speed-up came from three fixes found while benchmarking.");
    s.addChart(pres.charts.BAR, [{ name: "Median seconds", labels: ["Statement upload · scanned PDF", "Statement upload · digital PDF", "Cleanup · scanned page", "PAN card upload · cleanup, model, checks",
      "Cleanup · ID phone photo", "Integrity check · ID phone photo", "Integrity check · scanned receipt", "Masking a PAN card"], values: [16.4, 12.7, 8.8, 7.3, 6.8, 3.1, 0.8, 0.7] }], {
      x: M - 0.05, y: 1.8, w: 7.3, h: 5.1, barDir: "bar", chartColors: [INDIGO], showValue: true, dataLabelFormatCode: '0.0" s"', dataLabelFontSize: 10.5, dataLabelColor: INK,
      catAxisLabelColor: BODY, catAxisLabelFontSize: 10.5, catAxisLabelFontFace: F, catAxisOrientation: "maxMin", valAxisHidden: true, valAxisMinVal: 0, valAxisMaxVal: 19,
      valGridLine: { style: "none" }, catGridLine: { style: "none" }, showLegend: false, showTitle: true, title: "Median seconds per document", titleFontSize: 11.5, titleColor: INK, titleFontFace: F, barGapWidthPct: 45,
    });
    const RX = 7.75, RW = W - M - RX;
    T(s, "THREE FIXES FOUND WHILE BENCHMARKING", { x: RX, y: 1.9, w: RW, h: 0.28, fontSize: 10.5, bold: true, color: MUTED, charSpacing: 2 });
    const fx = [["2 s on every model call", "Windows tried IPv6 for 'localhost' first — now 127.0.0.1, pooled."], ["ID photos 19% slower", "A second OCR pass on ID cards — now reuses preprocessing's words."], ["Statement photos ~23 s", "Their rows are now parsed in the background after the upload."]];
    fx.forEach(([t, d], i) => {
      const y = 2.25 + i * 1.08;
      rect(s, RX, y, RW, 0.96, BG, { line: LINE, r: 0.1 });
      T(s, [{ text: t, options: { bold: true, fontSize: 12.5, color: INK, breakLine: true } }, { text: d, options: { fontSize: 10.5, color: MUTED } }], { x: RX + 0.2, y, w: RW - 0.4, h: 0.96, valign: "middle", paraSpaceAfter: 3 });
    });
    rect(s, RX, 5.6, RW, 1.25, INDIGO_T, { line: INDIGO_M, r: 0.1 });
    T(s, [{ text: "Agents did not slow the app. ", options: { bold: true, color: INDIGO_D } },
          { text: "Against the pre-agent build the two model uploads got 9–11% faster; every other endpoint added at most 56 ms.", options: { color: BODY } }],
      { x: RX + 0.2, y: 5.6, w: RW - 0.4, h: 1.25, fontSize: 12, valign: "middle" });
  }

  // ─ Limitations & open decisions
  {
    const s = content("Evaluation", "Known limits and open decisions", null,
      "Reported as they are. Two agents remain weak: the authenticity check, because the local 7B vision model cannot see a local edit even when shown the edited area magnified, and the card outline in phone photos. The tampering detector now finds real forgeries, but it has never seen a forged Indian document, so its tier is advice to a reviewer. Three decisions belong to the business: what a MEDIUM fraud tier should trigger, whether to verify the PAN number with the issuer, and to review the flag rate on the first live applications.");
    const rows = [
      ["Authenticity · Weak 11%", "The local 7B vision model answers 'edited' for genuine and forged areas alike", "Leave local edits to the patch detector", RED],
      ["Card outline · Weak 69%", "A pale card on a pale surface, or a cluttered background, is missed", "Ask for a plain dark background; train on real photos", RED],
      ["Tamper Locator · 74.6%", "A document flagged for another reason (an editor tag) gets no box", "Regions from more signals to reach 80%", AMBER],
      ["PII Masking · 98.4%", "A card whose number can't be located is withheld (about 1 PAN in 11)", "Redact those by hand; add a field detector", AMBER],
      ["No forged Indian documents", "Tampering was tested on real receipts and European ID images", "Collect officers' review verdicts as labels", MUTED],
      ["RBI circulars", "Compliance was tested on 40 ADGM regulations; rbi.org.in blocks scripts", "Download six circulars by hand, re-run", MUTED],
    ];
    T(s, [{ text: "LIMIT", options: {} }], { x: M + 0.2, y: 1.5, w: 3, h: 0.3, fontSize: 10, bold: true, color: MUTED, charSpacing: 2 });
    T(s, "NEXT STEP", { x: M + 5.25, y: 1.5, w: 3, h: 0.3, fontSize: 10, bold: true, color: MUTED, charSpacing: 2 });
    rows.forEach(([t, d, n, c], i) => {
      const y = 1.85 + i * 0.84;
      rect(s, M, y, 8.3, 0.74, BG, { line: LINE, r: 0.08 });
      s.addShape(pres.shapes.OVAL, { x: M + 0.18, y: y + 0.3, w: 0.14, h: 0.14, fill: { color: c }, line: { color: c } });
      T(s, [{ text: t, options: { bold: true, fontSize: 11.5, color: INK, breakLine: true } }, { text: d, options: { fontSize: 9.5, color: MUTED } }], { x: M + 0.45, y, w: 4.7, h: 0.74, valign: "middle" });
      T(s, n, { x: M + 5.25, y, w: 2.95, h: 0.74, fontSize: 10.5, color: INDIGO_D, valign: "middle" });
    });
    rect(s, 9.15, 1.5, 3.58, 5.4, NAVY, { r: 0.12 });
    T(s, "OPEN DECISIONS", { x: 9.4, y: 1.7, w: 3, h: 0.3, fontSize: 10.5, bold: true, color: "A5B4FC", charSpacing: 2 });
    const asks = [["What MEDIUM should mean", "About half of clean test cases get a MEDIUM fraud tier (24% with real scans): a genuine file with an editor tag is flagged."],
      ["Verify the PAN with the issuer", "83% of PAN numbers are read correctly, and a wrong read still looks like a valid PAN."],
      ["Watch the first live flags", "The detector's floors were measured on other kinds of documents than Indian ones."]];
    for (let i = 0; i < asks.length; i++) {
      const [t, d] = asks[i], y = 2.2 + i * 1.55;
      chip(s, String(i + 1), 9.4, y + 0.04, 0.36, 0.36, INDIGO, WHITE, { fs: 11 });
      T(s, [{ text: t, options: { bold: true, fontSize: 13, color: WHITE, breakLine: true } }, { text: d, options: { fontSize: 10, color: "CBD5E1" } }], { x: 9.9, y, w: 2.65, h: 1.45, valign: "top", paraSpaceAfter: 3 });
    }
  }

  // 38 ─ How to run
  {
    const s = content("Getting started", "Run it yourself", "Five steps on one Windows machine. Ports: frontend 5173, API 8000, Ollama 11434.",
      "The database scripts create the FinSight database on SQL Server LocalDB. The API has its own virtual environment with CrewAI. Tests and the evaluation can be run from the api folder; the real-data evaluation needs the public datasets, which are kept outside the repository. The launcher folder has start and stop scripts for the whole stack.");
    const blocks = [
      ["1  Database", ["run docs/mssql/*.sql", "on (localdb)\\MSSQLLocalDB"]],
      ["2  Local model", ["ollama pull qwen2.5vl:7b"]],
      ["3  API  :8000", ["cd api", "python -m venv .venv-agents", ".venv-agents\\Scripts\\pip install -r requirements-agents.txt", "run.bat"]],
      ["4  Frontend  :5173", ["npm install", "npm run dev"]],
      ["5  Tests & evaluation", ["pytest tests", "python -m eval.real.run_real --agent <id>", "python -m eval.real.report_real"]],
    ];
    const pos = [[M, 1.95, 3.95, 1.45], [M + 4.1, 1.95, 3.95, 1.45], [M + 8.2, 1.95, 3.93, 2.95], [M, 3.55, 3.95, 1.35], [M + 4.1, 3.55, 3.95, 1.35]];
    blocks.forEach(([t, lines], i) => {
      const [x, y, w, h] = pos[i];
      rect(s, x, y, w, h, NAVY, { r: 0.1 });
      T(s, t, { x: x + 0.2, y: y + 0.12, w: w - 0.4, h: 0.32, fontSize: 12, bold: true, color: "A5B4FC" });
      T(s, lines.map((l, j) => ({ text: l, options: { breakLine: j < lines.length - 1 } })), { x: x + 0.2, y: y + 0.5, w: w - 0.35, h: h - 0.6, fontSize: 10.5, color: "E2E8F0", fontFace: FM, valign: "top", paraSpaceAfter: 4 });
    });
    T(s, "WHERE THINGS LIVE", { x: M, y: 5.15, w: 4, h: 0.28, fontSize: 10.5, bold: true, color: MUTED, charSpacing: 2 });
    const where = [["api/app/agents/", "registry, agent implementations, flows, tools, llm/gateway.py"], ["api/eval/real/", "real-data harness · targets_real.yaml · results_real/"],
      ["docs/reports/08-…Real-Data-Evaluation.pdf", "the full per-agent report"], ["github.com/ShreyasBile/FinSight", "source, reports and this deck"]];
    where.forEach(([p, d], i) => {
      const x = M + (i % 2) * 6.15, y = 5.5 + Math.floor(i / 2) * 0.68;
      rect(s, x, y, 5.98, 0.58, BG, { line: LINE, r: 0.08 });
      T(s, [{ text: p + "  ", options: { fontFace: FM, bold: true, color: INDIGO_D } }, { text: d, options: { color: MUTED } }], { x: x + 0.15, y, w: 5.7, h: 0.58, fontSize: 10.5, valign: "middle" });
    });
  }

  // 39 ─ Closing
  {
    const s = pres.addSlide(); pageNo++;
    s.background = { color: NAVY };
    logoImg(s, "logo_lockup_light.png", M, 0.9, 0.74);
    T(s, "Agents read, check and draft.\nPeople decide.", { x: M, y: 1.8, w: 9, h: 1.9, fontSize: 44, bold: true, color: WHITE, valign: "top" });
    const st = [["29", "agents in 6 crews"], ["25", "meet their target on real data"], ["13", "agents improved by real-data testing"], ["0", "auto-rejections"]];
    st.forEach(([v, l], i) => {
      const x = M + i * 3.07;
      rect(s, x, 4.3, 2.9, 1.35, NAVY2, { line: NAVY3, r: 0.12 });
      T(s, v, { x: x + 0.25, y: 4.38, w: 2.4, h: 0.7, fontSize: 32, bold: true, color: "A5B4FC" });
      T(s, l, { x: x + 0.25, y: 5.05, w: 2.5, h: 0.5, fontSize: 11.5, color: "CBD5E1", valign: "top" });
    });
    T(s, "Local model by default · every number grounded · every step traced · every decision human", { x: M, y: 6.1, w: CW, h: 0.4, fontSize: 13, color: "94A3B8" });
    T(s, "Thank you  ·  Questions?", { x: M, y: 6.6, w: 6, h: 0.35, fontSize: 13, bold: true, color: WHITE });
    T(s, "github.com/ShreyasBile/FinSight", { x: W - M - 5, y: 6.6, w: 5, h: 0.35, fontSize: 12, color: "A5B4FC", align: "right", fontFace: FM });
    s.addNotes("Close on the core idea: specialised agents take over the reading, checking and drafting; the person keeps every decision, with evidence they can trust.");
  }

  await pres.writeFile({ fileName: OUT });
  console.log("wrote", OUT, pageNo, "slides");
}
build().catch((e) => { console.error(e); process.exit(1); });
