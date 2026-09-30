"""災時點名看板：誰平安、誰需要協助、誰還沒回。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.security import require_admin
from app.services import rollcall

router = APIRouter()


@router.get("")
def board(db: Session = Depends(get_db), _principal: dict | None = Depends(require_admin)):
    return rollcall.board(db)


def _taiwan_time(iso: str | None) -> str:
    """表格給公所的人看：換成台灣時間。"""
    from datetime import datetime, timedelta, timezone
    if not iso:
        return ""
    return datetime.fromisoformat(iso).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")


@router.get("/export.csv")
def export_csv(db: Session = Depends(get_db), _principal: dict | None = Depends(require_admin)):
    """點名名單匯出成 CSV（Excel 開得起來的 UTF-8 BOM）：公所或應變中心要用試算表彙整時用。"""
    from app.csv_export import csv_response
    board_ = rollcall.board(db)
    if not board_.get("active"):
        last = rollcall.last_round(db)
        board_ = rollcall.board(db, last) if last else {"people": []}
    via = {"line": "LINE", "app": "App", "family": "家屬", "console": "後台", "volunteer": "志工上門"}
    return csv_response(["姓名", "狀態", "脆弱度", "電話", "地址", "回報方式", "代為確認", "回報時間"],
                        ([p["name"], p["status_label"], p["vulnerability"], p["phone"], p["address"],
                          via.get(p["via"], p["via"] or ""), p["marked_by"] or "", _taiwan_time(p["responded_at"])]
                         for p in board_["people"]), "rollcall.csv")


@router.post("/remind")
def remind(db: Session = Depends(get_db), _principal: dict | None = Depends(require_admin)):
    """再問一次還沒回的長者。"""
    if not rollcall.current_round(db):
        raise HTTPException(409, "目前不是緊急模式，沒有進行中的點名。")
    sent = len(rollcall.ask(db, only_pending=True))
    return {"sent": sent, "message": f"已再問 {sent} 位還沒回的長者" if sent else "還沒回的長者都沒有綁定 LINE，請電話聯繫或上門確認"}


@router.post("/{user_id}")
def mark(user_id: str, status: str = "ok", db: Session = Depends(get_db),
         principal: dict | None = Depends(require_admin)):
    """後台打電話或志工上門確認後代為標記。"""
    if status not in ("ok", "unwell", "help"):
        raise HTTPException(422, "status 只能是 ok、unwell 或 help")
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(404, "找不到這位長者")
    who = f"後台：{principal['name']}" if principal else "後台"
    if not rollcall.note(db, user, status, via="console", marked_by=who):
        raise HTTPException(409, "目前不是緊急模式，或這個人不是長者。")
    if status == "help":
        # 需要協助不只是記一筆：開求救單，附近志工會收到、後台會響鈴
        from app.services import sos
        result = sos.open_from_console(db, user, who)
        if result.get("existing"):
            return {"message": f"已標記 {user.name}：需要協助（這位長者已經有求救單在處理）"}
        nearby = result.get("nearby") or 0
        return {"message": f"已標記 {user.name}：需要協助，並開了求救單" + (f"，已通知附近 {nearby} 位志工" if nearby else "，請指派處理人")}
    return {"message": f"已標記 {user.name}：{rollcall.STATUS_LABEL[status]}"}
