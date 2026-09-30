"""一鍵求救的「附近志工」與「受理」。

參考 PulsePoint：有人求救時，除了家屬與管理員，也推給住在附近、已核准的志工；
第一個按「我過去」的人成為處理人（受理），其他人就知道已經有人在處理，不會一窩蜂跑去、
也不會每個人都以為別人會去。管理員也可以在工作區或 LINE 按「我來處理」受理。

受理只改 responder_id / acknowledged_at，狀態仍是 open；處理完由處理人或管理員結案
（dispatch.resolve_sos），整段都寫進 dispatch_events，工作區的任務紀錄看得到。
"""
import logging

from sqlalchemy.orm import Session

from app.models.need import CommunityNeed
from app.models.user import User
from app.services.geo import haversine_km
from app.timeutil import now_utc
from app.validation import tel_uri

logger = logging.getLogger(__name__)

NEARBY_KM = 2.0     # 鄉下住得散，800 公尺常常一個人都沒有；2 公里騎車也是幾分鐘
NEARBY_MAX = 5      # 叫太多人反而亂，先叫最近的幾位
ESCALATE_MINUTES = 10  # 求救這麼久還沒人受理，再叫管理員一次
ARRIVAL_MINUTES = 20   # 受理這麼久還沒回報到場，提醒管理員（可能被耽擱，要改派或打電話問）


def spot(need: CommunityNeed) -> tuple[float, float] | None:
    """求救的位置：求救單上的座標，沒有就用求助者登記的位置。"""
    if need.lat is not None and need.lng is not None:
        return need.lat, need.lng
    requester = need.requester
    if requester is not None and requester.lat is not None and requester.lng is not None:
        return requester.lat, requester.lng
    return None


PAUSE_PREFIX = "volunteer_paused:"


def paused_ids(db: Session) -> set[str]:
    """傳了「暫停支援」的志工（人在外地、身體不便）：附近求救不叫他們。"""
    from app.models.config import SystemConfig
    return {row.key[len(PAUSE_PREFIX):] for row in db.query(SystemConfig).filter(SystemConfig.key.like(PAUSE_PREFIX + "%")).all()}


def set_paused(db: Session, user: User, paused: bool) -> None:
    from app.models.config import SystemConfig
    key = PAUSE_PREFIX + str(user.id)
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if paused and row is None:
        db.add(SystemConfig(key=key, value=now_utc().isoformat()))
    elif not paused and row is not None:
        db.delete(row)
    db.commit()


def _navigate(need: CommunityNeed) -> str | None:
    where = spot(need)
    return f"https://www.google.com/maps/dir/?api=1&destination={where[0]},{where[1]}" if where else None


def _aed_line(need: CommunityNeed) -> str | None:
    from app.services.aed import one_line
    where = spot(need)
    return one_line(*where) if where else None


def nearby_volunteers(db: Session, need: CommunityNeed) -> list[tuple[User, float]]:
    """已核准、綁了 LINE、有登記位置的志工，由近到遠；求救者本人不算。"""
    where = spot(need)
    if where is None:
        return []
    rows = []
    paused = paused_ids(db)
    for user in db.query(User).filter(User.role_filter("volunteer"), User.is_active.is_(True),
                                      User.line_uid.isnot(None), User.lat.isnot(None)).all():
        if user.id == need.requester_id or str(user.id) in paused:
            continue
        km = haversine_km(where[0], where[1], user.lat, user.lng)
        if km <= NEARBY_KM:
            rows.append((user, km))
    rows.sort(key=lambda r: r[1])
    return rows[:NEARBY_MAX]


def alert_nearby(db: Session, need: CommunityNeed) -> int:
    """推「附近有人需要幫忙」給附近志工。先不給電話，按了「我過去」才給，減少個資外流。"""
    from app.services.line_ops import bubble
    from app.services.outbox import send_flex_reliably
    requester = need.requester
    sent = 0
    for volunteer, km in nearby_volunteers(db, need):
        distance = f"{km * 1000:.0f} 公尺" if km < 1 else f"{km:.1f} 公里"
        buttons = [{"label": "🏃 我過去", "data": f"action=sos_go&need_id={need.id}", "color": "#c0392b"}]
        if _navigate(need):
            buttons.append({"label": "🧭 導航", "uri": _navigate(need)})
        card = bubble("🆘 附近有人需要幫忙", "#c0392b",
                      [f"{requester.name if requester else '一位居民'}按下了一鍵求救",
                       f"地點：{need.address or '見導航'}（離您約 {distance}）",
                       *([_aed_line(need)] if _aed_line(need) else []),
                       "能過去看看的話請按「我過去」；第一位按的人負責，其他人會收到通知。",
                       "有生命危險請直接撥 119。"], buttons)
        # 先存進寄件佇列再推，LINE 暫時失敗會重試；已排入就算通知到了
        send_flex_reliably(aggregate_type="CommunityNeed", aggregate_id=str(need.id), destination=volunteer.line_uid,
                           alt="🆘 附近有人需要幫忙", contents=card, dedupe_key=f"sos-nearby:{need.id}:{volunteer.id}")
        sent += 1
    return sent


