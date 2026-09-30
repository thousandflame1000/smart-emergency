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


@router.get("/export.csv")
def export_csv(db: Session = Depends(get_db), _principal: dict | None = Depends(require_admin)):
    """點名名單匯出成 CSV（Excel 開得起來的 UTF-8 BOM）：公所或應變中心要用試算表彙整時用。"""
    import csv
    import io
    from fastapi.responses import Response
    board_ = rollcall.board(db)
    if not board_.get("active"):
        last = rollcall.last_round(db)
        board_ = rollcall.board(db, last) if last else {"people": []}
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["姓名", "狀態", "脆弱度", "電話", "地址", "回報方式", "代為確認", "回報時間（UTC）"])
    via = {"line": "LINE", "app": "App", "family": "家屬", "console": "後台", "volunteer": "志工上門"}
    for p in board_["people"]:
        writer.writerow([p["name"], p["status_label"], p["vulnerability"], p["phone"], p["address"],
                         via.get(p["via"], p["via"] or ""), p["marked_by"] or "", p["responded_at"] or ""])
    return Response("\ufeff" + buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=rollcall.csv"})


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
    return {"message": f"已標記 {user.name}：{rollcall.STATUS_LABEL[status]}"}
