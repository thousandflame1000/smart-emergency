# -*- coding: utf-8 -*-
"""三個角色入口與其 LINE 操作：居民求助、志工執行、決策者處置。

家屬不是第四個主選單，而是居民入口內依照照護關係展開的功能。

指令文字由 handle_text 接手，按鈕由 handle_postback 接手；兩者都回傳 True 代表已處理。
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.care_relation import CareRelation
from app.models.checkin import DailyCheckin
from app.models.config import SystemConfig
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.user import User
from app.services import dispatch
from app.services.line_notify import reply_flex_message, reply_text
from app.timeutil import now_utc, today_tw

logger = logging.getLogger(__name__)

VOLUNTEER_COMMANDS = {"我的任務", "任務"}
ADMIN_COMMANDS = {"總覽", "待派", "待派需求", "待審", "待審志工", "求救單", "後台", "開啟後台", "緊急求救"}
FAMILY_COMMANDS = {"邀請家人", "長輩狀況", "家人狀況"}
RESIDENT_COMMANDS = {"我的需求", "進度", "求助進度", "查看進度", "我的紀錄",
                     "我的資料", "個人資料", "修改資料", "刪除我的帳號", "刪除帳號"}
ROLE_CENTER_COMMANDS = {"居民中心", "志工中心", "決策中心"}
MAINTENANCE_COMMANDS = {"更新選單"}
COMMAND_WORDS = (VOLUNTEER_COMMANDS | ADMIN_COMMANDS | FAMILY_COMMANDS | RESIDENT_COMMANDS
                 | ROLE_CENTER_COMMANDS | MAINTENANCE_COMMANDS)
BIND_RE = re.compile(r"^綁定\s*(\d{6})$")
JOIN_RE = re.compile(r"^加入\s*(\d{8})$")
# 打錯位數或夾雜非數字時用這個攔下來，好告訴對方正確格式。
NEAR_CODE_RE = re.compile(r"^(綁定|加入)\s*([0-9A-Za-z\-]{1,16})$")
JOIN_TTL = timedelta(days=7)
JOIN_TTL_ADMIN = timedelta(minutes=30)
MAX_CODE_FAILURES = 5
FAILURE_WINDOW = timedelta(hours=1)
INVITE_TTL = timedelta(hours=24)
CHECKIN_ZH = {"pending": "⏳ 還沒回覆", "ok": "✅ 已回報平安", "help_needed": "🆘 求助中", "no_response": "⚠️ 長時間未回應"}


# ── 共用卡片元件 ─────────────────────────────────────────────────────────────
def bubble(title: str, color: str, lines: list[str], buttons: list[dict] | None = None) -> dict:
    """buttons: {"label", "data"} postback、{"label", "uri"} 連結、{"label", "text"} 訊息。"""
    footer = []
    for i, b in enumerate(buttons or []):
        action = ({"type": "uri", "label": b["label"][:20], "uri": b["uri"]} if "uri" in b else
                  {"type": "message", "label": b["label"][:20], "text": b["text"]} if "text" in b else
                  {"type": "postback", "label": b["label"][:20], "data": b["data"]})
        footer.append({"type": "button", "height": "sm", "style": "primary" if i == 0 else "secondary",
                       **({"color": b.get("color", "#1b7a44")} if i == 0 else {}), "action": action})
    out = {
        "type": "bubble", "size": "kilo",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": color,
                   "contents": [{"type": "text", "text": title, "color": "#ffffff", "weight": "bold"}]},
        "body": {"type": "box", "layout": "vertical", "spacing": "sm",
                 "contents": [{"type": "text", "text": t, "wrap": True, "size": "sm"} for t in lines]},
    }
    if footer:
        out["footer"] = {"type": "box", "layout": "vertical", "spacing": "sm", "contents": footer}
    return out


def carousel(bubbles: list[dict]) -> dict:
    return {"type": "carousel", "contents": bubbles[:10]}


def _flex(event, alt: str, contents: dict) -> None:
    reply_flex_message(event.reply_token, alt, contents)


def _say(event, text: str) -> None:
    reply_text(event.reply_token, text)


def is_admin(user: User) -> bool:
    return bool(user.roles) and "admin" in user.roles


def is_field_staff(user: User) -> bool:
    return bool(user.roles) and "field_staff" in user.roles


def is_volunteer(user: User) -> bool:
    return bool(user.roles) and any(r in user.roles for r in ("volunteer", "admin"))


def _need_label(need_type: str) -> str:
    from app.routers.linebot import NEED_ZH
    return NEED_ZH.get(need_type, need_type)


def _status_label(status: str) -> str:
    from app.routers.linebot import STATUS_ZH
    return STATUS_ZH.get(status, status)


# ── 志工：我的任務 ───────────────────────────────────────────────────────────
def my_tasks(event, db: Session, user: User) -> None:
    from app.services.line_notify import build_task_bubble
    if not is_volunteer(user):
        _say(event, "此功能僅限志工使用。想當志工請傳「我要當志工」。")
        return
    tasks = dispatch.list_my_tasks(user, db)
    if not tasks:
        _say(event, "您目前沒有進行中的任務。點選單的「接單」可以挑需求，管理員派單時也會直接通知您。")
        return
    bubbles = [build_task_bubble(
        user.line_uid, t["description"], t["address"], t["resource_name"], need_id=t["need_id"],
        distance_km=t["dist_km"], dest_lat=t["lat"], dest_lng=t["lng"], accepted=t["accepted"],
    ) for t in tasks]
    _flex(event, f"您有 {len(tasks)} 個進行中的任務", carousel(bubbles))


def resident_center(event, db: Session, user: User) -> None:
    """Keep the resident's main menu small while making secondary paths discoverable."""
    cards = [bubble(
        "居民服務", "#2471a3",
        ["先處理眼前需要；不確定需求類型時，選「申請需求」即可一次填寫。"],
        [{"label": "申請需求", "text": "申請物資"},
         {"label": "查看進度", "text": "我的需求"},
         {"label": "我的資料", "text": "我的資料"}],
    )]
    if "family" in (user.roles or []):
        cards.append(bubble(
            "家庭照護", "#148f77",
            ["您是照護聯絡人，可查看已綁定長輩的每日狀況並確認平安。"],
            [{"label": "長輩狀況", "text": "長輩狀況"},
             {"label": "志工申請", "text": "我要當志工"},
             {"label": "操作說明", "text": "幫助"}],
        ))
    else:
        cards.append(bubble(
            "家庭與加入", "#7f8c8d",
            ["家人可在緊急或未回覆時收到通知；也可申請加入志工。"],
            [{"label": "邀請家人", "text": "邀請家人"},
             {"label": "志工申請", "text": "我要當志工"},
             {"label": "操作說明", "text": "幫助"}],
        ))
    _flex(event, "居民服務", carousel(cards))


