# Plain-English Design Assistant (SEC/11) — model card

The "Describe It" assistant turns a sentence like *"a cool mud-brick shelter
for a family of six in Jaipur, small north windows"* into a shelter design,
then **runs the real RC simulation** on the result. The language model only
proposes; every number on screen is engine output.

## What it is

| Piece | Where | Notes |
|---|---|---|
| Intent classifier | `src/nlp_design.py` + `src/data/nlp_model.json` | hashed word+char n-grams → multinomial logistic regression, pure-NumPy inference (no sklearn at runtime, no external LLM API — keeps the free stack and the offline edition working) |
| Slot extractor | `src/nlp_design.py::extract_slots` | gazetteer + regex, deliberately rule-based so it can never invent materials |
| API | `POST /api/nlp/design`, `GET /api/nlp/info`, `POST /api/nlp/feedback` | `src/api_app.py` |
| Training | `ml/nlp/generate_nlp_dataset.py` → `train_nlp.py` | 30,000 synthetic utterances |
| Evaluation | `ml/nlp/eval_nlp.py` | hand-written held-out set, never trained on: **73/73 intent · 44/44 slots** |

Intents: `design` · `modify` · `optimize` · `explain` · `compare` · `unknown`.
Slots cover: site (city aliases incl. Bombay→Mumbai), wall/roof/insulation
materials, thicknesses (mm/cm/m), dimensions (digits and spelled-out numbers),
occupants (→ floor area), orientation, window wall/size/negation, goals
(cooling/heating/low-cost/rapid), ventilation (→ `ach`), roof pitch
(→ `roof_pitch_deg`), hazards (cyclone/flood/monsoon/snow → engine-verified
presets), and relative changes ("make it bigger").

``ach`` and ``roof_pitch_deg`` are first-class engine inputs and are exposed
everywhere: the SEC/03 form, the optimizer search space
(`src/optimization/optuna_optimizer.py`), the surrogate AI features, and the
NLP slots.

## Multi-turn conversations

`POST /api/nlp/design` accepts `context_design` (the previous turn's design).
A follow-up like *"thicker walls"* refines what is on screen instead of
restarting from the zone prescription. Continuation is conservative:

* relative changes always continue,
* `modify` continues only when the sentence names **no new site** — *"now
  design a tin shed for Chennai"* is a fresh brief and must not inherit Leh's
  stone walls.

## The feedback loop — real user phrasings

Synthetic templates can only take the parser so far; the scarce resource is
how **actual users phrase requests**. Under every answer the site asks
*"Did I understand you correctly?"* and stores the verdict:

```
public-src/app.js  →  POST /api/nlp/feedback  →  nlp_feedback table
                                                  (supabase migration 0007)
```

Each row keeps the original sentence, the classifier's read, the slots, the
produced design, the user's verdict, and — when the user says "not quite" —
their correction in their own words. The table has **no public-read policy**:
user sentences are not public data.

Export + score:

```bash
SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python3 ml/nlp/import_feedback.py
python3 ml/nlp/eval_nlp.py --extra ml/nlp/data/real_user_feedback.csv
```

The export CSV is gitignored (`ml/nlp/data/real_user_feedback.csv`). The eval
reports the **user-confirmed rate** (the honest real-world accuracy) and an
**intent re-check** that flags drift after a retrain, when the current model
starts reading confirmed sentences differently.

## Honesty rules (project constraints)

* Lists honest held-out scores, never the template-split accuracy alone.
* Unknown cities, unsupported materials, budgets: reported as *not honoured*,
  never silently substituted. Seismic design is out of scope — the RC engine
  models heat, not structure.
* No external LLM calls; no secrets; the offline edition embeds the same
  model JSON and parses identically.

## v4 — the audit corpus (2026-09-15)

The project owner supplied an 86-utterance hand-annotated corpus
(`ml/nlp/data/nlp_gold_v2.jsonl`: tactical/disaster/conversational/
optimisation/comparative/physics/Hinglish/adversarial). Audited against it,
the baseline read **66.3%** intent accuracy — the "99.97%" synthetic split
was exactly the inflation the audit described. The fix, in three layers:

1. **Grammar engine** (`src/nlp_design.py::grammar`) — deterministic rules
   for constructs a bag-of-ngrams cannot see: compare-constructs ("compare X
   with Y", "which is better: A or B", "X versus Y"), prompt-injection and
   out-of-domain guards, optimise-verb-anywhere, imperative refinement
   chains. A rule fires only when its construct is *present*; the model
   still reads everything else. `understood.read_by` records which rule
   steered a sentence ("via grammar:compare-construct" shows in the UI).
2. **Hinglish bridge** — romanised-Hindi transliteration (`_hinglish`)
   feeding the SAME gazetteers ("mitti"→mud, "deewar"→wall, "ghumao"→rotate),
   with `se bachne` reversing the naive cold→cooling heuristic. The
   classifier featurises RAW text (train/inference contract unchanged).
3. **Trained on the shapes, benchmarked on the gold** — generator v4 adds
   Hinglish, comparative-grammar, imperative-chain and adversarial families
   (36k utterances); the 86 gold rows are NEVER trained on.
   `python3 ml/nlp/train_nlp.py` exports `metrics.gold_holdout` — currently
   **86/86 = 1.000** through the production path — served by
   `/api/nlp/info` beside the (inflated) split number, labelled as such.

Slot extraction closed the audit's real misses: hyphenated aliases
("mud-brick"), "8-soldier"/"2 personnel"/"15 displaced people", adjective-
before-noun windows ("small north windows", "south glazing"), trailing ACH
("drop ACH to 1"), "3 meter ceiling", insulation-REMOVAL, plural hazards
("blizzards"), the "RCC slab roof" comma-loss bug, and per-word thickness
attribution ("75mm mineral wool" is an insulation number).

**Comparisons are now actionable** (the audit's "severe false positive"
became a feature): `/api/nlp/design` answers a resolvable pair by running
the real engine on both sides (`mode: "material"`), comparing engine-verified
preset caches at the selected site (`mode: "preset"`), or quoting archived
multi-year normals for two sites (`mode: "climate"`). Every number comes from
`rc_model.simulate`, the preset cache, or the climate archive — never an
estimate. Tests: `tests/test_nlp_grammar.py`, `test_nlp_gold_corpus.py`,
`test_nlp_compare_endpoint.py`; browser: `scripts/e2e_nlp_v4.js`.
