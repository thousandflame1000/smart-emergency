# -*- coding: utf-8 -*-
"""
Typhoon hazard model used by the scenario engine (app/services/scenario.py).

References:
  - Wind field: Holland, G.J. (1980), Mon. Wea. Rev. 108(9):1212-1218.
  - Inland decay: Kaplan & DeMaria (1995), J. Appl. Meteor. 34(11):2499-2512.
  - Wind categories: WMO Beaufort scale; CWA (Taiwan) typhoon intensity classes.
  - Risk framework: UNDRR Risk = Hazard x Exposure x Vulnerability / Capacity.

Track anchors: 2024 Typhoon Kong-rey (康芮) — landfall near Chenggong,
Taitung on 2024-10-31, peak sustained winds ~240 km/h (~67 m/s), landfall
winds ~184 km/h (~51 m/s), tracked NW across the island into the strait;
largest storm to hit Taiwan since 1996 (CNN, NASA Earthdata, Wikipedia).
Landfall location/intensity are the real reported figures; the exact
hourly position and Rmax are our own interpolation (no machine-readable
best-track file was pulled for this build) toward Taichung's longitude,
which is honest but not equivalent to replaying an official best-track.
"""
import math

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1, lng1, lat2, lng2) -> float:
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(d_lng / 2) ** 2)
    return EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))


def holland_wind_speed(vmax_ms: float, rmax_km: float, r_km: float, b: float = 1.5) -> float:
    """Holland (1980) parametric wind profile. b is the peakedness
    parameter (literature range ~1.0-2.5); r_km must be > 0."""
    r_km = max(r_km, 0.1)
    ratio = (rmax_km / r_km) ** b
    return math.sqrt(max(vmax_ms ** 2 * ratio * math.exp(1 - ratio), 0.0))


def kaplan_demaria_decay(v_landfall_ms: float, hours_since_landfall: float,
                          v_background_ms: float = 8.0, decay_per_hour: float = 0.08) -> float:
    """Exponential inland decay toward a residual background wind speed."""
    return v_background_ms + (v_landfall_ms - v_background_ms) * math.exp(-decay_per_hour * hours_since_landfall)


_BEAUFORT_THRESHOLDS = [
    (0.3, 0), (1.6, 1), (3.4, 2), (5.5, 3), (8.0, 4), (10.8, 5),
    (13.9, 6), (17.2, 7), (20.8, 8), (24.5, 9), (28.5, 10), (32.7, 11),
]


def beaufort_scale(v_ms: float) -> int:
    for threshold, scale in reversed(_BEAUFORT_THRESHOLDS):
        if v_ms >= threshold:
            return scale
    return 0


def cwa_typhoon_category(vmax_ms: float) -> str:
    """CWA (中央氣象署) official typhoon intensity classification."""
    if vmax_ms >= 51.0:
        return "強烈颱風"
    if vmax_ms >= 32.7:
        return "中度颱風"
    if vmax_ms >= 17.2:
        return "輕度颱風"
    return "熱帶性低氣壓"


# Beaufort scale -> base hazard severity (0-1), a simplified fragility
# curve (cf. HAZUS-style wind damage functions).
_HAZARD_BASE = {0: 0, 1: 0, 2: 0, 3: .01, 4: .02, 5: .04, 6: .08,
                7: .14, 8: .22, 9: .32, 10: .45, 11: .60, 12: .75}


def report_probability(v_ms: float, vulnerability_pts: float, max_vuln: float = 28.0) -> float:
    """UNDRR risk = hazard x vulnerability, applied to "does this person
    report a need this tick". Exposure is folded into v_ms (already a
    function of distance); capacity is folded into vulnerability's
    existing isolation term (see dispatch._vulnerability_pts)."""
    base = _HAZARD_BASE.get(beaufort_scale(v_ms), 0.75)
    multiplier = 1.0 + vulnerability_pts / max_vuln
    return min(base * multiplier, 0.95)


def urgency_from_risk(v_ms: float, vulnerability_pts: float, max_vuln: float = 28.0) -> int:
    """Urgency from wind severity, adjusted (up to +50%) for personal
    vulnerability — the same person facing the same wind is a more urgent
    ticket if they can't tolerate delay (standard equity-weighted triage
    practice, not double-counting: vulnerability still separately breaks
    ties in dispatch._score)."""
    adjusted = beaufort_scale(v_ms) * (1 + 0.5 * vulnerability_pts / max_vuln)
    if adjusted >= 12: return 5
    if adjusted >= 10: return 4
    if adjusted >= 8:  return 3
    if adjusted >= 6:  return 2
    return 1


def mobilization_capacity(vmax_ms: float) -> int:
    """How many resource units can be mobilized while the storm is at
    this intensity. Modeled on the real operational logic behind
    Taiwan's wind-based work/school suspension thresholds: the more
    dangerous conditions are, the fewer volunteers can safely respond."""
    b = beaufort_scale(vmax_ms)
    if b >= 12: return 1
    if b >= 10: return 2
    if b >= 8:  return 4
    return 8


TICK_HOURS = 3
LANDFALL_TICK = 2
TOTAL_TICKS = 6

# (lat, lng, vmax_ms, rmax_km) per tick, approaching from the Pacific and
# making landfall near Chenggong, Taitung — Kong-rey's real reported
# landfall point and intensity (~184 km/h = 51.1 m/s).
_PRE_LANDFALL = [
    (21.80, 123.00, 67.0, 50.0),
    (22.60, 122.10, 63.0, 52.0),
    (23.10, 121.37, 51.1, 55.0),  # landfall
]
# Post-landfall heading (NW, crossing the island toward the strait) — our
# own interpolation, not part of the reported best track.
_POST_LANDFALL_DELTA = (0.30, -0.50)


def storm_state(tick: int) -> dict:
    """Deterministic storm position/intensity at a given tick."""
    if tick <= LANDFALL_TICK:
        lat, lng, vmax, rmax = _PRE_LANDFALL[tick]
    else:
        steps = tick - LANDFALL_TICK
        lat = _PRE_LANDFALL[LANDFALL_TICK][0] + _POST_LANDFALL_DELTA[0] * steps
        lng = _PRE_LANDFALL[LANDFALL_TICK][1] + _POST_LANDFALL_DELTA[1] * steps
        hours = steps * TICK_HOURS
        vmax = kaplan_demaria_decay(_PRE_LANDFALL[LANDFALL_TICK][2], hours)
        rmax = _PRE_LANDFALL[LANDFALL_TICK][3]
    return {
        "tick": tick, "lat": lat, "lng": lng, "vmax_ms": round(vmax, 1),
        "rmax_km": rmax, "beaufort": beaufort_scale(vmax),
        "category": cwa_typhoon_category(vmax),
    }


def wind_at(tick: int, lat: float, lng: float) -> float:
    state = storm_state(tick)
    r = haversine_km(state["lat"], state["lng"], lat, lng)
    return holland_wind_speed(state["vmax_ms"], state["rmax_km"], r)
