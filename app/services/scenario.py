# -*- coding: utf-8 -*-
"""
Finals demo scenario engine. Drives a typhoon event tick-by-tick using the
physical/statistical models in app/services/hazard.py, then runs the real
dispatch.auto_dispatch() against whatever needs/resources emerge. Nothing
here is scripted — who reports, when, and at what urgency is computed from
wind exposure and each person's actual vulnerability score.

Mode switching only flips the DB flag (no LINE broadcast) so this can be
re-run during rehearsal without spamming real users.
"""
import json
import random
from datetime import date, timedelta, datetime

from sqlalchemy.orm import Session

from app.models.user import User
from app.models.checkin import DailyCheckin
from app.models.alert import Alert
from app.models.resource import CommunityResource
from app.models.need import CommunityNeed
from app.models.config import SystemConfig
from app.services import dispatch, hazard

SCENARIO_TAG = "[TYPHOON_SIM]"
SCENARIO_NEED_TYPE = "demo_water"
POPULATION_SIZE = 7
BASE_LAT, BASE_LNG = 24.150, 120.670


def _cfg_get(db: Session, key: str, default: str) -> str:
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    return row.value if row else default


def _cfg_set(db: Session, key: str, value: str) -> None:
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SystemConfig(key=key, value=value))


def _load(db: Session):
    step = int(_cfg_get(db, "scenario_step", "-1"))
    log = json.loads(_cfg_get(db, "scenario_log", "[]"))
    entities = json.loads(_cfg_get(db, "scenario_entities", "{}"))
    return step, log, entities


def _save(db: Session, step: int, log: list, entities: dict) -> None:
    _cfg_set(db, "scenario_step", str(step))
    _cfg_set(db, "scenario_log", json.dumps(log, ensure_ascii=False))
    _cfg_set(db, "scenario_entities", json.dumps(entities, ensure_ascii=False))
    db.commit()


def _get_or_create_protagonist(db: Session):
    p = db.query(User).filter(User.name == "王秀霞").first()
    if p:
        return p, False

    p = User(name="王秀霞", roles=["elderly"], address=f"情境模擬（{SCENARIO_TAG}）",
              lat=BASE_LAT + 0.001, lng=BASE_LNG + 0.001, is_active=True)
    db.add(p)
    db.commit()
    today = date.today()
    for days_ago in range(1, 6):
        db.add(DailyCheckin(elderly_id=p.id, date=today - timedelta(days=days_ago), status="no_response"))
    db.commit()
    db.add(Alert(elderly_id=p.id, alert_type="no_response_3h", notified_users=[], status="sent"))
    db.commit()
    return p, True


def _stable_key(u: User) -> str:
    """Sort/RNG-seed key derived from content, not the randomly-generated
    UUID primary key — keeps population selection and dice rolls
    reproducible across a full wipe+reseed of seed_rich_demo.py, whose
    generated names/addresses are themselves deterministic (random.seed(42))."""
    return u.address or u.name or str(u.id)


NEIGHBORHOOD_RADIUS_KM = 3.0  # matches dispatch.URGENCY_MAX_KM's tightest
                              # tier (urgency 5 -> 2km) with headroom; a
                              # city-spanning population would violate the
                              # platform's own "neighborhood watch" scope
                              # and make its own dispatch radius unreachable.


def _select_population(db: Session, protagonist):
    all_elderly = (
        db.query(User)
        .filter(User.role_filter("elderly"), User.id != protagonist.id, User.is_active == True)
        .all()
    )
    candidates = [
        u for u in all_elderly
        if u.lat is not None and u.lng is not None
        and hazard.haversine_km(protagonist.lat, protagonist.lng, u.lat, u.lng) <= NEIGHBORHOOD_RADIUS_KM
    ]
    others = sorted(candidates, key=_stable_key)[:POPULATION_SIZE]

    created_ids = []
    if len(others) < 4:
        base_lat = protagonist.lat if protagonist.lat is not None else BASE_LAT
        base_lng = protagonist.lng if protagonist.lng is not None else BASE_LNG
        for i in range(4 - len(others)):
            u = User(name=f"社區長者{i+1}（情境模擬）", roles=["elderly"],
                      address=f"情境模擬地址{i+1}（{SCENARIO_TAG}）",
                      lat=base_lat + (i - 2) * 0.01, lng=base_lng + (i % 3) * 0.01,
                      is_active=True)
            db.add(u)
            others.append(u)
        db.commit()
        created_ids = [str(u.id) for u in others[-(4 - len(candidates)):]]
    return others, created_ids


