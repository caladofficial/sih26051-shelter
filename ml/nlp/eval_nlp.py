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

from src.nlp_design import classify, extract_slots       # noqa: E402

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


def main() -> int:
    ok = 0
    print("=== INTENT (hand-written, never trained on) ===")
    wrong = []
    for text, want in HELD_OUT:
        got, conf, _ = classify(text)
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
    return 0 if (acc >= 0.80 and slot_ok / slot_total >= 0.85) else 1


if __name__ == "__main__":
    raise SystemExit(main())