def volunteer_center(event, db: Session, user: User) -> None:
    if not is_volunteer(user):
        _say(event, "此功能僅限志工使用。想當志工請傳「我要當志工」。")
        return
    _flex(event, "志工中心", carousel([
        bubble(
            "任務執行", "#2471a3",
            ["接單後，任務卡會保留接單、送達與回報的完整流程。"],
            [{"label": "接單", "text": "接單"},
             {"label": "我的任務", "text": "我的任務"},
             {"label": "登記物資", "text": "登記物資"}],
        ),
        bubble(
            "資源與位置", "#148f77",
            ["物資以您的帳號與類型更新，避免同一資源重複登記。"],
            [{"label": "我的物資", "text": "我的物資"},
             {"label": "取消物資", "text": "取消物資"},
             {"label": "分享位置", "text": "分享位置"},
             {"label": "操作說明", "text": "幫助"}],
        ),
    ]))


# ── 管理員 ───────────────────────────────────────────────────────────────────
def _require_admin(event, user: User) -> bool:
    if not is_admin(user):
        _say(event, "此功能僅限管理員使用。如果您負責社區調度，請聯絡現有管理員把您加入。")
        return False
    return True


def _admin_snapshot(db: Session) -> dict:
    from app.models.volunteer_application import VolunteerApplication
    from app.routers.linebot import _get_mode
    needs = db.query(CommunityNeed).filter(CommunityNeed.status.in_(["open", "suggested", "matched"])).all()
    open_n = sum(1 for n in needs if n.status == "open" and n.need_type != "sos")
    sos_n = sum(1 for n in needs if n.status == "open" and n.need_type == "sos")
    suggested = sum(1 for n in needs if n.status == "suggested")
    matched = [n for n in needs if n.status == "matched"]
    accepted = dispatch.accepted_need_ids(db, [n.id for n in matched])
    apps = db.query(VolunteerApplication).filter(VolunteerApplication.status == "pending").count()
    unanswered = db.query(DailyCheckin).filter(DailyCheckin.date == today_tw(),
                                               DailyCheckin.status.in_(["pending", "no_response"])).count()
    helping = db.query(DailyCheckin).filter(DailyCheckin.date == today_tw(),
                                            DailyCheckin.status == "help_needed").count()
    unwell = db.query(DailyCheckin).filter(DailyCheckin.date == today_tw(),
                                           DailyCheckin.status == "unwell").count()
    return {
        "mode": "🚨 緊急模式" if _get_mode(db) == "emergency" else "🟢 日常模式",
        "open": open_n,
        "sos": sos_n,
        "suggested": suggested,
        "matched": len(matched),
        "accepted": len(accepted),
        "apps": apps,
        "unanswered": unanswered,
        "helping": helping,
        "unwell": unwell,
    }


