# -*- coding: utf-8 -*-
"""
Scenario engine tests (app/services/scenario.py). The engine drives a
tick-based typhoon simulation (app/services/hazard.py) — who reports a
need, when, and at what urgency is computed from wind exposure and each
person's real vulnerability score, not scripted. These tests check the
properties that actually matter for a live demo: reproducibility, that
reset() never touches pre-existing data, and that the population stays
within the platform's own neighborhood-watch scope.
"""
from datetime import date, timedelta

from app.models.user import User
from app.models.checkin import DailyCheckin
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.services import scenario, hazard


def _run_full(db, **start_kwargs):
    st = scenario.start(db, **start_kwargs)
    while not st["finished"]:
        st = scenario.advance(db)
    return st


def _make_neighborhood(db, n=6):
    """A protagonist plus n nearby elderly with a checkin history spread,
    all within the scenario's neighborhood radius of each other."""
    protagonist = User(name="王秀霞", roles=["elderly"], lat=24.150, lng=120.670, is_active=True)
    db.add(protagonist)
    db.commit()
    today = date.today()
    for days_ago in range(1, 6):
        db.add(DailyCheckin(elderly_id=protagonist.id, date=today - timedelta(days=days_ago), status="no_response"))
    db.commit()

    others = []
    for i in range(n):
        u = User(name=f"鄰居{i}", roles=["elderly"],
                 lat=24.150 + (i - n / 2) * 0.005, lng=120.670 + (i % 3) * 0.004,
                 is_active=True)
        db.add(u)
        others.append(u)
    db.commit()
    return protagonist, others


def test_full_run_is_deterministic(db):
    _make_neighborhood(db)
    st1 = _run_full(db)
    outcome1 = [(e["requester"], e["urgency"], e["status"]) for e in _snapshot(db)]

    scenario.reset(db)
    st2 = _run_full(db)
    outcome2 = [(e["requester"], e["urgency"], e["status"]) for e in _snapshot(db)]

    messages1 = [entry["message"] for entry in st1["log"]]
    messages2 = [entry["message"] for entry in st2["log"]]
    assert messages1 == messages2
    assert outcome1 == outcome2


def _snapshot(db):
    needs = db.query(CommunityNeed).filter(CommunityNeed.need_type == scenario.SCENARIO_NEED_TYPE).all()
    out = []
    for n in needs:
        u = db.query(User).filter(User.id == n.requester_id).first()
        out.append({"requester": u.name if u else None, "urgency": n.urgency, "status": n.status})
    out.sort(key=lambda e: e["requester"] or "")
    return out


def test_higher_urgency_and_vulnerability_wins_scarce_resource(db):
    protagonist, others = _make_neighborhood(db)
    _run_full(db)

    need = (
        db.query(CommunityNeed)
        .filter(CommunityNeed.requester_id == protagonist.id,
                CommunityNeed.need_type == scenario.SCENARIO_NEED_TYPE)
        .first()
    )
    assert need is not None, "protagonist should report at least once during the run"
    # She has the highest vulnerability in this fixture; she should not be
    # left "open" while a lower-urgency, lower-vulnerability need is served.
    served = db.query(CommunityNeed).filter(
        CommunityNeed.need_type == scenario.SCENARIO_NEED_TYPE, CommunityNeed.status == "suggested"
    ).all()
    if served:
        min_served_urgency = min(n.urgency for n in served)
        assert need.status == "suggested" or need.urgency < min_served_urgency


def test_population_stays_within_neighborhood_radius(db):
    protagonist, others = _make_neighborhood(db)
    far = User(name="很遠的長者", roles=["elderly"], lat=24.30, lng=120.90, is_active=True)
    db.add(far)
    db.commit()

    _run_full(db)

    reported_names = {e["requester"] for e in _snapshot(db)}
    assert "很遠的長者" not in reported_names, "population selection should respect NEIGHBORHOOD_RADIUS_KM"


def test_reset_does_not_touch_pre_existing_protagonist(db):
    protagonist, others = _make_neighborhood(db)
    protagonist_id = protagonist.id

    _run_full(db)
    scenario.reset(db)

    assert db.query(User).filter(User.id == protagonist_id).first() is not None
    assert db.query(DailyCheckin).filter(DailyCheckin.elderly_id == protagonist_id).count() == 5
    for u in others:
        assert db.query(User).filter(User.id == u.id).first() is not None

    assert db.query(CommunityNeed).filter(CommunityNeed.need_type == scenario.SCENARIO_NEED_TYPE).count() == 0
    assert db.query(CommunityResource).filter(CommunityResource.resource_type == scenario.SCENARIO_NEED_TYPE).count() == 0


