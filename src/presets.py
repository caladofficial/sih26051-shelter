"""Pre-designed, structured shelter presets (curated, not random).

Every preset is a complete flat-schema design whose thermal numbers are
computed by the sourced RC engine on REAL hourly weather of each site
(scripts/build_presets.py) — nothing here is invented. The presets exist
so a user can load a credible starting design for their climate zone and
location in one click, then iterate.

Rationale texts follow standard bioclimatic practice (passive solar,
thermal mass, insulation placement, ventilation); all numbers shown in
the UI come from the engine simulation of the preset at the chosen site.
"""

PRESETS = [
    {
        "id": "prayagraj_brick",
        "name": "Composite Brick Studio",
        "tagline": "Balanced all-rounder for composite plains climates",
        "zones": ["composite"],
        "design": {
            "length_m": 3.0, "width_m": 3.0, "height_m": 2.6,
            "orientation_deg": 0.0,
            "wall_material": "brick", "wall_thickness_m": 0.23,
            "roof_material": "rcc_slab", "roof_thickness_m": 0.15,
            "floor_material": "concrete", "floor_thickness_m": 0.1,
            "insulation_material": "eps", "insulation_thickness_m": 0.05,
            "window_wall": "south", "window_width_m": 1.2,
            "window_height_m": 1.2, "window_sill_m": 0.9,
            "window_shgc": 0.55, "window_u_w_m2k": 2.6,
        },
        "rationale": "230 mm brick mass buffers the day-night swing; 50 mm "
                     "EPS on walls and roof cuts peak solar loads; moderate "
                     "south glazing admits winter sun without overheating. "
                     "A sane default for Prayagraj-class composite climates.",
    },
    {
        "id": "hot_desert_rammed",
        "name": "Desert Thermal-Mass Cell",
        "tagline": "Heavy rammed earth for hot-dry amplitude control",
        "zones": ["hot-dry"],
        "design": {
            "length_m": 3.0, "width_m": 3.0, "height_m": 2.6,
            "orientation_deg": 0.0,
            "wall_material": "rammed_earth", "wall_thickness_m": 0.45,
            "roof_material": "rcc_slab", "roof_thickness_m": 0.2,
            "floor_material": "concrete", "floor_thickness_m": 0.1,
            "insulation_material": "none", "insulation_thickness_m": 0.0,
            "window_wall": "north", "window_width_m": 0.6,
            "window_height_m": 0.9, "window_sill_m": 0.9,
            "window_shgc": 0.35, "window_u_w_m2k": 3.5,
        },
        "rationale": "450 mm rammed earth gives a long thermal lag — peak "
                     "heat arrives indoors at night when it can be vented. "
                     "Small north opening with low SHGC limits direct gain; "
                     "mass does the cooling work in hot-dry deserts.",
    },
    {
        "id": "coastal_light",
        "name": "Coastal Light Envelope",
        "tagline": "Insulated panel shell for warm-humid coasts",
        "zones": ["warm-humid"],
        "design": {
            "length_m": 3.0, "width_m": 3.0, "height_m": 2.6,
            "orientation_deg": 90.0,
            "wall_material": "puf_sandwich_panel", "wall_thickness_m": 0.075,
            "roof_material": "gi_sheet", "roof_thickness_m": 0.002,
            "floor_material": "concrete", "floor_thickness_m": 0.1,
            "insulation_material": "eps", "insulation_thickness_m": 0.025,
            "window_wall": "south", "window_width_m": 1.5,
            "window_height_m": 1.2, "window_sill_m": 0.9,
            "window_shgc": 0.45, "window_u_w_m2k": 2.6,
        },
        "rationale": "In warm-humid climates mass stores heat rather than "
                     "shaving it — the insulated panel shell sheds solar "
                     "gain fast, a low-SHGC window and shaded orientation "
                     "limit radiant load, and the envelope stays light for "
                     "night-time ventilation.",
    },
    {
        "id": "ladakh_vernacular",
        "name": "Ladakh Vernacular (Stone + Mud Roof)",
        "tagline": "Traditional high-altitude masonry, engine-checked",
        "zones": ["cold"],
        "design": {
            "length_m": 3.0, "width_m": 3.0, "height_m": 2.6,
            "orientation_deg": 0.0,
            "wall_material": "stone", "wall_thickness_m": 0.45,
            "roof_material": "mud_brick", "roof_thickness_m": 0.25,
            "floor_material": "concrete", "floor_thickness_m": 0.1,
            "insulation_material": "none", "insulation_thickness_m": 0.0,
            "window_wall": "south", "window_width_m": 0.6,
            "window_height_m": 0.9, "window_sill_m": 0.9,
            "window_shgc": 0.75, "window_u_w_m2k": 5.8,
        },
        "rationale": "The traditional Ladakhi wall recipe — thick stone "
                     "mass with a flat mud roof — couples huge thermal "
                     "storage with slow night-time release. The single "
                     "south window harvests the intense high-altitude "
                     "winter sun. Included so vernacular practice can be "
                     "compared against engineered upgrades on the same "
                     "engine, same weather.",
    },
    {
        "id": "ladakh_high_perf",
        "name": "Ladakh High-Performance Upgrade",
        "tagline": "Sheep-wool + low-e glazing: engineered for -25 °C nights",
        "zones": ["cold"],
        "design": {
            "length_m": 3.0, "width_m": 3.0, "height_m": 2.6,
            "orientation_deg": 0.0,
            "wall_material": "rammed_earth", "wall_thickness_m": 0.45,
            "roof_material": "puf_sandwich_panel", "roof_thickness_m": 0.15,
            "floor_material": "concrete", "floor_thickness_m": 0.1,
            "insulation_material": "sheep_wool", "insulation_thickness_m": 0.15,
            "window_wall": "south", "window_width_m": 1.2,
            "window_height_m": 1.2, "window_sill_m": 0.9,
            "window_shgc": 0.85, "window_u_w_m2k": 1.6,
        },
        "rationale": "150 mm local sheep-wool insulation on the inside of "
                     "heavy rammed earth keeps the mass inside the heated "
                     "volume; the PUF panel roof stops the biggest single "
                     "loss path; south-facing low-e double glazing admits "
                     "maximum solar gain (SHGC 0.85) while U 1.6 limits "
                     "night losses. Designed for Leh/Kargil/Dras winters.",
    },
    {
        "id": "kashmir_valley",
        "name": "Kashmir Valley House",
        "tagline": "Brick + wool batt + double glazing for the valley cold",
        "zones": ["cold"],
        "design": {
            "length_m": 3.0, "width_m": 3.0, "height_m": 2.6,
            "orientation_deg": 0.0,
            "wall_material": "brick", "wall_thickness_m": 0.345,
            "roof_material": "timber", "roof_thickness_m": 0.15,
            "floor_material": "concrete", "floor_thickness_m": 0.1,
            "insulation_material": "mineral_wool", "insulation_thickness_m": 0.1,
            "window_wall": "south", "window_width_m": 0.9,
            "window_height_m": 1.2, "window_sill_m": 0.9,
            "window_shgc": 0.65, "window_u_w_m2k": 2.0,
        },
        "rationale": "Kashmir winters are cold but less extreme than "
                     "Ladakh's: a 345 mm brick wall with 100 mm mineral "
                     "wool, timber roof deck and clear double glazing "
                     "balances cost and comfort for valley towns.",
    },
    {
        "id": "emergency_relief",
        "name": "Rapid Relief Kit",
        "tagline": "Pre-fab sandwich panel — deployable anywhere",
        "zones": ["all"],
        "design": {
            "length_m": 3.0, "width_m": 3.0, "height_m": 2.6,
            "orientation_deg": 0.0,
            "wall_material": "puf_sandwich_panel", "wall_thickness_m": 0.1,
            "roof_material": "puf_sandwich_panel", "roof_thickness_m": 0.1,
            "floor_material": "concrete", "floor_thickness_m": 0.1,
            "insulation_material": "none", "insulation_thickness_m": 0.0,
            "window_wall": "south", "window_width_m": 0.6,
            "window_height_m": 0.6, "window_sill_m": 0.9,
            "window_shgc": 0.55, "window_u_w_m2k": 2.6,
        },
        "rationale": "A factory-made insulated panel box: the core is "
                     "already the insulation (k ~0.023 W/mK), so it is "
                     "fast to erect and performs without site-built layers. "
                     "Small window keeps it robust in transit and on site.",
    },
    {
        "id": "rural_lowcost",
        "name": "Rural Low-Cost Unit",
        "tagline": "Mud brick + timber roof at minimal material cost",
        "zones": ["composite", "hot-dry", "cold"],
        "design": {
            "length_m": 3.0, "width_m": 3.0, "height_m": 2.6,
            "orientation_deg": 0.0,
            "wall_material": "mud_brick", "wall_thickness_m": 0.3,
            "roof_material": "timber", "roof_thickness_m": 0.15,
            "floor_material": "concrete", "floor_thickness_m": 0.1,
            "insulation_material": "none", "insulation_thickness_m": 0.0,
            "window_wall": "south", "window_width_m": 0.6,
            "window_height_m": 0.9, "window_sill_m": 0.9,
            "window_shgc": 0.55, "window_u_w_m2k": 5.8,
        },
        "rationale": "Local mud brick and timber framing keep cost and "
                     "embodied energy low while providing useful thermal "
                     "mass; honest about its limits (single glazing, no "
                     "insulation) — the engine quantifies the trade-off.",
    },
    {
        "id": "temperate_comfort",
        "name": "Temperate Comfort Box",
        "tagline": "Light-touch envelope for mild upland climates",
        "zones": ["temperate"],
        "design": {
            "length_m": 3.0, "width_m": 3.0, "height_m": 2.6,
            "orientation_deg": 0.0,
            "wall_material": "brick", "wall_thickness_m": 0.23,
            "roof_material": "rcc_slab", "roof_thickness_m": 0.15,
            "floor_material": "concrete", "floor_thickness_m": 0.1,
            "insulation_material": "eps", "insulation_thickness_m": 0.025,
            "window_wall": "south", "window_width_m": 1.2,
            "window_height_m": 1.2, "window_sill_m": 0.9,
            "window_shgc": 0.55, "window_u_w_m2k": 2.6,
        },
        "rationale": "Mild climates reward a modest envelope: standard "
                     "brick, thin insulation and a normal window — enough "
                     "to damp extremes without paying for heavy cold-"
                     "climate measures.",
    },
    {
        "id": "high_altitude_aac",
        "name": "AAC Insulated High-Altitude Unit",
        "tagline": "Aerated concrete + XPS for fast cold-climate build",
        "zones": ["cold"],
        "design": {
            "length_m": 3.0, "width_m": 3.0, "height_m": 2.6,
            "orientation_deg": 0.0,
            "wall_material": "aerated_concrete", "wall_thickness_m": 0.3,
            "roof_material": "rcc_slab", "roof_thickness_m": 0.2,
            "floor_material": "concrete", "floor_thickness_m": 0.1,
            "insulation_material": "xps", "insulation_thickness_m": 0.1,
            "window_wall": "south", "window_width_m": 0.9,
            "window_height_m": 1.2, "window_sill_m": 0.9,
            "window_shgc": 0.75, "window_u_w_m2k": 1.6,
        },
        "rationale": "AAC blocks (k ~0.18 W/mK) build fast and insulate "
                     "better than solid masonry; 100 mm XPS takes the "
                     "envelope into cold-climate territory; low-e double "
                     "glazing south-facing captures high-altitude sun.",
    },

    {
        "id": "composite_coolroof",
        "name": "Cool-Roof Composite Studio",
        "tagline": "Low-conductivity walls + roof insulation for hot plains",
        "zones": ["composite"],
        "design": {
            "length_m": 3.0, "width_m": 3.0, "height_m": 2.7,
            "orientation_deg": 0.0,
            "wall_material": "aerated_concrete", "wall_thickness_m": 0.25,
            "roof_material": "rcc_slab", "roof_thickness_m": 0.12,
            "floor_material": "concrete", "floor_thickness_m": 0.1,
            "insulation_material": "eps", "insulation_thickness_m": 0.075,
            "window_wall": "south", "window_width_m": 1.2,
            "window_height_m": 1.2, "window_sill_m": 0.9,
            "window_shgc": 0.5, "window_u_w_m2k": 2.8,
        },
        "rationale": "Roof gains dominate the composite-plains heat load, so "
                     "75 mm EPS sits above the ceiling slab while the 250 mm "
                     "aerated-concrete walls carry their own low conductivity. "
                     "A smaller south window with SHGC 0.5 keeps winter solar "
                     "gain while trimming summer peaks.",
    },
    {
        "id": "composite_monsoon_hybrid",
        "name": "Monsoon Hybrid Studio",
        "tagline": "Brick mass below, insulated panel roof — dry-in fast",
        "zones": ["composite"],
        "design": {
            "length_m": 3.0, "width_m": 3.0, "height_m": 2.7,
            "orientation_deg": 0.0,
            "wall_material": "brick", "wall_thickness_m": 0.23,
            "roof_material": "puf_sandwich_panel", "roof_thickness_m": 0.1,
            "floor_material": "concrete", "floor_thickness_m": 0.1,
            "insulation_material": "xps", "insulation_thickness_m": 0.05,
            "window_wall": "south", "window_width_m": 1.2,
            "window_height_m": 1.2, "window_sill_m": 1.0,
            "window_shgc": 0.45, "window_u_w_m2k": 2.8,
        },
        "rationale": "A wet-season build that closes in quickly: PUF panel "
                     "roof over brick mass walls, with XPS on the walls to "
                     "limit conduction. Higher sill keeps the opening above "
                     "typical local floor-flood levels while preserving light.",
    },
    {
        "id": "cyclone_shell",
        "name": "Cyclone-Resilient Coastal Shell",
        "tagline": "Reinforced concrete + XPS against storm coasts",
        "zones": ["warm-humid"],
        "design": {
            "length_m": 3.0, "width_m": 3.0, "height_m": 2.4,
            "orientation_deg": 0.0,
            "wall_material": "concrete", "wall_thickness_m": 0.15,
            "roof_material": "gi_sheet", "roof_thickness_m": 0.002,
            "floor_material": "concrete", "floor_thickness_m": 0.12,
            "insulation_material": "xps", "insulation_thickness_m": 0.05,
            "window_wall": "south", "window_width_m": 0.6,
            "window_height_m": 0.9, "window_sill_m": 1.2,
            "window_shgc": 0.4, "window_u_w_m2k": 3.5,
            "ach": 5,
        },
        "rationale": "Low silhouette with a small, high, storm-shuttered "
                     "opening and a light GI roof that a real build ties down "
                     "to a reinforced ring beam. The heavy RCC-plus-XPS walls "
                     "and 5 ACH night ventilation shed humid-day heat without "
                     "relying on glass.",
    },
    {
        "id": "breezeway_panel",
        "name": "Breezeway Panel Studio",
        "tagline": "Light insulated shell that cools by night air",
        "zones": ["warm-humid"],
        "design": {
            "length_m": 3.0, "width_m": 3.0, "height_m": 2.8,
            "orientation_deg": 0.0,
            "wall_material": "puf_sandwich_panel", "wall_thickness_m": 0.075,
            "roof_material": "gi_sheet", "roof_thickness_m": 0.002,
            "floor_material": "concrete", "floor_thickness_m": 0.1,
            "insulation_material": "none", "insulation_thickness_m": 0.0,
            "window_wall": "south", "window_width_m": 1.5,
            "window_height_m": 1.2, "window_sill_m": 1.0,
            "window_shgc": 0.35, "window_u_w_m2k": 3.5,
            "ach": 8,
        },
        "rationale": "For humid coasts the night sky is the heat sink: the "
                     "shell stays light so it sheds daytime gains fast, "
                     "operable openings give 8 ACH cross-ventilation, and "
                     "low-SHGC glass plus a reflective metal roof keep "
                     "daytime solar gain down.",
    },
    {
        "id": "passive_solar_temperate",
        "name": "Passive-Solar Sunspace Studio",
        "tagline": "Big south glass with thermal mass for mild winters",
        "zones": ["temperate"],
        "design": {
            "length_m": 3.0, "width_m": 3.0, "height_m": 2.6,
            "orientation_deg": 0.0,
            "wall_material": "brick", "wall_thickness_m": 0.23,
            "roof_material": "timber", "roof_thickness_m": 0.15,
            "floor_material": "concrete", "floor_thickness_m": 0.12,
            "insulation_material": "eps", "insulation_thickness_m": 0.05,
            "window_wall": "south", "window_width_m": 1.8,
            "window_height_m": 1.5, "window_sill_m": 0.6,
            "window_shgc": 0.65, "window_u_w_m2k": 2.6,
        },
        "rationale": "Temperate winters are mild but real: 2.7 m² of south "
                     "glass admits low-angle winter sun onto the exposed "
                     "concrete floor slab, which stores it for the night. "
                     "EPS on walls and a 150 mm timber roof cut the envelope "
                     "losses, and the overhang geometry suits upland sun "
                     "angles.",
    },
    {
        "id": "desert_nightpurge",
        "name": "Desert Night-Purge Cell",
        "tagline": "Rammed earth lag plus deliberate night ventilation",
        "zones": ["hot-dry"],
        "design": {
            "length_m": 3.0, "width_m": 3.0, "height_m": 2.7,
            "orientation_deg": 0.0,
            "wall_material": "rammed_earth", "wall_thickness_m": 0.35,
            "roof_material": "rcc_slab", "roof_thickness_m": 0.18,
            "floor_material": "concrete", "floor_thickness_m": 0.1,
            "insulation_material": "xps", "insulation_thickness_m": 0.05,
            "window_wall": "north", "window_width_m": 0.6,
            "window_height_m": 0.9, "window_sill_m": 0.9,
            "window_shgc": 0.35, "window_u_w_m2k": 3.5,
            "ach": 6,
        },
        "rationale": "Where nights drop 15 K below days, mass alone works "
                     "only if the stored heat can leave: this cell couples "
                     "350 mm rammed-earth walls with 6 ACH purge openings "
                     "for after-sunset ventilation and XPS under the roof "
                     "slab to kill the daytime top gain.",
    },
    {
        "id": "superinsulated_cold",
        "name": "Super-Insulated Cold Cell",
        "tagline": "150 mm XPS + solar glass for deep-winter plateaus",
        "zones": ["cold"],
        "design": {
            "length_m": 3.0, "width_m": 3.0, "height_m": 2.6,
            "orientation_deg": 0.0,
            "wall_material": "aerated_concrete", "wall_thickness_m": 0.3,
            "roof_material": "rcc_slab", "roof_thickness_m": 0.15,
            "floor_material": "concrete", "floor_thickness_m": 0.12,
            "insulation_material": "xps", "insulation_thickness_m": 0.15,
            "window_wall": "south", "window_width_m": 1.2,
            "window_height_m": 1.2, "window_sill_m": 0.9,
            "window_shgc": 0.62, "window_u_w_m2k": 1.1,
        },
        "rationale": "For -25 °C plateau nights the envelope is everything: "
                     "150 mm XPS continuous insulation, low-conductivity AAC "
                     "walls, and the best glazing in the catalogue (U 1.1) "
                     "kept on the south side with SHGC 0.62 so the winter "
                     "sun still pays in.",
    },
    {
        "id": "insulated_relief_cell",
        "name": "Insulated Rapid-Relief Cell",
        "tagline": "Deployable panel shelter with winter-rated insulation",
        "zones": ["all"],
        "design": {
            "length_m": 3.0, "width_m": 3.0, "height_m": 2.6,
            "orientation_deg": 0.0,
            "wall_material": "puf_sandwich_panel", "wall_thickness_m": 0.1,
            "roof_material": "puf_sandwich_panel", "roof_thickness_m": 0.1,
            "floor_material": "plywood", "floor_thickness_m": 0.05,
            "insulation_material": "mineral_wool", "insulation_thickness_m": 0.05,
            "window_wall": "south", "window_width_m": 0.6,
            "window_height_m": 0.9, "window_sill_m": 0.9,
            "window_shgc": 0.55, "window_u_w_m2k": 2.6,
            "ach": 2,
        },
        "rationale": "The relief kit's sibling for cold nights and hot "
                     "afternoons alike: the same fast flat-pack panel "
                     "envelope, plus 50 mm mineral-wool liner where the "
                     "plain kit has none — one deployable design, wider "
                     "climate reach.",
    },
]
