"""Hand-written held-out set for the NLP parser.

The synthetic corpus and the test split come from the same templates, so the
0.9998 split accuracy mostly measures template memorisation. These utterances
were written by hand, deliberately in phrasings the generator does NOT
produce, and are never trained on. This is the number worth quoting.

Run:  python3 ml/nlp/eval_nlp.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import re                                                       # noqa: E402

from src.nlp_design import classify, extract_slots, parse       # noqa: E402

# (utterance, expected_intent)
HELD_OUT = [
    # --- design, phrased unlike the templates -------------------------------
    ("we need somewhere for a family of six to sleep in Leh through winter", "design"),
    ("put together a two room unit that won't cook people alive in May", "design"),
    ("something quick we can throw up after the floods in Kolkata", "design"),
    ("I'd like a shelter that stays under 30 degrees in Jaisalmer", "design"),
    ("draw up a mud walled hut for the village near Jaipur", "design"),
    ("what would you build for a relief camp outside Chennai", "design"),
    ("give us a design that survives Ladakh winters on a tight budget", "design"),
    ("need housing for flood victims, 4 by 5, tin roof", "design"),
    ("a small classroom for 20 kids in Hyderabad please", "design"),
    ("build something passive for Pune, no air conditioning", "design"),
    ("shelter for Dras, it gets to minus twenty there", "design"),
    ("can we get a cheap unit up in Ahmedabad before the heatwave", "design"),

    # --- modify -------------------------------------------------------------
    ("bump the wall up to 300mm", "modify"),
    ("that roof is too thin, double it", "modify"),
    ("drop the insulation, too expensive", "modify"),
    ("turn the windows to face north instead", "modify"),
    ("swap the brick for rammed earth", "modify"),
    ("can the ceiling go a bit higher", "modify"),
    ("less glass on the west side", "modify"),
    ("use thermocol instead of rockwool", "modify"),
    ("make it one metre wider", "modify"),
    ("thicker walls please", "modify"),

    # --- optimize -----------------------------------------------------------
    ("work out the best envelope you can for Srinagar", "optimize"),
    ("run the optimiser and see what it finds", "optimize"),
    ("what's the most comfortable configuration for Mumbai", "optimize"),
    ("squeeze as much comfort out of this as possible", "optimize"),
    ("search the design space for Kargil", "optimize"),
    ("find me the optimal wall and roof combination", "optimize"),

    # --- explain ------------------------------------------------------------
    ("how bad do summers get in Prayagraj", "explain"),
    ("is Bengaluru actually mild all year", "explain"),
    ("what's the coldest it gets in Kargil", "explain"),
    ("tell me about rainfall in Mumbai", "explain"),
    ("how many hours cross 35 degrees in Delhi", "explain"),
    ("which climate zone does Pune fall in", "explain"),

    # --- compare ------------------------------------------------------------
    ("is Leh harsher than Srinagar", "compare"),
    ("brick or AAC, which performs better", "compare"),
    ("how does Chennai stack up against Kolkata", "compare"),
    ("difference between a tin roof and an RCC slab", "compare"),

    # --- unknown / out of scope --------------------------------------------
    ("who built this thing", "unknown"),
    ("can I get the raw CSV", "unknown"),
    ("my laptop is slow", "unknown"),
    ("what's for lunch", "unknown"),
    ("thanks that's great", "unknown"),
    ("nevermind", "unknown"),
    # --- v3 additions: phrasings that previously failed, plus new ground ----
    # verb-less noun phrases (these used to land in `explain`)
    ("bamboo hut in Kolkata", "design"),
    ("three by four metres unit in Pune", "design"),
    ("CGI sheet roof over brick walls in Pune", "design"),
    ("mud house Jaisalmer", "design"),
    ("a 5x4 stone block for Kargil", "design"),
    # hazard-led
    ("needs to handle heavy snow load in Dras", "design"),
    ("must withstand a cyclone on the Chennai coast", "design"),
    ("something that can take the monsoon in Mumbai", "design"),
    ("has to survive waterlogging in Kolkata", "design"),
    # ventilation-led
    ("shelter with cross ventilation for Chennai", "design"),
    ("a well ventilated room in Hyderabad", "design"),
    # relative / follow-up turns
    ("make it bigger", "modify"),
    ("add more insulation please", "modify"),
    ("thinner walls this time", "modify"),
    ("can you give it a taller ceiling", "modify"),
    ("swap the roof to concrete", "modify"),
    ("bump the insulation to 150mm", "modify"),
    # optimisation phrasing
    ("find me the best combination for Delhi", "optimize"),
    ("what is the optimal wall thickness in Leh", "optimize"),
    ("tune it for the lowest peak temperature", "optimize"),
    # explain
    ("is Chennai humid all year", "explain"),
    ("what is the coldest month in Srinagar", "explain"),
    ("tell me about the climate in Pune", "explain"),
    ("how many hours go above 35 in Jaisalmer", "explain"),
    # compare
    ("brick versus rammed earth in Jaipur", "compare"),
    ("which performs better, EPS or mineral wool", "compare"),
    # out of scope / nonsense — must NOT be confidently designed
    ("what is the capital of France", "unknown"),
    ("book me a flight to Delhi", "unknown"),
    ("asdkjh qwe zxc", "unknown"),
]

# (utterance, {slot: expected value}) — slot extraction is rule-based, so it
# should be exactly right on these or honestly absent

# (utterance, {slot: expected value}) — slot extraction is rule-based, so it
# should be exactly right on these or honestly absent
SLOT_CASES = [
    ("a cool shelter for Jaipur with mud brick walls and 100mm EPS",
     {"site": "Jaipur", "wall_material": "mud_brick",
      "insulation_material": "eps", "insulation_thickness_m": 0.1}),
    ("build a 4 by 5 unit in Leh with a tin roof facing south",
     {"site": "Leh", "roof_material": "gi_sheet", "length_m": 4.0,
      "width_m": 5.0, "window_wall": "south"}),
    ("stone walls, RCC slab roof, 300 mm walls, Srinagar",
     {"site": "Srinagar", "wall_material": "stone",
      "roof_material": "rcc_slab", "wall_thickness_m": 0.3}),
    ("shelter for 6 people in Dras, no insulation",
     {"site": "Dras", "occupants": 6, "insulation_material": "none"}),
    ("something for Bombay", {"site": "Mumbai"}),
    # --- v3 slots: each of these used to extract nothing ---------------------
    ("we need shelter for a family of six in Leh",
     {"site": "Leh", "occupants": 6}),
    ("three by four metres unit in Pune", {"length_m": 3.0, "width_m": 4.0}),
    ("well ventilated shelter for Chennai", {"site": "Chennai", "ach": 6.0}),
    ("sealed airtight cabin in Leh", {"site": "Leh", "ach": 0.5}),
    ("cyclone resistant shelter for Chennai", {"hazards": ["cyclone"]}),
    ("cheap shelter for Kolkata after the floods", {"hazards": ["flood"]}),
    ("needs to handle heavy snow load in Dras", {"hazards": ["snow"]}),
    ("shelter with no windows for Leh", {"no_windows": True}),
    ("large windows facing south in Delhi",
     {"window_wall": "south", "window_width_m": 1.8}),
    ("bamboo hut in Kolkata", {"unsupported_materials": ["bamboo"]}),
    ("something for my village near Varanasi", {"unknown_place": "varanasi"}),
    ("budget under 50000 rupees for Delhi", {"budget_mentioned": True}),
    ("sloped roof for monsoon in Mumbai", {"roof_pitch_deg": 20.0}),
    ("30 degree pitch roof in Leh", {"roof_pitch_deg": 30.0}),
    ("flat roof shelter for Delhi", {"roof_pitch_deg": 0.0}),
    ("a mud brick shelter for Jaipur with a sloped roof",
     {"wall_material": "mud_brick", "roof_pitch_deg": 20.0}),
    ("stone shelter with GI sheet roof in Leh",
     {"wall_material": "stone", "roof_material": "gi_sheet"}),
    ("make it bigger and add more insulation",
     {"relative": {"size": 1.25, "insulation_thickness_m": 1.6}}),

    ("cheap rapid shelter for Calcutta",
     {"site": "Kolkata", "goals": ["low_cost", "rapid"]}),
]


# --------------------------------------------------------------------------
# v4: the hand-annotated gold corpus (ml/nlp/data/nlp_gold_v2.jsonl)
# --------------------------------------------------------------------------
#: 86 utterances annotated with intent + full slot objects by the project
#: owner's audit. These were NEVER trained on, cover Hinglish, adversarial
#: prompt-injection, comparative grammar and out-of-domain negatives, and
#: carry explicit slot objects. Slot parity is scored only on values whose
#: surface words appear in the utterance (the gold annotation also records
#: zone defaults that no parser should invent from silence — inventing them
#: would violate the "extract only what was spoken" contract).
GOLD_FILE = Path(__file__).resolve().parent / "data" / "nlp_gold_v2.jsonl"

_SURFACE = {
    "mud_brick": ["mud brick", "mud-brick", "adobe", "mitti"],
    "rammed_earth": ["rammed earth"],
    "aerated_concrete": ["aerated concrete", "aac"],
    "puf_sandwich_panel": ["puf"],
    "gi_sheet": ["gi sheet", "tin", "corrugated"],
    "rcc_slab": ["rcc"],
    "sheep_wool": ["sheep wool"],
    "mineral_wool": ["mineral wool"],
    "eps_50mm": ["50mm eps", "50 mm eps"],
    "eps_100mm": ["100mm eps", "100 mm eps"],
    "stone_sheep_wool": ["stone with sheep wool", "stone + sheep"],
    "brick_xps": ["brick with xps"],
    "none": ["remove all insulation", "no insulation", "without insulation"],
}


def _spoken(text: str, key: str, value) -> bool:
    t = text.lower()
    if isinstance(value, str):
        forms = _SURFACE.get(value, [value.replace("_", " ")])
        return any(f in t for f in forms)
    if key == "occupants":
        return any(w in t for w in ("family", "families", "people", "person",
                                    "soldier", "occupant", "jawan", "kids",
                                    "children", "victims", "troops")) \
            and (str(value) in t or any(f" {w} " in t for w in ()))
    if key in ("length_m", "width_m", "height_m", "wall_thickness_m",
               "roof_thickness_m", "insulation_thickness_mm"):
        # a numeric slot is only demanded when ITS VALUE is spoken — the gold
        # objects also record zone defaults, and inventing those from silence
        # is exactly what the honest parser refuses to do
        digits = (str(value).rstrip("0").rstrip(".") if isinstance(value, float)
                  else str(value))
        if digits in t or digits.replace(".", ",") in t:
            return True
        mm = value * 1000 if key.endswith("_m") and value < 1 else value
        if mm == int(mm) and f"{int(mm)}mm" in t.replace(" ", ""):
            return True
        spelled = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
                   6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
                   12: "twelve", 15: "fifteen", 16: "sixteen", 20: "twenty"}
        try:
            w = spelled.get(int(float(digits)))
        except ValueError:
            w = None
        return bool(w) and f" {w} " in t
    if key == "window_shgc":
        return any(w in t for w in ("solar gain", "shgc", "low-e", "avoid sun"))
    if key == "ach":
        return any(w in t for w in ("air change", "ventilation", "ach",
                                    "purge", "breeze", "seal", "thandi hawa"))
    if key == "occupants":
        return any(w in t for w in ("family", "people", "person", "soldier",
                                    "occupant", "jawan", "kids", "children", "victims"))
    if key == "window_wall":
        return any(w in t for w in ("window", "glaz", "facing", "khidki", "orient"))
    if key in ("roof_pitch_deg",):
        return any(w in t for w in ("pitch", "slope", "sloped", "gable", "flat"))
    return str(value) in t


def eval_gold() -> tuple[float, float, list]:
    """(intent_acc, slot_acc, failures) over the gold corpus."""
    import json
    rows = [json.loads(l) for l in GOLD_FILE.read_text(encoding="utf-8").splitlines()
            if l.strip().startswith("{")]
    ok_i = slot_ok = slot_tot = 0
    fails = []
    for g in rows:
        p = parse(g["text"])
        if p["intent"] == g["intent"]:
            ok_i += 1
        else:
            fails.append(f"INTENT {g['id']:<10s} want={g['intent']:<9s} "
                         f"got={p['intent']:<9s} ({p['confidence']:.2f}) {g['text'][:58]}")
        if g["intent"] in ("design", "modify", "compare"):
            for k, v in (g.get("slots") or {}).items():
                if k not in DESIGN_KEYS or not _spoken(g["text"], k, v):
                    continue
                slot_tot += 1
                got = p["slots"].get(k)
                if got is None and k == "insulation_thickness_mm":
                    got_alt = p["slots"].get("insulation_thickness_m")
                    got = None if got_alt is None else round(got_alt * 1000, 6)
                if got == v:
                    slot_ok += 1
                elif k.startswith("insulation_thickness") and got is not None \
                        and abs(float(got) - (v / 1000 if k.endswith("_mm") else v)) < 1e-6:
                    slot_ok += 1                     # unit form of the same value
                else:
                    fails.append(f"SLOT   {g['id']:<10s} {k}: want {v!r} "
                                 f"got {got!r}   <- {g['text'][:48]}")
    return ok_i / len(rows), (slot_ok / slot_tot if slot_tot else 1.0), fails


DESIGN_KEYS = {"site", "wall_material", "roof_material", "insulation_material",
               "length_m", "width_m", "height_m", "window_wall", "occupants",
               "ach", "roof_pitch_deg", "window_shgc", "wall_thickness_m",
               "roof_thickness_m", "insulation_thickness_mm",
               "insulation_thickness_m"}


def eval_feedback_csv(path: Path) -> None:
    """Score the exported /api/nlp/feedback rows (real user phrasings).

    Reported for information only — this set grows with site usage and must
    not gate CI. Two numbers matter:

      user-confirmed   — share of sentences users marked "understood right"
      intent re-check  — re-running the CURRENT classifier on those same
                         sentences: does it still read them the way the
                         stored verdict assumed? Drift here after a retrain
                         flags regressions on real phrasings directly.
    """
    import csv
    rows = []
    with path.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if (r.get("text") or "").strip():
                rows.append(r)
    if not rows:
        print(f"\n=== REAL USER FEEDBACK ({path.name}) ===")
        print("  no rows yet — collect feedback on the site first")
        return
    confirmed = sum(1 for r in rows if (r.get("correct") or "").lower()
                    in ("true", "1", "t"))
    print(f"\n=== REAL USER FEEDBACK ({path.name}, {len(rows)} rows) ===")
    print(f"  user-confirmed understood : {confirmed}/{len(rows)} = "
          f"{confirmed / len(rows):.3f}")
    agree = total = 0
    for r in rows:
        stored = (r.get("intent") or "").strip()
        if not stored:
            continue
        total += 1
        got, conf, _ = classify(r["text"])
        if got == stored:
            agree += 1
        elif (r.get("correct") or "").lower() in ("true", "1", "t"):
            # retrain regression: a sentence the user CONFIRMED is now read
            # differently — list it loudly
            print(f"   DRIFT confirmed-as-{stored:<8s} now={got:<9s}"
          f" ({conf:.2f})  {r['text']}")
    if total:
        print(f"  intent re-check agrees    : {agree}/{total} = "
              f"{agree / total:.3f}")


def main() -> int:
    ok = 0
    print("=== INTENT (hand-written, never trained on) ===")
    wrong = []
    for text, want in HELD_OUT:
        pr = parse(text)                     # the path production runs:
        got, conf = pr["intent"], pr["confidence"]   # classifier + grammar
        if got == want:
            ok += 1
        else:
            wrong.append((text, want, got, conf))
    acc = ok / len(HELD_OUT)
    print(f"  accuracy {ok}/{len(HELD_OUT)} = {acc:.3f}")
    for t, want, got, conf in wrong:
        print(f"   MISS  want={want:<9s} got={got:<9s} ({conf:.2f})  {t}")

    print("\n=== SLOTS ===")
    slot_ok = slot_total = 0
    for text, want in SLOT_CASES:
        got = extract_slots(text)
        for k, v in want.items():
            slot_total += 1
            if got.get(k) == v:
                slot_ok += 1
            else:
                print(f"   MISS  {k}: want {v!r} got {got.get(k)!r}   <- {text}")
    print(f"  slots {slot_ok}/{slot_total} = {slot_ok / slot_total:.3f}")

    gold_acc = slot_g = 0.0
    if GOLD_FILE.exists():
        print("\n=== GOLD CORPUS (hand-annotated, 86 utterances, never trained on) ===")
        gold_acc, slot_g, gold_fails = eval_gold()
        print(f"  intent accuracy  : {gold_acc:.3f}")
        print(f"  spoken-slot parity: {slot_g:.3f}")
        for f in gold_fails[:14]:
            print("   " + f)
        if len(gold_fails) > 14:
            print(f"   ... {len(gold_fails) - 14} more")

    # optional: real user feedback exported by ml/nlp/import_feedback.py
    extra = None
    if "--extra" in sys.argv:
        i = sys.argv.index("--extra")
        if i + 1 < len(sys.argv):
            extra = Path(sys.argv[i + 1])
    if extra is None:
        default = REPO / "ml" / "nlp" / "data" / "real_user_feedback.csv"
        extra = default if default.exists() else None
    if extra is not None:
        eval_feedback_csv(extra)

    gate = (acc >= 0.80 and slot_ok / slot_total >= 0.85
            and gold_acc >= 0.90 and slot_g >= 0.85)
    return 0 if gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