def responder_card(need: CommunityNeed) -> dict:
    """受理後給處理人的卡片：電話、導航、處理完成。"""
    from app.services.line_ops import bubble
    requester = need.requester
    buttons = []
    if requester and tel_uri(requester.phone):
        buttons.append({"label": f"📞 撥打 {requester.name}"[:20], "uri": tel_uri(requester.phone), "color": "#c0392b"})
    if _navigate(need):
        buttons.append({"label": "🧭 導航", "uri": _navigate(need)})
    buttons.append({"label": "📍 已到場", "data": f"action=sos_arrived&need_id={need.id}"})
    buttons.append({"label": "✅ 處理完成", "data": f"action=sos_done&need_id={need.id}"})
    return bubble("🆘 由您處理這筆求救", "#c0392b",
                  [f"當事人：{requester.name if requester else '未知'}",
                   f"地點：{need.address or '見導航'}",
                   f"狀況：{need.description or '未說明'}",
                   *([_aed_line(need)] if _aed_line(need) else []),
                   "到了按「已到場」，確認安全後按「處理完成」。需要送醫請撥 119。"], buttons)


def take(db: Session, need_id: str, user: User, *, via: str) -> dict:
    """受理：第一個人成功，之後的人拿到「已經有人在處理」。

    用條件式 UPDATE（responder_id 還是空的才寫入），兩個人同時按也只會有一個成功。"""
    from app.services.alert import notify_admins
    from app.services.dispatch import _log_dispatch_event, notify_requester
    taken = db.query(CommunityNeed).filter(
        CommunityNeed.id == str(need_id), CommunityNeed.need_type == "sos",
        CommunityNeed.status == "open", CommunityNeed.responder_id.is_(None),
    ).update({"responder_id": str(user.id), "acknowledged_at": now_utc().replace(tzinfo=None)},
             synchronize_session=False)
    need = db.query(CommunityNeed).filter(CommunityNeed.id == str(need_id)).first()
    if need is None or need.need_type != "sos":
        db.rollback()
        return {"error": "找不到這筆求救"}
    if taken != 1:
        db.rollback()
        db.refresh(need)
        if need.status != "open":
            return {"closed": True, "message": "這筆求救已經處理完成了，謝謝您。"}
        if str(need.responder_id) == str(user.id):
            return {"ok": True, "need": need, "already_mine": True}
        who = need.responder.name if need.responder else "其他人"
        return {"taken_by": who, "message": f"已經有 {who} 在處理了，謝謝您的熱心 🙏"}
    role = "管理員" if user.has_role("admin") else "志工"
    _log_dispatch_event(db, "sos_acknowledged", need=need, actor_id=str(user.id), actor_label=f"{role}:{user.name}",
                        previous_status="open", new_status="open", outcome="acknowledged", details={"via": via})
    db.commit()
    db.refresh(need)
    notify_requester(need, f"🙋 {role} {user.name} 已經在處理您的求救，會盡快聯絡您或過去看您。"
                           "有生命危險請直接撥 119。")
    elder = need.requester.name if need.requester else "居民"
    try:
        notify_admins(db, f"🙋 {role} {user.name} 已受理 {elder} 的求救（{via}）。")
    except Exception:
        logger.warning("notify admins about SOS acknowledgement failed", exc_info=True)
    _tell_family(db, need, f"🙋 {role} {user.name} 已經要去看 {elder}，有消息會再通知您。")
    return {"ok": True, "need": need}