def admin_overview(event, db: Session, user: User) -> None:
    stats = _admin_snapshot(db)
    _say(event, "\n".join([
        f"📊 目前狀況　{stats['mode']}",
        f"🆘 待處理求救單：{stats['sos']}",
        f"📦 待派遣需求：{stats['open']}（另有 {stats['suggested']} 筆系統建議待您確認）",
        f"🚚 進行中任務：{stats['matched']}（志工已確認 {stats['accepted']}）",
        f"🙋 待審志工申請：{stats['apps']}",
        f"👴 今日長者：求助 {stats['helping']}、不舒服 {stats['unwell']}、未回覆 {stats['unanswered']}",
        "",
        "指令：待派、待審、求救單、後台",
    ]))


def decision_center(event, db: Session, user: User) -> None:
    stats = _admin_snapshot(db)
    _flex(event, "決策中心", carousel([
        bubble(
            "決策中心", "#2471a3",
            [stats["mode"],
             f"緊急求救 {stats['sos']}｜待派 {stats['open']}｜建議{_status_label('suggested')} {stats['suggested']}",
             f"進行中 {stats['matched']}｜志工待審 {stats['apps']}"],
            [{"label": "緊急求救", "text": "求救單"},
             {"label": "待派需求", "text": "待派"},
             {"label": "完整總覽", "text": "總覽"}],
        ),
        bubble(
            "核准與全貌", "#148f77",
            [f"長者：求助 {stats['helping']}｜不舒服 {stats['unwell']}｜未回覆 {stats['unanswered']}",
             "高風險個案先由緊急求救與待派需求進入處置。"],
            [{"label": "待審志工", "text": "待審"},
             {"label": "開啟後台", "text": "後台"},
             {"label": "操作說明", "text": "幫助"}],
        ),
    ]))


def _top_candidates(need: CommunityNeed, db: Session, limit: int) -> list[dict]:
    found = dispatch.preview_candidates(str(need.id), db).get("candidates", [])
    return [c for c in found if c.get("source") == "resource"][:limit]


def admin_pending_needs(event, db: Session, user: User) -> None:
    needs = (db.query(CommunityNeed).filter(CommunityNeed.status == "open", CommunityNeed.need_type != "sos")
             .order_by(CommunityNeed.urgency.desc(), CommunityNeed.created_at).limit(8).all())
    if not needs:
        _say(event, "目前沒有待派遣的需求 👍")
        return
    bubbles = []
    for n in needs:
        top = _top_candidates(n, db, 1)
        lines = [f"{_need_label(n.need_type)}　緊急度 {n.urgency}", f"地點：{n.address or '地址未填'}"]
        if n.description:
            lines.append(n.description[:50])
        buttons = []
        if top:
            c = top[0]
            dist = f"，{c['dist_km']} km" if c.get("dist_km") is not None else ""
            lines.append(f"最佳候選：{c['vol']}（{c['score']} 分{dist}）")
            buttons.append({"label": f"派給 {c['vol']}"[:20],
                            "data": f"action=admin_match&need_id={n.id}&resource_id={c['id']}"})
            buttons.append({"label": "看其他候選", "data": f"action=admin_cands&need_id={n.id}"})
        else:
            lines.append("⚠️ 目前沒有可用的志工物資")
        bubbles.append(bubble("待派遣", "#c2610a", lines, buttons))
    _flex(event, f"{len(needs)} 筆待派遣需求", carousel(bubbles))


def admin_candidates(event, db: Session, need_id: str) -> None:
    need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    if not need:
        _say(event, "找不到這筆需求。")
        return
    cands = _top_candidates(need, db, 8)
    if not cands:
        _say(event, "這筆需求目前沒有可用的志工物資，請先到後台增補。")
        return
    bubbles = []
    for c in cands:
        dist = f"{c['dist_km']} km" if c.get("dist_km") is not None else "無座標"
        bubbles.append(bubble(
            f"{c['vol']}", "#2471a3",
            [f"物資：{c['name']}", f"評分 {c['score']}　距離 {dist}",
             "會發 LINE 任務卡" if c.get("notify_channel") == "line" else "⚠️ 未綁定 LINE，需自行聯繫"],
            [{"label": "派給這位", "data": f"action=admin_match&need_id={need_id}&resource_id={c['id']}"}]))
    _flex(event, "候選志工", carousel(bubbles))


