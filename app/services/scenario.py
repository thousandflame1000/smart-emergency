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
from app.services import dispatch, hazard, road_network

SCENARIO_TAG = "[TYPHOON_SIM]"
SCENARIO_NEED_TYPE = "demo_water"
POPULATION_SIZE = 7
# Protagonist's home base: Yuli (玉里), a real Huatung Valley town on the
# road network, inland of Chenggong (the storm's real landfall point) —
# see app/services/road_network.py and hazard.py's track.
BASE_LAT, BASE_LNG = road_network.NODES["yuli"]
# Real towns along the corridor to scatter the simulated neighborhood
# across — gives genuine geographic spread that the road network (not
# straight-line distance) actually has to route between.
_NEIGHBOR_TOWNS = ["ruisui", "fuli", "chishang", "guanshan", "changbin", "chenggong"]

# 每次改動 entities dict 的欄位結構就要 bump 這個版本號。舊版本留在
# SystemConfig 裡的殘留狀態（例如有人在改版前就點過「開始情境」）
# 欄位完全不同，硬用新程式碼的假設去解讀會直接壞掉——版本不符就當
# 作沒開始過，不要嘗試相容解析。
SCENARIO_SCHEMA_VERSION = 3


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
    if entities.get("_schema") != SCENARIO_SCHEMA_VERSION:
        return -1, [], {}
    return step, log, entities


def _save(db: Session, step: int, log: list, entities: dict) -> None:
    entities = {**entities, "_schema": SCENARIO_SCHEMA_VERSION}
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


def _select_population(db: Session, protagonist, population_size: int):
    """
    Scatters a synthetic neighborhood across real Huatung Valley towns
    (app/services/road_network.py) — decoupled from seed_rich_demo.py's
    Taichung dataset on purpose: that data models a different, unrelated
    community, and reusing it here would put the "population" hundreds
    of km from where this storm actually is.
    """
    others = []
    for i in range(population_size):
        town = _NEIGHBOR_TOWNS[i % len(_NEIGHBOR_TOWNS)]
        lat, lng = road_network.NODES[town]
        jitter = (i // len(_NEIGHBOR_TOWNS)) * 0.004
        # Address must be unique per person, not just per town — it's the
        # RNG/sort stable key (_stable_key); a shared address across
        # several residents of the same town makes their per-tick dice
        # rolls identical and their sort order tie-broken by DB row order
        # (not guaranteed stable across a reset+rerun), breaking
        # reproducibility.
        u = User(name=f"{town}居民{i+1}（情境模擬）", roles=["elderly"],
                  address=f"{town}{i+1}號（{SCENARIO_TAG}）",
                  lat=lat + jitter, lng=lng + jitter, is_active=True)
        db.add(u)
        others.append(u)
    db.commit()
    created_ids = [str(u.id) for u in others]
    return others, created_ids


def _init_population(db: Session, entities: dict) -> dict:
    protagonist, p_created = _get_or_create_protagonist(db)
    population_size = entities.get("population_size", POPULATION_SIZE)
    others, created_ids = _select_population(db, protagonist, population_size)
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
    intensity_scale = entities.get("intensity_scale", 1.0)
    capacity_scale = entities.get("capacity_scale", 1.0)
    state = hazard.storm_state(tick, intensity_scale)
    reported = set(entities.get("reported", []))
    ids = ([entities["protagonist"]] if entities.get("protagonist") else []) + entities.get("population", [])
    users = sorted(db.query(User).filter(User.id.in_(ids)).all(), key=_stable_key) if ids else []

    lines = [f"風暴中心 ({state['lat']:.2f}, {state['lng']:.2f})｜近中心最大風速 "
             f"{state['vmax_ms']} m/s｜{state['beaufort']} 級風｜{state['category']}"]

    for u in users:
        uid = str(u.id)
        if uid in reported:
            continue
        v_ms = hazard.wind_at(tick, u.lat or state["lat"], u.lng or state["lng"], intensity_scale)
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
        # Anchor each mobilized resource to an actual reported need's
        # location — real relief mobilization sends volunteers toward
        # where help was actually requested, not to an abstract centroid
        # of the whole population. With real towns tens of km apart along
        # the corridor, a centroid lands nowhere near anyone and every
        # need gets rejected by the urgency-5 2km dispatch radius
        # (dispatch.URGENCY_MAX_KM) regardless of vulnerability — a real
        # bug this design avoids structurally, not by loosening the cap.
        reported_locs = [(u.lat, u.lng) for u in users
                          if str(u.id) in reported and u.lat is not None and u.lng is not None]
        if not reported_locs:
            reported_locs = [(BASE_LAT, BASE_LNG)]
        else:
            # Real relief planners stage supplies where they reach the
            # most people, not at an arbitrary reported location — sort
            # candidate anchors by how many other reports are within a
            # realistic catchment radius (real road distance), so scarce
            # resources land somewhere multiple needs can actually
            # compete for them, not wherever happened to sort first.
            COVERAGE_RADIUS_KM = 8.0  # matches dispatch.URGENCY_MAX_KM's urgency-3 tier

            def _dist(a, b):
                d = road_network.road_distance_km(*a, *b, db=db)
                return d if d is not None else hazard.haversine_km(*a, *b)

            def _coverage(loc):
                return sum(1 for other in reported_locs if _dist(loc, other) <= COVERAGE_RADIUS_KM)

            reported_locs = sorted(reported_locs, key=_coverage, reverse=True)

        n = hazard.mobilization_capacity(state["vmax_ms"], capacity_scale)
        for i in range(n):
            anchor_lat, anchor_lng = reported_locs[i % len(reported_locs)]
            vol = User(name=f"颱風夜志工{i+1}（情境模擬）", roles=["volunteer"],
                        address=f"情境模擬地址（{SCENARIO_TAG}）",
                        lat=anchor_lat + i * 0.0005, lng=anchor_lng + i * 0.0005, is_active=True)
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
    step, log, entities = _load(db)
    intensity_scale = entities.get("intensity_scale", 1.0)
    capacity_scale = entities.get("capacity_scale", 1.0)
    population_size = entities.get("population_size", POPULATION_SIZE)
    autoplay = _cfg_get(db, "scenario_autoplay", "0") == "1"
    steps = []
    for i in range(hazard.TOTAL_TICKS):
        st = hazard.storm_state(i, intensity_scale)
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
        "params": {
            "intensity_scale": intensity_scale,
            "capacity_scale": capacity_scale,
            "population_size": population_size,
            "intensity_scale_range": list(hazard.INTENSITY_SCALE_RANGE),
            "capacity_scale_range": list(hazard.CAPACITY_SCALE_RANGE),
        },
    }


