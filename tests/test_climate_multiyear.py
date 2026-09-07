"""Multi-year, self-updating climate archive (SEC/10 + period selector).

Guards the properties that make the upgrade trustworthy:
  * the default analysis period tracks recent conditions, not a hardcoded year
  * calendar years are sliced on LOCAL time (no stray 13th month)
  * partial years are compared like-for-like, never against a full year
  * future hours are never invented
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.api_app import app, resolve_period, _period_year, _zone_from
from src.data import climate_archive as ca

client = TestClient(app)

HAS_ARCHIVE = bool(ca.load_index().get("sites"))
needs_archive = pytest.mark.skipif(
    not HAS_ARCHIVE, reason="local climate archive not generated")


# ---------------------------------------------------------------- period token
def test_resolve_period_defaults_to_latest():
    assert resolve_period(None) == "latest"
    assert resolve_period("") == "latest"
    assert resolve_period("latest") == "latest"
    assert resolve_period("auto") == "latest"


def test_resolve_period_accepts_calendar_years():
    assert resolve_period(2025) == 2025
    assert resolve_period("2024") == 2024
    # nonsense falls back to the rolling window rather than exploding
    assert resolve_period("banana") == "latest"
    assert resolve_period(1492) == "latest"


def test_period_year_is_an_int_for_legacy_callers():
    assert _period_year(2025) == 2025
    assert isinstance(_period_year("latest"), int)


# -------------------------------------------------------------------- coverage
@needs_archive
def test_coverage_reports_real_freshness():
    r = client.get("/api/climate/coverage")
    assert r.status_code == 200
    j = r.json()
    assert j["sites"], "no sites in coverage"
    assert j["default_period"] == "latest"
    assert j["latest_hour_utc"]
    # the selector always offers the rolling window first
    assert j["options"][0]["value"] == "latest"
    assert j["options"][0]["default"] is True
    years = [o["value"] for o in j["options"][1:]]
    assert years == sorted(years, reverse=True), "years should be newest-first"


@needs_archive
def test_no_future_hours_are_fabricated():
    """The archive must stop at the present, whatever the manifest claims."""
    latest = pd.Timestamp(ca.latest_hour())
    tomorrow = pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=1)
    assert latest <= tomorrow, f"archive claims data from the future: {latest}"


@needs_archive
def test_partial_year_is_flagged_not_padded():
    idx = ca.load_index()
    this_year = str(dt.date.today().year)
    for site, meta in idx["sites"].items():
        y = meta["years"].get(this_year)
        if not y:
            continue
        assert y["status"] == "year_to_date"
        assert y["n_hours"] < 8760, f"{site}: in-progress year looks padded"


# ----------------------------------------------------------------- year slicing
@needs_archive
def test_calendar_year_slices_on_local_time():
    """A UTC slice at +05:30 leaks into January and creates a 13th month."""
    site = next(iter(ca.load_index()["sites"]))
    df = ca.read_local(site)
    sub = ca.slice_period(df, 2025, site, "Asia/Kolkata")
    local = sub.index.tz_convert("Asia/Kolkata")
    assert set(local.year) == {2025}
    assert local.month.nunique() == 12
    assert len(sub) == 8760


@needs_archive
def test_rolling_window_is_a_full_year_ending_now():
    site = next(iter(ca.load_index()["sites"]))
    df = ca.read_local(site)
    roll = ca.slice_period(df, "latest", site)
    assert 8700 <= len(roll) <= 8790
    assert str(roll.index[-1]) == str(df.index[-1]), "rolling window must end at the newest hour"


# -------------------------------------------------------------------- trends
@needs_archive
def test_trends_compare_like_for_like():
    site = next(iter(ca.load_index()["sites"]))
    r = client.get(f"/api/climate/trends?site={site}")
    assert r.status_code == 200
    j = r.json()
    assert len(j["years"]) >= 2
    doy = j["comparable_through_doy"]
    assert 1 <= doy <= 366
    # every year truncated to the same span => comparable hour counts
    hours = [y["n_hours"] for y in j["years"]]
    assert max(hours) - min(hours) <= 48, f"years not truncated evenly: {hours}"
    assert j["delta"]["span"].startswith(str(j["years"][0]["year"]))


@needs_archive
def test_trends_reject_unknown_site():
    assert client.get("/api/climate/trends?site=Atlantis").status_code == 404
    assert client.get("/api/climate/trends").status_code == 400


@needs_archive
def test_design_shift_reports_both_stress_cases():
    site = next(iter(ca.load_index()["sites"]))
    j = client.get(f"/api/climate/trends?site={site}").json()
    shift = j["design_shift"]
    assert "hot" in shift and "cold" in shift
    hot = shift["hot"]
    assert hot["from_peak_c"] > hot["from_mean_c"], "peak must exceed the week mean"
    assert hot["to_peak_c"] > shift["cold"]["to_peak_c"], "hot week must beat cold week"


# ---------------------------------------------------------------- zone normals
@needs_archive
def test_zone_uses_climatological_normals_not_extremes():
    """Adding years must not make a site progressively 'colder'.

    min() over every archived month returns the most extreme month ever seen;
    averaging January-with-January is what a normal means.
    """
    df = ca.read_local("Jaisalmer")
    if df is None:
        pytest.skip("Jaisalmer not archived")
    df = df.copy()
    df.index = df.index.tz_convert("Asia/Kolkata")
    zone, t_hot, t_cold, rh = _zone_from(df)
    assert zone == "hot_dry", f"got {zone} (t_cold={t_cold:.2f}, rh={rh:.1f})"
    # a desert's coldest-month normal sits well above any single cold January
    coldest_single = df["t2m"].resample("ME").mean().min()
    assert t_cold > coldest_single


@needs_archive
def test_zone_is_stable_across_selected_period():
    """The zone label must not flip when the user changes the period."""
    seen = set()
    for period in ("latest", "2024", "2025"):
        j = client.post("/api/location/profile",
                        json={"lat": 26.9157, "lon": 70.9083,
                              "climate_period": period}).json()
        seen.add(j["zone"])
    assert len(seen) == 1, f"zone flipped across periods: {seen}"


# -------------------------------------------------------------- climate endpoint
@needs_archive
def test_climate_endpoint_serves_rolling_and_fixed_periods():
    base = "/api/climate?lat=25.4358&lon=81.8463"
    roll = client.get(f"{base}&year=latest").json()
    assert roll["location"]["period"] == "latest"
    assert "Rolling" in roll["location"]["period_label"]
    assert roll["n_hours"] > 8000

    fixed = client.get(f"{base}&year=2025").json()
    assert fixed["location"]["period"] == 2025
    assert fixed["location"]["period_label"] == "2025"
    assert fixed["n_hours"] == 8760
    # different windows must give different weather
    assert roll["summary"] != fixed["summary"]


@needs_archive
def test_simulate_honours_the_climate_period():
    body = {"lat": 25.4358, "lon": 81.8463, "period": "hot_week"}
    a = client.post("/api/simulate", json={**body, "climate_period": "2024"}).json()
    b = client.post("/api/simulate", json={**body, "climate_period": "2025"}).json()
    assert a["metrics"]["max_indoor_c"] != b["metrics"]["max_indoor_c"]


@needs_archive
def test_static_bundle_matches_the_archive():
    """The offline fallback must not drift from the real archive."""
    bundle = ca.load_bundle()
    assert bundle.get("sites"), "bundle not built"
    idx = ca.load_index()
    for site, entry in bundle["sites"].items():
        assert site in idx["sites"]
        for year in entry["years"]:
            assert year in idx["sites"][site]["years"]
            b = entry["years"][year]["summary"]["t2m_mean_c"]
            a = idx["sites"][site]["years"][year]["t2m_mean_c"]
            assert abs(a - b) < 0.5, f"{site} {year}: bundle {b} vs archive {a}"