def admin_pending_apps(event, db: Session, user: User) -> None:
    from app.services.volunteer_application import list_applications
    apps = list_applications(db, status="pending")[:10]
    if not apps:
        _say(event, "目前沒有待審的志工申請 👍")
        return
    _flex(event, f"{len(apps)} 筆待審志工申請", carousel([
        application_bubble(a["id"], a["name"], a["phone"], a["service_area"]) for a in apps]))


def application_bubble(app_id: str, name: str, phone: str | None, area: str | None) -> dict:
    return bubble("🙋 志工申請", "#2471a3",
                  [f"姓名：{name}", f"電話：{phone or '未填'}", f"服務區域：{area or '未填'}"],
                  [{"label": "✅ 核准", "data": f"action=admin_app&id={app_id}&d=approve"},
                   {"label": "婉拒", "data": f"action=admin_app&id={app_id}&d=reject"}])


def admin_sos_list(event, db: Session, user: User) -> None:
    rows = (db.query(CommunityNeed).filter(CommunityNeed.status == "open", CommunityNeed.need_type == "sos")
            .order_by(CommunityNeed.created_at).limit(10).all())
    if not rows:
        _say(event, "目前沒有待處理的求救單 👍")
        return
    _flex(event, f"{len(rows)} 筆求救單", carousel([sos_bubble(str(n.id), n.requester.name if n.requester else "?",
                                                              n.address, n.requester.phone if n.requester else None)
                                                   for n in rows]))


def sos_bubble(need_id: str, name: str, address: str | None, phone: str | None) -> dict:
    buttons = [{"label": "✅ 已聯繫處理", "data": f"action=admin_sos&need_id={need_id}", "color": "#c0392b"}]
    if phone and re.fullmatch(r"[0-9+\-()\s]{7,20}", phone):
        buttons.insert(0, {"label": f"📞 撥打 {phone}"[:20], "uri": "tel:" + re.sub(r"[^0-9+]", "", phone)})
    return bubble("🆘 緊急求助", "#c0392b", [f"當事人：{name}", f"地點：{address or '未知'}", "請立即聯繫，必要時通報 119"],
                  buttons)


def admin_login_link(event, user: User) -> None:
    from app.services.admin_session import login_url
    _flex(event, "後台登入連結", bubble(
        "🔐 後台登入", "#2c3e50",
        [f"{user.name}，點下方按鈕登入管理後台。", "連結 10 分鐘內有效，請勿轉傳給別人。"],
        [{"label": "🔓 登入後台", "uri": login_url(str(user.id))}]))


def _admin_postback(event, db: Session, user: User, action: str, data: dict) -> None:
    label = f"admin:{user.name}"
    if action == "admin_cands":
        admin_candidates(event, db, data.get("need_id", ""))
    elif action == "admin_match":
        result = dispatch.manual_dispatch(data.get("need_id", ""), data.get("resource_id", ""), db,
                                          actor_id=str(user.id), actor_label=label)
        if result.get("error"):
            _say(event, "⚠️ " + result["error"])
        else:
            note = "任務卡已傳給志工" if result.get("volunteer_notified") else "志工未綁定 LINE，請自行聯繫"
            _say(event, f"✅ 已派遣。{note}；求助的人也已收到通知。")
    elif action == "admin_revoke":
        need = db.query(CommunityNeed).filter(CommunityNeed.id == data.get("need_id", "")).first()
        volunteer_uid = None
        if need is not None and need.matched_resource is not None and need.matched_resource.owner is not None:
            volunteer_uid = need.matched_resource.owner.line_uid
        result = dispatch.decline_task_assignment(data.get("need_id", ""), db,
                                                  actor_id=str(user.id), actor_label=label)
        if result.get("already_open") or result.get("error"):
            _say(event, "這筆任務已經不在進行中了（可能已完成、已取消或早就撤銷）。")
        else:
            _say(event, "✅ 已撤銷，需求退回待派遣。求助的人已收到通知，志工的物資已釋放。")
            if volunteer_uid:
                try:
                    from app.services.line_notify import send_text
                    send_text(volunteer_uid, "ℹ️ 管理員調整了任務安排，您剛才接的那一單已撤銷，不需要前往，謝謝您的熱心。")
                except Exception:
                    logger.warning("notify volunteer about revoke failed", exc_info=True)
    elif action == "admin_sos":
        result = dispatch.resolve_sos(data.get("need_id", ""), db, actor_label=label)
        _say(event, "⚠️ " + result["error"] if result.get("error") else
             ("這筆求救單已經處理過了。" if result.get("already_resolved") else "✅ 已標記為聯繫處理，當事人已收到通知。"))
    elif action == "admin_app":
        from fastapi import HTTPException
        from app.services.volunteer_application import decide
        try:
            out = decide(db, data.get("id", ""), decision=data.get("d", ""), reviewer_id=str(user.id))
        except HTTPException as exc:
            _say(event, "⚠️ " + str(exc.detail))
            return
        _say(event, "✅ 已核准，對方已收到通知並換上志工選單。" if data.get("d") == "approve" else "已婉拒，對方已收到通知。")