def start(db: Session, intensity_scale: float = 1.0, capacity_scale: float = 1.0,
          population_size: int | None = None) -> dict:
    reset(db)
    entities = {
        "intensity_scale": min(max(intensity_scale, hazard.INTENSITY_SCALE_RANGE[0]), hazard.INTENSITY_SCALE_RANGE[1]),
        "capacity_scale": min(max(capacity_scale, hazard.CAPACITY_SCALE_RANGE[0]), hazard.CAPACITY_SCALE_RANGE[1]),
        "population_size": min(max(population_size or POPULATION_SIZE, 1), 30),
    }
    _save(db, -1, [], entities)
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

    # 保險清除：不管 entities 是不是能正常解析（例如改版前留下的舊
    # 欄位結構、或 _load() 判定版本不符直接丟棄），只要是情境自己
    # 打過標籤的資料，一律用標籤掃過去清掉——比只信任 entities 記錄
    # 更可靠，且不會誤刪任何沒有這個標籤的既有/真實資料。
    for n in db.query(CommunityNeed).filter(CommunityNeed.description.like(f"{SCENARIO_TAG}%")).all():
        db.delete(n)
    for r in db.query(CommunityResource).filter(CommunityResource.note == SCENARIO_TAG).all():
        db.delete(r)
    for u in db.query(User).filter(User.address.like(f"%{SCENARIO_TAG}%")).all():
        # A need can point at a tagged resident without being tagged itself —
        # e.g. an operator manually created one via the admin "+ 新增需求"
        # form and picked a simulated resident from the requester dropdown.
        # community_needs.requester_id is NOT NULL, so deleting that user
        # without also clearing their needs makes SQLAlchemy's cascade try
        # to null the column and crash reset()/start() with a 500 — which
        # then makes every future reset()/start() call crash the same way,
        # since the broken need never gets cleaned up. Catch it by
        # requester_id too, not just by description tag.
        db.query(CommunityNeed).filter(CommunityNeed.requester_id == u.id).delete(synchronize_session=False)
        db.query(Alert).filter(Alert.elderly_id == u.id).delete(synchronize_session=False)
        db.query(DailyCheckin).filter(DailyCheckin.elderly_id == u.id).delete(synchronize_session=False)
        db.delete(u)

    db.commit()
    _cfg_set(db, "scenario_autoplay", "0")
    _save(db, -1, [], {})
    return status(db)


def set_autoplay(db: Session, enabled: bool) -> dict:
    _cfg_set(db, "scenario_autoplay", "1" if enabled else "0")
    db.commit()
    return status(db)
