/* Node runner for the offline-NLP parity gate — invoked by
 * scripts/check_nlp_edge_parity.py, never shipped to browsers.
 * stdin : {"model": <bundle.nlp block>, "cases": ["text", ...]}
 * stdout: [{"i":n,"intent":"...","slots":{...}}, ...]
 */
"use strict";
const path = require("path");
const ENG = require(path.join(__dirname, "engine.js"));

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => { raw += c; });
process.stdin.on("end", () => {
  const input = JSON.parse(raw);
  ENG.nlp.init(input.model);
  const out = input.cases.map((t, i) => {
    const r = ENG.nlp.parse(t);
    return { i, intent: r.intent, confidence: r.confidence,
             cintent: ENG.nlp.classify(t).intent,
             slots: r.slots, norm: ENG.nlp.normalise(t) };
  });
  process.stdout.write(JSON.stringify(out));
});