# ── 綁定碼共用：猜錯太多次就先擋住，避免有人亂猜綁到別人的紀錄 ─────────────────
def _now_naive() -> datetime:
    return now_utc().replace(tzinfo=None)


def _attempt_key(line_uid: str) -> str:
    return f"codefail:{line_uid}"


def _too_many_failures(db: Session, line_uid: str) -> bool:
    row = db.query(SystemConfig).filter(SystemConfig.key == _attempt_key(line_uid)).first()
    if not row:
        return False
    try:
        info = json.loads(row.value)
        started = datetime.fromisoformat(info["since"])
    except (ValueError, KeyError):
        return False
    if _now_naive() - started > FAILURE_WINDOW:
        db.delete(row)
        db.commit()
        return False
    return info.get("n", 0) >= MAX_CODE_FAILURES


def _record_failure(db: Session, line_uid: str) -> None:
    row = db.query(SystemConfig).filter(SystemConfig.key == _attempt_key(line_uid)).first()
    info = {"n": 0, "since": _now_naive().isoformat()}
    if row:
        try:
            info = json.loads(row.value)
        except ValueError:
            pass
    info["n"] = info.get("n", 0) + 1
    payload = json.dumps(info)
    if row:
        row.value = payload
    else:
        db.add(SystemConfig(key=_attempt_key(line_uid), value=payload))
    db.commit()


def _clear_failures(db: Session, line_uid: str) -> None:
    db.query(SystemConfig).filter(SystemConfig.key == _attempt_key(line_uid)).delete(synchronize_session=False)
    db.commit()


LOCKED_MESSAGE = "嘗試錯誤次數太多，請一小時後再試，或請對方重新產生新的綁定碼。"


# ── 管理員預先建立成員 → 對方傳「加入 碼」綁定自己的 LINE ─────────────────────
def _join_key(code: str) -> str:
    return f"join:{code}"