def _init_population(db: Session, entities: dict) -> dict:
    protagonist, p_created = _get_or_create_protagonist(db)
    others, created_ids = _select_population(db, protagonist)
    entities["protagonist"] = str(protagonist.id)
    if p_created:
        entities["protagonist_created"] = "1"
    entities["population"] = [str(u.id) for u in others]
    entities["population_created"] = created_ids
    entities["reported"] = []
    entities["resources"] = []
    entities["resource_owners"] = []
    entities["needs"] = []
    return entities


def _run_tick(db: Session, tick: int, entities: dict):
    state = hazard.storm_state(tick)
    reported = set(entities.get("reported", []))
    ids = ([entities["protagonist"]] if entities.get("protagonist") else []) + entities.get("population", [])
    users = sorted(db.query(User).filter(User.id.in_(ids)).all(), key=_stable_key) if ids else []

    lines = [f"風暴中心 ({state['lat']:.2f}, {state['lng']:.2f})｜近中心最大風速 "
             f"{state['vmax_ms']} m/s｜{state['beaufort']} 級風｜{state['category']}"]

    for u in users:
        uid = str(u.id)
        if uid in reported:
            continue
        v_ms = hazard.wind_at(tick, u.lat or state["lat"], u.lng or state["lng"])
        vuln = dispatch._vulnerability_pts(u.id, db)
        p = hazard.report_probability(v_ms, vuln)
        # Independent per-person RNG keyed by (tick, stable identity) —
        # not a shared stream — so the draw doesn't depend on iteration
        # order or on any other person's outcome.
        roll = random.Random(f"{tick}:{_stable_key(u)}").random()
        if roll < p:
            urgency = hazard.urgency_from_risk(v_ms, vuln)
            need = CommunityNeed(
                requester_id=u.id, need_type=SCENARIO_NEED_TYPE,
                description=f"{SCENARIO_TAG} 第{tick}時段：{u.name} 通報需要物資",
                address=u.address, lat=u.lat, lng=u.lng,
                urgency=urgency, status="open",
            )
            db.add(need)
            db.commit()
            reported.add(uid)
            entities.setdefault("needs", []).append(str(need.id))
            lines.append(f"「{u.name}」通報｜當地風速 {v_ms:.1f} m/s（{hazard.beaufort_scale(v_ms)} 級）"
                         f"｜脆弱度 {vuln:.0f}｜緊急度 {urgency}")

    entities["reported"] = list(reported)

    if tick == hazard.LANDFALL_TICK and not entities.get("resources"):
        # Anchor mobilized resources to the population's centroid, not an
        # arbitrary fixed point — otherwise the tight 2km radius on
        # urgency-5 needs (dispatch.URGENCY_MAX_KM) rejects people purely
        # for being far from wherever we happened to place volunteers,
        # which has nothing to do with vulnerability.
        located = [u for u in users if u.lat is not None and u.lng is not None]
        center_lat = sum(u.lat for u in located) / len(located) if located else BASE_LAT
        center_lng = sum(u.lng for u in located) / len(located) if located else BASE_LNG

        n = hazard.mobilization_capacity(state["vmax_ms"])
        for i in range(n):
            vol = User(name=f"颱風夜志工{i+1}（情境模擬）", roles=["volunteer"],
                        address=f"情境模擬地址（{SCENARIO_TAG}）",
                        lat=center_lat + i * 0.001, lng=center_lng + i * 0.001, is_active=True)
            db.add(vol)
            db.commit()
            res = CommunityResource(
                owner_id=vol.id, resource_type=SCENARIO_NEED_TYPE,
                name=f"颱風夜調度物資 {i+1}", quantity="1份",
                lat=vol.lat, lng=vol.lng, is_available=True, note=SCENARIO_TAG,
            )
            db.add(res)
            db.commit()
            entities.setdefault("resource_owners", []).append(str(vol.id))
            entities.setdefault("resources", []).append(str(res.id))
        lines.append(f"風雨達危險等級，僅能動員 {n} 組物資出勤"
                     "（動員量隨風力等級調降，見 hazard.mobilization_capacity）")

    if entities.get("resources"):
        result = dispatch.auto_dispatch()
        lines.append(f"自動媒合：{result.get('suggested', 0)} 筆建議待確認、"
                     f"{result.get('skipped', 0)} 筆資源不足、{result.get('matched', 0)} 筆完成")

    return "\n".join(lines), entities