def escalate_unacknowledged(db: Session | None = None) -> int:
    """排程每 2 分鐘呼叫：超過 ESCALATE_MINUTES 還沒人受理的求救，再推一次給管理員（每筆只推一次）。"""
    from datetime import timedelta
    from app.database import SessionLocal
    from app.models.dispatch_event import DispatchEvent
    from app.services.alert import notify_admins
    from app.services.dispatch import _log_dispatch_event
    own = db is None
    db = db or SessionLocal()
    try:
        cutoff = now_utc().replace(tzinfo=None) - timedelta(minutes=ESCALATE_MINUTES)
        stale = db.query(CommunityNeed).filter(
            CommunityNeed.need_type == "sos", CommunityNeed.status == "open",
            CommunityNeed.responder_id.is_(None), CommunityNeed.created_at <= cutoff).all()
        done = {str(e.need_id) for e in db.query(DispatchEvent).filter(
            DispatchEvent.action == "sos_escalated",
            DispatchEvent.need_id.in_([str(n.id) for n in stale])).all()} if stale else set()
        sent = 0
        for need in stale:
            if str(need.id) in done:
                continue
            elder = need.requester.name if need.requester else "居民"
            buttons = [{"label": "🙋 我來處理", "data": f"action=sos_take&need_id={need.id}", "color": "#c0392b"}]
            if need.requester and tel_uri(need.requester.phone):
                buttons.insert(0, {"label": f"📞 撥打 {elder}"[:20], "uri": tel_uri(need.requester.phone)})
            notify_admins(db, f"⚠️ {elder} 的求救已經超過 {ESCALATE_MINUTES} 分鐘沒有人受理！\n"
                              f"地點：{need.address or '未知'}。請立刻聯繫或指派人過去。", buttons=buttons)
            _log_dispatch_event(db, "sos_escalated", need=need, actor_label="系統",
                                previous_status="open", new_status="open", outcome="escalated",
                                details={"minutes": ESCALATE_MINUTES})
            db.commit()
            sent += 1
        sent += _remind_overdue_arrivals(db)
        return sent
    finally:
        if own:
            db.close()


def _remind_overdue_arrivals(db: Session) -> int:
    """受理超過 ARRIVAL_MINUTES 還沒回報到場的求救，提醒管理員一次。"""
    from datetime import timedelta
    from app.models.dispatch_event import DispatchEvent
    from app.services.alert import notify_admins
    from app.services.dispatch import _log_dispatch_event
    cutoff = now_utc().replace(tzinfo=None) - timedelta(minutes=ARRIVAL_MINUTES)
    taken = db.query(CommunityNeed).filter(
        CommunityNeed.need_type == "sos", CommunityNeed.status == "open",
        CommunityNeed.responder_id.isnot(None), CommunityNeed.acknowledged_at <= cutoff).all()
    if not taken:
        return 0
    seen = {(str(e.need_id), e.action) for e in db.query(DispatchEvent).filter(
        DispatchEvent.action.in_(("sos_on_scene", "sos_arrival_overdue")),
        DispatchEvent.need_id.in_([str(n.id) for n in taken])).all()}
    sent = 0
    for need in taken:
        if (str(need.id), "sos_on_scene") in seen or (str(need.id), "sos_arrival_overdue") in seen:
            continue
        elder = need.requester.name if need.requester else "居民"
        who = need.responder.name if need.responder else "處理人"
        buttons = []
        if need.responder and tel_uri(need.responder.phone):
            buttons.append({"label": f"📞 撥打 {who}"[:20], "uri": tel_uri(need.responder.phone), "color": "#c26a12"})
        if need.requester and tel_uri(need.requester.phone):
            buttons.append({"label": f"📞 撥打 {elder}"[:20], "uri": tel_uri(need.requester.phone)})
        notify_admins(db, f"⏱️ {who} 受理 {elder} 的求救已超過 {ARRIVAL_MINUTES} 分鐘，還沒回報到場。\n"
                          "請確認是否被耽擱，需要時改派其他人。", buttons=buttons or None)
        _log_dispatch_event(db, "sos_arrival_overdue", need=need, actor_label="系統", previous_status="open",
                            new_status="open", outcome="reminded", details={"minutes": ARRIVAL_MINUTES})
        db.commit()
        sent += 1
    return sent


def welfare_check(db: Session, elder: User, family: User, *, on_site: bool = False) -> dict:
    """家屬聯絡不到長輩：請附近志工去看看。

    沒有智慧型手機、或昏倒按不了求救的長者，靠家屬發現「怎麼都沒接電話」。走跟一鍵求救同一條路
    （附近志工、後台響鈴、受理、結案），所以不另外做一套；誰提出的記在事件紀錄，受理與結案時通知他。
    on_site：志工點名上門發現需要協助，人已經在現場，不用再叫別的志工，也不用回頭通知他自己。"""
    from app.services.alert import notify_admins
    from app.services.dispatch import _log_dispatch_event
    from app.services.zones import resolve_zone_for_point
    existing = db.query(CommunityNeed).filter(CommunityNeed.requester_id == elder.id, CommunityNeed.need_type == "sos",
                                              CommunityNeed.status == "open").first()
    if existing:
        return {"existing": True, "need": existing,
                "responder": existing.responder.name if existing.responder_id and existing.responder else None}
    need = CommunityNeed(requester_id=elder.id, need_type="sos", urgency=5,
                         description=(f"志工 {family.name} 上門確認：需要協助" if on_site
                                      else f"家屬 {family.name} 聯絡不到，請附近志工去看看"),
                         address=elder.address, lat=elder.lat, lng=elder.lng,
                         zone_id=resolve_zone_for_point(db, elder.lat, elder.lng))
    db.add(need)
    db.commit()
    db.refresh(need)
    _log_dispatch_event(db, "welfare_check_requested", need=need, actor_id=str(family.id),
                        actor_label=f"{'志工' if on_site else '家屬'}:{family.name}", new_status="open",
                        outcome="requested", details={} if on_site else {"family_line_uid": family.line_uid})
    db.commit()
    nearby = 0 if on_site else alert_nearby(db, need)
    buttons = [{"label": "🙋 我來處理", "data": f"action=sos_take&need_id={need.id}", "color": "#c0392b"}]
    if tel_uri(elder.phone):
        buttons.insert(0, {"label": f"📞 撥打 {elder.name}"[:20], "uri": tel_uri(elder.phone)})
    headline = (f"🆘 志工 {family.name} 上門確認 {elder.name} 需要協助。" if on_site
                else f"👀 家屬 {family.name} 聯絡不到 {elder.name}，請人去看看。")
    admins = notify_admins(db, f"{headline}\n地點：{elder.address or '未填'}", buttons=buttons)
    return {"nearby": nearby, "admins": admins, "need": need}