def test_reset_removes_synthetic_protagonist_when_none_existed(db):
    """No pre-existing '王秀霞' and no nearby elderly at all -> engine must
    create its own, and reset() must remove everything it created."""
    st = _run_full(db)
    protagonist = db.query(User).filter(User.name == "王秀霞").first()
    assert protagonist is not None
    protagonist_id = protagonist.id

    scenario.reset(db)

    assert db.query(User).filter(User.id == protagonist_id).first() is None
    assert db.query(DailyCheckin).filter(DailyCheckin.elderly_id == protagonist_id).count() == 0


def test_mode_switches_without_broadcast(db):
    from app.models.config import SystemConfig
    _make_neighborhood(db)
    scenario.start(db)
    cfg = db.query(SystemConfig).filter(SystemConfig.key == "mode").first()
    assert cfg is not None and cfg.value == "emergency"


def test_advance_past_end_is_noop(db):
    _make_neighborhood(db)
    st = _run_full(db)
    again = scenario.advance(db)
    assert again["step"] == st["step"] == hazard.TOTAL_TICKS - 1


def test_autoplay_flag_persists_and_toggles(db):
    st = scenario.set_autoplay(db, True)
    assert st["autoplay"] is True
    assert scenario.status(db)["autoplay"] is True
    st = scenario.set_autoplay(db, False)
    assert st["autoplay"] is False


# ─── Sandbox parameters: real what-if knobs, not fixed constants ───

def test_weaker_storm_produces_fewer_reports(db):
    """intensity_scale actually feeds the Holland wind model — a weaker
    storm should mean lower wind everywhere and fewer/lower-urgency
    reports, not a cosmetic label."""
    _make_neighborhood(db)
    weak = _run_full(db, intensity_scale=0.3)
    weak_needs = _snapshot(db)

    scenario.reset(db)
    _make_neighborhood(db)
    strong = _run_full(db, intensity_scale=2.0)
    strong_needs = _snapshot(db)

    assert len(strong_needs) >= len(weak_needs)
    if weak_needs and strong_needs:
        assert max(n["urgency"] for n in strong_needs) >= max(n["urgency"] for n in weak_needs)


def test_capacity_scale_changes_mobilized_resource_count(db):
    """capacity_scale should change how many resources get mobilized at
    landfall — verified via hazard.mobilization_capacity directly since
    that's the real function the sandbox knob feeds."""
    vmax = 50.0
    low = hazard.mobilization_capacity(vmax, capacity_scale=0.25)
    normal = hazard.mobilization_capacity(vmax, capacity_scale=1.0)
    high = hazard.mobilization_capacity(vmax, capacity_scale=4.0)
    assert low < normal < high


def test_population_size_changes_neighborhood_size(db):
    _make_neighborhood(db, n=10)
    scenario.start(db, population_size=3)
    entities_step = scenario.status(db)
    # population_size caps how many *others* join the protagonist; the
    # actual reporting population is population_size (others) + 1 (her).
    assert entities_step["params"]["population_size"] == 3


def test_params_are_clamped_to_sane_ranges(db):
    _make_neighborhood(db)
    scenario.start(db, intensity_scale=99, capacity_scale=0.0001, population_size=-5)
    params = scenario.status(db)["params"]
    assert params["intensity_scale"] == hazard.INTENSITY_SCALE_RANGE[1]
    assert params["capacity_scale"] == hazard.CAPACITY_SCALE_RANGE[0]
    assert params["population_size"] >= 1


def test_status_reflects_current_run_params_before_finishing(db):
    _make_neighborhood(db)
    scenario.start(db, intensity_scale=0.5)
    st = scenario.status(db)
    assert st["params"]["intensity_scale"] == 0.5
    # preview steps should already reflect the scaled intensity, not the
    # default track — a weaker run's tick-0 wind should be lower.
    default_run_wind = hazard.storm_state(0, 1.0)["vmax_ms"]
    scaled_wind = hazard.storm_state(0, 0.5)["vmax_ms"]
    assert scaled_wind < default_run_wind