def status(db: Session) -> dict:
    step, log, _ = _load(db)
    autoplay = _cfg_get(db, "scenario_autoplay", "0") == "1"
    steps = []
    for i in range(hazard.TOTAL_TICKS):
        st = hazard.storm_state(i)
        steps.append({
            "title": f"Tick {i} · {st['category']}",
            "desc": f"風速 {st['vmax_ms']} m/s（{st['beaufort']} 級風）",
            "done": i <= step,
        })
    return {
        "step": step,
        "total": hazard.TOTAL_TICKS,
        "finished": step >= hazard.TOTAL_TICKS - 1,
        "autoplay": autoplay,
        "log": log,
        "steps": steps,
    }


def start(db: Session) -> dict:
    reset(db)
    return advance(db)


def advance(db: Session) -> dict:
    step, log, entities = _load(db)
    next_step = step + 1
    if next_step >= hazard.TOTAL_TICKS:
        _cfg_set(db, "scenario_autoplay", "0")
        db.commit()
        return status(db)

    if next_step == 0:
        _cfg_set(db, "mode", "emergency")
        entities = _init_population(db, entities)

    message, entities = _run_tick(db, next_step, entities)
    db.commit()

    log.append({"step": next_step, "title": f"Tick {next_step}", "message": message,
                "at": datetime.now().strftime("%H:%M:%S")})
    if next_step >= hazard.TOTAL_TICKS - 1:
        _cfg_set(db, "scenario_autoplay", "0")
    _save(db, next_step, log, entities)
    return status(db)


def reset(db: Session) -> dict:
    _, _, entities = _load(db)

    for nid in entities.get("needs", []):
        n = db.query(CommunityNeed).filter(CommunityNeed.id == nid).first()
        if n:
            db.delete(n)
    for rid in entities.get("resources", []):
        r = db.query(CommunityResource).filter(CommunityResource.id == rid).first()
        if r:
            db.delete(r)
    for vid in entities.get("resource_owners", []):
        v = db.query(User).filter(User.id == vid).first()
        if v:
            db.delete(v)
    for uid in entities.get("population_created", []):
        u = db.query(User).filter(User.id == uid).first()
        if u:
            db.delete(u)
    if entities.get("protagonist_created") == "1":
        pid = entities.get("protagonist")
        if pid:
            db.query(Alert).filter(Alert.elderly_id == pid).delete(synchronize_session=False)
            db.query(DailyCheckin).filter(DailyCheckin.elderly_id == pid).delete(synchronize_session=False)
            p = db.query(User).filter(User.id == pid).first()
            if p:
                db.delete(p)

    db.commit()
    _cfg_set(db, "scenario_autoplay", "0")
    _save(db, -1, [], {})
    return status(db)


def set_autoplay(db: Session, enabled: bool) -> dict:
    _cfg_set(db, "scenario_autoplay", "1" if enabled else "0")
    db.commit()
    return status(db)