def _tell_family(db: Session, need: CommunityNeed, text: str) -> None:
    """家屬請人去看看的案子，受理與結案時要讓提出的家屬知道。"""
    import hashlib
    import json
    from app.models.dispatch_event import DispatchEvent
    from app.services.outbox import send_text_reliably
    event = db.query(DispatchEvent).filter(DispatchEvent.need_id == str(need.id),
                                           DispatchEvent.action == "welfare_check_requested").first()
    uid = json.loads(event.details_json or "{}").get("family_line_uid") if event else None
    if uid:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        send_text_reliably(aggregate_type="CommunityNeed", aggregate_id=str(need.id), destination=uid,
                           content=text, dedupe_key=f"welfare-family:{need.id}:{digest}")


def arrive(db: Session, need_id: str, user: User) -> dict:
    """處理人回報已到場：後台與提出的家屬知道人到了，時間軸記下到場時間（每筆只記第一次）。"""
    from app.models.dispatch_event import DispatchEvent
    from app.services.alert import notify_admins
    from app.services.dispatch import _log_dispatch_event
    need = db.query(CommunityNeed).filter(CommunityNeed.id == str(need_id)).first()
    if need is None or need.need_type != "sos":
        return {"error": "找不到這筆求救"}
    if need.status != "open":
        return {"error": "這筆求救已經結案了。"}
    if not user.has_role("admin") and str(need.responder_id) != str(user.id):
        return {"error": "只有受理這筆求救的人可以回報到場。"}
    if db.query(DispatchEvent).filter(DispatchEvent.need_id == str(need.id), DispatchEvent.action == "sos_on_scene").first():
        return {"ok": True, "already": True}
    role = "管理員" if user.has_role("admin") else "志工"
    _log_dispatch_event(db, "sos_on_scene", need=need, actor_id=str(user.id), actor_label=f"{role}:{user.name}",
                        previous_status="open", new_status="open", outcome="on_scene", details={})
    db.commit()
    elder = need.requester.name if need.requester else "居民"
    try:
        notify_admins(db, f"📍 {role} {user.name} 已到 {elder} 身邊。")
    except Exception:
        logger.warning("notify admins about arrival failed", exc_info=True)
    _tell_family(db, need, f"📍 {role} {user.name} 已經到 {elder} 身邊了。")
    return {"ok": True}


def finish(db: Session, need_id: str, user: User) -> dict:
    """處理人回報處理完成：結案並讓管理員知道。只有處理人本人或管理員可以結。"""
    from app.services.alert import notify_admins
    from app.services.dispatch import resolve_sos
    need = db.query(CommunityNeed).filter(CommunityNeed.id == str(need_id)).first()
    if need is None or need.need_type != "sos":
        return {"error": "找不到這筆求救"}
    if not user.has_role("admin") and str(need.responder_id) != str(user.id):
        return {"error": "只有受理這筆求救的人或管理員可以結案。"}
    role = "管理員" if user.has_role("admin") else "志工"
    result = resolve_sos(str(need.id), db, actor_label=f"{role}:{user.name}")
    if result.get("error") or result.get("already_resolved"):
        return result
    elder = need.requester.name if need.requester else "居民"
    if role == "志工":
        try:
            notify_admins(db, f"✅ 志工 {user.name} 回報 {elder} 的求救已處理完成。")
        except Exception:
            logger.warning("notify admins about SOS finish failed", exc_info=True)
    _tell_family(db, need, f"✅ {role} {user.name} 回報：{elder} 的狀況已處理完成。")
    return result
