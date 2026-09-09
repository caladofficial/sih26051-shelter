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