def create_join_code(db: Session, member: User) -> tuple[str, int]:
    """8-digit single-use code that binds a LINE account to this pre-created member.

    Pasting a LINE User ID (33 random characters) into the console is not something people can
    do, so the console makes a code instead and the person sends it to the bot. A code that would
    hand out admin rights only lives 30 minutes."""
    for row in db.query(SystemConfig).filter(SystemConfig.key.like("join:%")).all():
        try:
            info = json.loads(row.value)
        except ValueError:
            info = {}
        expired = datetime.fromisoformat(info.get("exp", "1970-01-01")) < _now_naive()
        if expired or info.get("user_id") == str(member.id):
            db.delete(row)
    ttl = JOIN_TTL_ADMIN if "admin" in (member.roles or []) else JOIN_TTL
    code = f"{secrets.randbelow(90_000_000) + 10_000_000}"
    db.add(SystemConfig(key=_join_key(code), value=json.dumps(
        {"user_id": str(member.id), "exp": (_now_naive() + ttl).isoformat()})))
    db.commit()
    return code, int(ttl.total_seconds() // 60)


def _is_blank_account(db: Session, user: User) -> bool:
    """A resident account that was only just auto-created and has never been used."""
    if (user.roles or []) != ["elderly"] or user.phone or user.address:
        return False
    uid = user.id
    if db.query(CommunityNeed).filter(CommunityNeed.requester_id == uid).count():
        return False
    if db.query(CommunityResource).filter(CommunityResource.owner_id == uid).count():
        return False
    if db.query(CareRelation).filter((CareRelation.elderly_id == uid) | (CareRelation.contact_id == uid)).count():
        return False
    if db.query(DailyCheckin).filter(DailyCheckin.elderly_id == uid, DailyCheckin.status != "pending").count():
        return False
    return True


def join_member(event, db: Session, user: User, code: str) -> None:
    from app.services.user_deletion import UserDeletionBlocked, delete_user_data
    line_uid = user.line_uid
    if _too_many_failures(db, line_uid):
        _say(event, LOCKED_MESSAGE)
        return
    row = db.query(SystemConfig).filter(SystemConfig.key == _join_key(code)).first()
    info = {}
    if row:
        try:
            info = json.loads(row.value)
        except ValueError:
            info = {}
    if not info or datetime.fromisoformat(info["exp"]) < _now_naive():
        if row:
            db.delete(row)
            db.commit()
        _record_failure(db, line_uid)
        _say(event, "這個綁定碼無效或已過期，請請管理員在後台重新產生。")
        return
    target = db.query(User).filter(User.id == info["user_id"]).first()
    if target is None:
        _say(event, "找不到這位成員，請請管理員重新產生綁定碼。")
        return
    if target.line_uid:
        _say(event, "這位成員已經綁定過 LINE 了。")
        return
    if not _is_blank_account(db, user):
        _say(event, "這個 LINE 帳號已經有使用紀錄，不能直接綁定。請先傳「刪除我的帳號」清掉舊帳號，再重新傳一次綁定碼。")
        return
    try:
        delete_user_data(db, user)
    except UserDeletionBlocked as exc:
        _say(event, f"暫時無法綁定：{exc}")
        return
    target = db.query(User).filter(User.id == info["user_id"]).first()
    target.line_uid = line_uid
    db.query(SystemConfig).filter(SystemConfig.key == _join_key(code)).delete(synchronize_session=False)
    db.commit()
    _clear_failures(db, line_uid)
    from app.services.rich_menu import sync_user_menu
    sync_user_menu(target)
    roles = target.roles or []
    hint = ("點選單「決策中心」先看待處理狀況，或傳「後台」取得登入連結。" if "admin" in roles else
            "點選單「志工中心」處理接單、任務與物資。" if "volunteer" in roles else
            "點選單「居民中心」中的「長輩狀況」查看照護對象。" if "family" in roles else
            "選單有「緊急求助、申請需求、查看進度」；每天早上按「我很好」回報平安。")
    _say(event, f"✅ 已綁定為「{target.name}」。{hint}\n選單如果沒換，請重開聊天室。")


# ── 家屬 ────────────────────────────────────────────────────────────────────
def _invite_key(code: str) -> str:
    return f"invite:{code}"


def create_invite(db: Session, elder: User) -> str:
    """One 6-digit code per resident at a time; a new code replaces the old one."""
    for row in db.query(SystemConfig).filter(SystemConfig.key.like("invite:%")).all():
        try:
            info = json.loads(row.value)
        except ValueError:
            info = {}
        expired = datetime.fromisoformat(info.get("exp", "1970-01-01")) < now_utc().replace(tzinfo=None)
        if expired or info.get("elderly_id") == str(elder.id):
            db.delete(row)
    code = f"{secrets.randbelow(900000) + 100000}"
    db.add(SystemConfig(key=_invite_key(code), value=json.dumps({
        "elderly_id": str(elder.id), "exp": (now_utc().replace(tzinfo=None) + INVITE_TTL).isoformat()})))
    db.commit()
    return code


def invite_family(event, db: Session, user: User) -> None:
    from app.services.line_notify import oa_message_link
    code = create_invite(db, user)
    link = oa_message_link(f"綁定 {code}")
    how = (f"把下面這個連結傳給家人，他點開就會跳到機器人聊天室，按送出就綁定好了：\n{link}\n\n"
           f"（連結不能用時，請家人加入官方帳號後傳這句話：綁定 {code}）" if link else
           f"請家人先加入本官方帳號，然後傳這句話給機器人：\n\n綁定 {code}")
    _say(event, f"👨‍👩‍👧 {how}\n\n24 小時內有效，只能用一次。綁定後，您如果沒回報平安或按了求助，家人會收到通知。")


def bind_family(event, db: Session, user: User, code: str) -> None:
    if _too_many_failures(db, user.line_uid):
        _say(event, LOCKED_MESSAGE)
        return
    row = db.query(SystemConfig).filter(SystemConfig.key == _invite_key(code)).first()
    info = {}
    if row:
        try:
            info = json.loads(row.value)
        except ValueError:
            info = {}
    if not info or datetime.fromisoformat(info["exp"]) < now_utc().replace(tzinfo=None):
        if row:
            db.delete(row)
            db.commit()
        _record_failure(db, user.line_uid)
        _say(event, "這個綁定碼無效或已過期，請對方重新傳「邀請家人」取得新的。")
        return
    elder = db.query(User).filter(User.id == info["elderly_id"]).first()
    if elder is None:
        _say(event, "找不到對應的長輩帳號，請對方重新產生綁定碼。")
        return
    if str(elder.id) == str(user.id):
        _say(event, "這是您自己的綁定碼，請把它傳給家人，由家人傳給機器人。")
        return
    if db.query(CareRelation).filter(CareRelation.elderly_id == elder.id, CareRelation.contact_id == user.id).first():
        db.delete(row)
        db.commit()
        _say(event, f"您已經是 {elder.name} 的照護聯絡人了。")
        return
    next_order = (db.query(CareRelation).filter(CareRelation.elderly_id == elder.id).count()) + 1
    db.add(CareRelation(elderly_id=elder.id, contact_id=user.id, relation="family", notify_order=min(next_order, 10)))
    roles = list(user.roles or [])
    if "family" not in roles:
        roles.append("family")
        user.roles = roles
    db.delete(row)
    db.commit()
    from app.services.rich_menu import sync_user_menu
    sync_user_menu(user)
    _say(event, f"✅ 已綁定為 {elder.name} 的家屬。他如果沒回報平安或按了求助，您會第一時間收到通知。\n"
                "點選單的「居民中心」可找到「長輩狀況」，隨時查看他今天的狀況。")
    if elder.line_uid:
        try:
            from app.services.line_notify import send_text
            send_text(elder.line_uid, f"👨‍👩‍👧 {user.name} 已成為您的家屬聯絡人，之後有狀況會通知他。")
        except Exception:
            logger.warning("notify elder about new family failed", exc_info=True)


def elder_status(event, db: Session, user: User) -> None:
    relations = db.query(CareRelation).filter(CareRelation.contact_id == user.id,
                                              CareRelation.is_active == True).all()  # noqa: E712
    if not relations:
        _say(event, "您還沒有綁定的長輩。請長輩傳「邀請家人」取得綁定碼，再傳「綁定 碼」給我。")
        return
    bubbles = []
    for rel in relations[:10]:
        elder = rel.elderly
        checkin = db.query(DailyCheckin).filter(DailyCheckin.elderly_id == elder.id,
                                                DailyCheckin.date == today_tw()).first()
        status = CHECKIN_ZH.get(checkin.status, checkin.status) if checkin else "今天還沒有打卡紀錄"
        buttons = []
        if elder.phone and re.fullmatch(r"[0-9+\-()\s]{7,20}", elder.phone):
            buttons.append({"label": f"📞 撥打 {elder.phone}"[:20], "uri": "tel:" + re.sub(r"[^0-9+]", "", elder.phone),
                            "color": "#2471a3"})
        if checkin and checkin.status != "ok":
            buttons.append({"label": "✅ 我確認他平安", "data": f"action=confirm_safe&checkin_id={checkin.id}"})
        bubbles.append(bubble(f"👴 {elder.name}", "#148f77" if checkin and checkin.status == "ok" else "#c2610a",
                              [status, f"地址：{elder.address or '未填'}"], buttons))
    _flex(event, "長輩今日狀況", carousel(bubbles))


# ── 居民：我的需求 ───────────────────────────────────────────────────────────
def my_needs(event, db: Session, user: User) -> None:
    from app.services.form_token import form_url
    needs = (db.query(CommunityNeed).filter(CommunityNeed.requester_id == user.id)
             .order_by(CommunityNeed.created_at.desc()).limit(5).all())
    if not needs:
        _say(event, "您目前沒有提出過的需求。點選單的「申請需求」可以求助。")
        return
    lines = [f"{_need_label(n.need_type)}　{_status_label(n.status)}" for n in needs]
    active = [n for n in needs if n.status in ("open", "suggested", "matched")]
    buttons = []
    if active:
        buttons.append({"label": "取消進行中的需求", "data": "action=cancel_needs", "color": "#c0392b"})
    buttons.append({"label": "查看完整紀錄", "uri": form_url("me", user.line_uid)})
    _flex(event, "我的需求", bubble("📋 我的需求（最近 5 筆）", "#c0392b", lines, buttons))


def ask_delete_me(event, db: Session, user: User) -> None:
    lines = ["這會永久刪除您在鄰里守望的帳號，以及您的打卡紀錄、需求、登記的物資、家人綁定與志工申請。",
             "刪除後無法復原。之後您再傳訊息，會以全新的一般居民身分重新註冊。"]
    if is_admin(user) and db.query(User).filter(User.role_filter("admin"), User.id != user.id).count() == 0:
        lines.append("⚠️ 您是唯一的管理員，刪除後沒有人能登入後台。")
    _flex(event, "確認刪除帳號", bubble("🗑 刪除我的帳號", "#7f8c8d", lines, [
        {"label": "確定刪除", "data": "action=delete_me", "color": "#c0392b"},
        {"label": "先不要", "data": "action=keep_me"}]))


def delete_me(event, db: Session, user: User) -> None:
    from app.services.user_deletion import UserDeletionBlocked, delete_user_data
    line_uid = user.line_uid
    try:
        delete_user_data(db, user)
    except UserDeletionBlocked as exc:
        _say(event, f"⚠️ 現在還不能刪除：{exc}")
        return
    try:
        from app.services.rich_menu import _apis
        if line_uid:
            _apis()[0].unlink_rich_menu_id_from_user(line_uid)
    except Exception:
        logger.warning("unlink menu after account deletion failed", exc_info=True)
    _say(event, "✅ 您的帳號與相關資料已刪除。之後再傳訊息給我，會重新註冊成新的居民。")


def cancel_my_needs(event, db: Session, user: User) -> None:
    active = db.query(CommunityNeed).filter(CommunityNeed.requester_id == user.id,
                                            CommunityNeed.status.in_(["open", "suggested", "matched"])).all()
    for n in active:
        dispatch.cancel_need(str(n.id), db)
    _say(event, f"已幫您取消 {len(active)} 筆進行中的需求。之後有需要再點選單「申請需求」就可以。"
         if active else "您目前沒有進行中的需求。")


def my_profile_link(event, user: User) -> None:
    """填過一次之後還是要改得了——搬家、換電話都會發生。"""
    from app.services.form_token import form_url
    missing = [n for n, v in (("電話", user.phone), ("地址", user.address)) if not v]
    lines = ["姓名、電話、地址。需要幫忙時，志工靠這些找到您。"]
    if missing:
        lines.append("目前還缺：" + "、".join(missing))
    _flex(event, "我的資料", bubble("📇 我的資料", "#1b7a44", lines,
                                 [{"label": "填寫／修改", "uri": form_url("profile", user.line_uid)}]))


def my_records_link(event, user: User) -> None:
    from app.services.form_token import form_url
    _flex(event, "我的紀錄", bubble("📁 我的紀錄", "#2c3e50",
                                  ["需求紀錄、取消需求、家人綁定，都在這裡。"],
                                  [{"label": "開啟我的紀錄", "uri": form_url("me", user.line_uid)}]))


# ── 入口 ────────────────────────────────────────────────────────────────────
def handle_text(event, db: Session, user: User, text: str) -> bool:
    m = BIND_RE.match(text)
    if m:
        bind_family(event, db, user, m.group(1))
        return True
    m = JOIN_RE.match(text)
    if m:
        join_member(event, db, user, m.group(1))
        return True
    # 明顯是在輸入綁定碼但位數或字元不對時，要直接指出格式；
    # 丟去「我聽不懂，請傳『幫助』」等於讓人重猜一次（Nielsen #9）。
    near = NEAR_CODE_RE.match(text)
    if near:
        word, got = near.group(1), near.group(2)
        digits = "6" if word == "綁定" else "8"
        _say(event, f"「{word}」後面要接 {digits} 位數字，您輸入的是「{got}」。\n"
                    f"請確認後重傳，例如：{word} {'1' * int(digits)}")
        return True
    if text not in COMMAND_WORDS:
        return False
    if text == "居民中心":
        resident_center(event, db, user)
    elif text == "志工中心":
        volunteer_center(event, db, user)
    elif text == "決策中心":
        if _require_admin(event, user):
            decision_center(event, db, user)
    elif text == "更新選單":
        if _require_admin(event, user):
            _say(event, "Rich Menu 重建已移出 LINE 日常操作，以避免刪除、建立與圖片上傳被重複觸發。"
                        "請依受控維運流程單次執行。")
    elif text in VOLUNTEER_COMMANDS:
        my_tasks(event, db, user)
    elif text in ("後台", "開啟後台"):
        # 基層員工只能拿登入連結進網頁更新物資／資源點，其他管理指令仍是管理員專用。
        if is_admin(user) or is_field_staff(user):
            admin_login_link(event, user)
        else:
            _say(event, "此功能僅限管理員或基層員工使用。")
    elif text in ADMIN_COMMANDS:
        if not _require_admin(event, user):
            return True
        if text == "總覽":
            admin_overview(event, db, user)
        elif text in ("待派", "待派需求"):
            admin_pending_needs(event, db, user)
        elif text in ("待審", "待審志工"):
            admin_pending_apps(event, db, user)
        elif text in ("求救單", "緊急求救"):
            admin_sos_list(event, db, user)
        else:
            admin_login_link(event, user)
    elif text == "邀請家人":
        invite_family(event, db, user)
    elif text in ("長輩狀況", "家人狀況"):
        elder_status(event, db, user)
    elif text == "我的紀錄":
        my_records_link(event, user)
    elif text in ("我的資料", "個人資料", "修改資料"):
        my_profile_link(event, user)
    elif text in ("刪除我的帳號", "刪除帳號"):
        ask_delete_me(event, db, user)
    else:
        my_needs(event, db, user)
    return True


def handle_postback(event, db: Session, user: User, action: str, data: dict) -> bool:
    if action.startswith("admin_"):
        if not is_admin(user):
            _say(event, "此功能僅限管理員使用。如果您負責社區調度，請聯絡現有管理員把您加入。")
            return True
        _admin_postback(event, db, user, action, data)
        return True
    if action == "cancel_needs":
        cancel_my_needs(event, db, user)
        return True
    if action == "delete_me":
        delete_me(event, db, user)
        return True
    if action == "keep_me":
        _say(event, "好的，帳號保持原樣。")
        return True
    return False
