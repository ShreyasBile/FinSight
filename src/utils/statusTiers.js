import { Icons } from "../icons/Icons.jsx";

// Canonical severity tiers shared by every status indicator in the document
// review workflow (tamper check, face match, per-field OCR flags). Before
// this existed, each caller defined its own {color, icon} pair for
// green/amber/red/neutral and they silently drifted from each other. Layout
// (background, padding, text copy) stays local to each component — only the
// color + icon meaning of a tier is centralized here.
export const TIER = {
  good: { color: "var(--green)", icon: Icons.check },
  warn: { color: "var(--amber)", icon: Icons.alert },
  bad: { color: "var(--red)", icon: Icons.alert },
  info: { color: "var(--text3)", icon: Icons.info },
};
