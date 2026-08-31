# SIH26051 — Problem Statement (verified)

**PS Number:** SIH26051
**Organization:** DRDO — Department of Defence Production / iDEX
**Category:** Software
**Title:** Software Based Model Development for Design of Area Specific Shelter for Thermal Comfort Maintenance

> Source: SIH 2026 problem-statements snapshot (problem_statements.csv / README_DETAILED.md of
> `NoBugNinja/Smart-India-Hackathon-SIH-2026-Problem-Statements`, downloaded 2026-08-31).
> Always re-verify against the official SIH portal before submission.

## Background (from the statement)

- Ambient atmospheric conditions affect the temperature inside shelters, making thermal
  management necessary to keep temperature in a comfortable range.
- Existing shelters are generally not designed for a particular region, are not energy
  efficient, and thus demand external thermal-comfort maintenance systems.
- The project is conceptualised for the **tough climatic condition of high-altitude cold
  regions such as Ladakh**, and can be used to study design requirements of other climatic
  regions as well.
- Ladakh: high solar irradiance (1900–2100 kWh/m²/year), ~7.9 h average sunshine, 300+
  cloud-free days per year. Shelters stay comfortable during the day by trapping solar
  thermal energy, but approach ambient temperature after sunset because of high thermal
  losses through the shelter material and openings.

## Description (from the statement)

Detailed thermal analysis of the shelter — including **size, shape, orientation**, study of
**suitable materials**, **thermal mass storage material**, **composite multi-material**
options, and the **effect of openings** — is a potential solution for a self-sufficient
passive shelter for the region.

The statement mentions a general model in ANSYS as a conceptual example. The expected
outcome is a **user-friendly software model** that works on user-defined values (real-time
data, material properties) to simulate cases and compare different materials under the same
ambient conditions, predicting the most efficient combination of materials, shape and size
for temperature maintenance.

## Expected Solution (from the statement)

Development of a software-based model for predicting suitable shelter design (material,
size, shape) with the objective of thermal-comfort maintenance in passive shelters installed
in different atmospheric conditions. Feeding collected data and material properties into the
model outputs the most efficient design with materials for thermal comfort in a particular
region. The model must solve:

1. **Prediction of shelter inside temperature** based on user-defined inputs.
2. **Prediction of thermal energy generated from solar radiation.**
3. **Heat flow details** as per the temperature difference between ambient and shelter
   temperature for a defined time period.

## What this repo implements (mapping to the statement)

| Statement requirement | Implementation |
|---|---|
| Inside-temperature prediction from user inputs | RC lumped model + EnergyPlus (IDF auto-generated from config) |
| Solar thermal energy prediction | pvlib sun position + per-surface POA irradiance (sol-air method) |
| Heat flow details vs ambient | hourly conduction/ventilation/solar heat-balance outputs |
| Material comparison | `data/external/materials.csv` (sourced) + parametric sweeps |
| Orientation / shape / openings | geometry module + orientation sweep (0–315°) |
| Thermal mass / insulation | materials DB + insulation sweep (EPS 0–100 mm) |
| User-friendly model | Streamlit dashboard (Phase 7) fed from the same config |

**Scope note:** the statement names ANSYS as an example; it does not mandate a specific tool.
This project deliberately uses a 100% free/open-source stack (EnergyPlus, pvlib, SciPy,
Optuna, Streamlit, SQLite) so the prototype never depends on paid licenses.
