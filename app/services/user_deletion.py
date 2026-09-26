# -*- coding: utf-8 -*-
"""刪除使用者與只屬於他的資料。後台的刪除按鈕和 LINE 上的「刪除我的帳號」共用這一份。"""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.labels import need_status
from app.models.alert import Alert
from app.models.care_relation import CareRelation
from app.models.checkin import DailyCheckin
from app.models.config import SystemConfig
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.user import User
from app.models.volunteer_application import VolunteerApplication


class UserDeletionBlocked(Exception):
    """The user still has live dispatches or audit rows, so deleting would break something."""


# 擋住刪除的狀態，和說明文字裡念出來的詞，用同一份定義長出來。
# 先前這句話寫死「待確認或已派遣」，而那兩個詞在別處早就改叫「待核准」和「執行中」。
BLOCKING_STATUSES = ["suggested", "matched"]


def active_dispatch_count(db: Session, user: User) -> int:
    my_resource_ids = [r.id for r in db.query(CommunityResource.id).filter(CommunityResource.owner_id == user.id)]
    q = db.query(CommunityNeed).filter(CommunityNeed.status.in_(BLOCKING_STATUSES))
    if my_resource_ids:
        q = q.filter((CommunityNeed.requester_id == user.id) | CommunityNeed.matched_resource_id.in_(my_resource_ids))
    else:
        q = q.filter(CommunityNeed.requester_id == user.id)
    return q.count()


def delete_user_data(db: Session, user: User) -> None:
    active = active_dispatch_count(db, user)
    if active:
        raise UserDeletionBlocked(
            f"還有 {active} 筆進行中的派遣"
            f"（{'或'.join(need_status(s) for s in BLOCKING_STATUSES)}），"
            "請先取消或完成後再刪除；也可以改成「停用」保留紀錄。")
    uid, line_uid = user.id, user.line_uid
    try:
        checkin_ids = [c.id for c in db.query(DailyCheckin.id).filter(DailyCheckin.elderly_id == uid)]
        if checkin_ids:
            db.query(Alert).filter(Alert.checkin_id.in_(checkin_ids)).delete(synchronize_session=False)
        db.query(Alert).filter(Alert.elderly_id == uid).delete(synchronize_session=False)
        db.query(Alert).filter(Alert.resolved_by == uid).update({"resolved_by": None}, synchronize_session=False)
        db.query(DailyCheckin).filter(DailyCheckin.confirmed_by == uid).update({"confirmed_by": None}, synchronize_session=False)
        db.query(DailyCheckin).filter(DailyCheckin.elderly_id == uid).delete(synchronize_session=False)
        db.query(CareRelation).filter(
            (CareRelation.elderly_id == uid) | (CareRelation.contact_id == uid)).delete(synchronize_session=False)
        db.query(VolunteerApplication).filter(VolunteerApplication.applicant_id == uid).update(
            {"applicant_id": None}, synchronize_session=False)
        db.query(VolunteerApplication).filter(VolunteerApplication.reviewed_by == uid).update(
            {"reviewed_by": None}, synchronize_session=False)
        db.query(CommunityNeed).filter(CommunityNeed.requester_id == uid).delete(synchronize_session=False)
        db.query(CommunityResource).filter(CommunityResource.owner_id == uid).delete(synchronize_session=False)
        if line_uid:
            db.query(SystemConfig).filter(SystemConfig.key.in_([f"flow:{line_uid}", f"form:{line_uid}"])).delete(
                synchronize_session=False)
        db.delete(user)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise UserDeletionBlocked("仍有派遣或任務的稽核紀錄無法刪除，請改成「停用」以保留紀錄。")
