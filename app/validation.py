# -*- coding: utf-8 -*-
"""管理端 API 的輸入驗證。之前四個新增端點幾乎什麼都收：緊急度 99 或 -3、空的需求類型、
座標 999、空姓名、角色 "hacker"、自己當自己的照護聯絡人，全都會寫進資料庫。"""
import math

from app.errors import ApiError

USER_ROLES = {"elderly", "volunteer", "family", "admin", "field_staff"}
NEED_TYPES = {"water", "food", "first_aid", "shelter", "vehicle", "tool", "other", "sos"}
RESOURCE_TYPES = {"water", "food", "first_aid", "shelter", "vehicle", "tool", "other"}


def check_coords(lat, lng) -> None:
    if (lat is None) != (lng is None):
        raise ApiError(422, "緯度與經度必須成對提供，或兩者都留空。")
    if lat is None:
        return
    if not (math.isfinite(lat) and math.isfinite(lng)) or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ApiError(422, "座標超出範圍（緯度 -90 到 90、經度 -180 到 180）。")


def check_name(name: str | None, *, what: str = "姓名") -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise ApiError(422, f"{what}不能是空的。")
    if len(cleaned) > 100:
        raise ApiError(422, f"{what}太長（最多 100 字）。")
    return cleaned


def check_roles(roles: list[str]) -> list[str]:
    cleaned = []
    for role in roles:
        role = (role or "").strip()
        if role not in USER_ROLES:
            raise ApiError(422, f"不認得的角色「{role}」，只能是：{'、'.join(sorted(USER_ROLES))}。")
        if role not in cleaned:
            cleaned.append(role)
    if not cleaned:
        raise ApiError(422, "至少要有一個角色。")
    return cleaned


def check_choice(value: str, allowed: set[str], *, what: str) -> str:
    if value not in allowed:
        raise ApiError(422, f"{what}「{value}」不合法，只能是：{'、'.join(sorted(allowed))}。")
    return value


def check_urgency(urgency: int) -> int:
    if not 1 <= urgency <= 5:
        raise ApiError(422, "緊急度必須是 1 到 5。")
    return urgency
