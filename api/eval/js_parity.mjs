// Runs the ORIGINAL browser logic (src/utils/docVerification.js) over a corpus
// so the Python port (api/app/docrules.py) can be compared answer-for-answer.
//   node eval/js_parity.mjs corpus.json > js_results.json
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
const mod = await import(pathToFileURL(path.resolve(here, "../../src/utils/docVerification.js")).href);
const corpus = JSON.parse(readFileSync(process.argv[2], "utf-8"));

const out = {
  validate: corpus.validate.map(([k, v]) => mod.validateField(k, v)),
  identity: corpus.identity.map(([d, p]) => mod.checkDocumentIdentity(d, p).ok),
  names: corpus.names.map(([a, b]) => mod.compareNames(a, b).level),
  cross: corpus.cross.map((m) => mod.crossVerifyDocuments(m)),
  dates: corpus.dates.map((s) => mod.parseDateAny(s)),
};
process.stdout.write(JSON.stringify(out));
