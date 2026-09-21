"""Explicit, version-checked inventory commands; graph documents are never authoritative."""
from __future__ import annotations

import json
import re
from datetime import datetime, UTC
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.dispatch_event import DispatchEvent
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.resource_point import ResourcePoint
from app.models.user import User
from app.services.record_version import row_predicates, row_version


def quantity_parts(value: str | None) -> tuple[int, str] | None:
    # A count without a unit uses the legacy "份". Never equate boxes with bottles.
    match = re.fullmatch(r"\s*(\d+)\s*([^\d\s,，/／+＋()（）]{0,20})\s*", value or "")
    if not match or int(match[1]) > 1_000_000:
        return None
    return int(match[1]), match[2] or "份"


def editable_values(row) -> dict:
    if isinstance(row, CommunityResource):
        return {"name": row.name, "quantity": row.quantity or "", "lat": row.lat, "lng": row.lng,
                "address": row.address or "", "is_available": bool(row.is_available)}
    return {"lat": row.lat, "lng": row.lng, "address": row.address or ""}


class InventoryValues(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="", min_length=1, max_length=200)
    quantity: str = Field(default="", max_length=80)
    lat: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    lng: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    address: str = Field(default="", max_length=500)
    is_available: bool = True


class InventoryChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str = Field(min_length=1, max_length=180)
    operation: Literal["update", "create"] = "update"
    expected_version: str | None = Field(default=None, max_length=64)
    creation_key: UUID | None = None
    values: InventoryValues
    owner_id: UUID | None = None
    resource_type: Literal["water", "food", "first_aid", "shelter", "vehicle", "tool", "other"] | None = None


class InventoryCommand(BaseModel):
    changes: list[InventoryChange] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_objects(self):
        if len({c.node_id for c in self.changes}) != len(self.changes):
            raise ValueError("同一物件不能重複寫入")
        keys = [c.creation_key for c in self.changes if c.operation == "create"]
        if len(keys) != len(set(keys)):
            raise ValueError("同一來源物資不能重複建立")
        return self


class InventoryConflict(ValueError):
    pass


def _prepare(change: InventoryChange, db: Session):
    values = change.values.model_dump(exclude_unset=True)
    if not values:
        raise ValueError("未指定變更欄位")
    if change.operation == "create":
        if change.node_id.startswith("db:") or not change.owner_id or not change.resource_type or not change.creation_key:
            raise ValueError("新增物資須使用情境物件識別碼，並指定擁有者與品項")
        owner = db.get(User, change.owner_id)
        if not owner or not owner.is_active:
            raise ValueError("物資擁有者不存在或已停用")
        if not (owner.has_role("volunteer") or owner.has_role("admin")):
            raise ValueError("物資提供者必須是志工或管理員")
        if not {"name", "quantity"} <= values.keys():
            raise ValueError("新增物資須填寫名稱與數量單位")
        key = uuid5(NAMESPACE_URL, "smart-emergency:workspace-resource:" + str(change.creation_key))
        row = db.get(CommunityResource, key)
        if row:
            if (str(row.owner_id) == str(change.owner_id) and row.resource_type == change.resource_type
                    and all(editable_values(row).get(k) == v for k, v in values.items())):
                return row, {}, {}, "already_applied"
            raise InventoryConflict("這個物件已登記到資料庫，請重新同步")
        row = CommunityResource(id=key, owner_id=change.owner_id, resource_type=change.resource_type,
                                is_available=True, **{k: v for k, v in values.items() if k != "is_available"})
        row.is_available = values.get("is_available", True)
        before = {}
    else:
        prefix, model = next(((prefix, model) for prefix, model in
                              (("db:res:", CommunityResource), ("db:point:", ResourcePoint))
                              if change.node_id.startswith(prefix)), (None, None))
        if model is None:
            raise ValueError("只允許寫回資料庫物資或資源點位置")
        row = db.get(model, change.node_id[len(prefix):])
        if row is None:
            raise InventoryConflict("物件已刪除，請重新同步")
        if not change.expected_version or change.expected_version != row_version(row):
            raise InventoryConflict("資料庫已有新版本，請重新同步並檢查差異")
        before = editable_values(row)
        if not values.keys() <= before.keys():
            raise ValueError("資源點只允許更新位置與地址")
        if isinstance(row, CommunityResource) and db.query(CommunityNeed.id).filter(
                CommunityNeed.matched_resource_id == row.id,
                CommunityNeed.status.in_(("suggested", "matched"))).first():
            raise InventoryConflict("物資已被待核准或執行中的任務保留，不能修改")
    after = {**before, **values}
    if (after.get("lat") is None) != (after.get("lng") is None):
        raise ValueError("經緯度必須成對提供")
    if "quantity" in values and quantity_parts(values["quantity"]) is None:
        raise ValueError("數量須為非負整數及單一單位，例如 20箱；不接受約數或混合單位")
    if "name" in values and not values["name"].strip():
        raise ValueError("物資名稱不可空白")
    changed = {k: v for k, v in values.items() if before.get(k) != v}
    return row, before, changed, "create" if change.operation == "create" else "update"


def preview_inventory(command: InventoryCommand, db: Session) -> dict:
    changes, conflicts = [], []
    for change in command.changes:
        try:
            row, before, values, action = _prepare(change, db)
            changes.append({"node_id": change.node_id, "label": row.name, "operation": action,
                            "fields": [{"field": key, "before": before.get(key), "after": value}
                                       for key, value in values.items()]})
        except ValueError as exc:
            conflicts.append({"node_id": change.node_id, "reason": str(exc)})
    return {"changes": changes, "conflicts": conflicts, "can_apply": not conflicts}


def apply_inventory(command: InventoryCommand, db: Session, actor: dict | None = None) -> dict:
    results = []
    try:
        prepared = [(change, *_prepare(change, db)) for change in command.changes]
        for change, row, before, values, action in prepared:
            if action == "create":
                db.add(row)
                db.flush()
            elif values:
                model = type(row)
                # Compare every column as well as timestamps; older writers do not all bump timestamps.
                predicates = row_predicates(row)
                timestamp_key = "last_updated" if isinstance(row, CommunityResource) else "updated_at"
                count = db.query(model).filter(*predicates).update(
                    {**values, timestamp_key: datetime.now(UTC).replace(tzinfo=None)}, synchronize_session=False)
                if count != 1:
                    raise InventoryConflict("寫入期間資料已變動，整批變更未套用")
            if values:
                db.add(DispatchEvent(action="workspace_inventory", outcome=action,
                                     resource_id=row.id if isinstance(row, CommunityResource) else None,
                                     actor_id=actor["id"] if actor else None,
                                     actor_label=actor["name"] if actor else "workspace",
                                     details_json=json.dumps({"node_id": change.node_id, "before": before,
                                                              "changes": values}, ensure_ascii=False)))
            prefix = "db:res:" if isinstance(row, CommunityResource) else "db:point:"
            results.append({"node_id": change.node_id, "database_id": prefix + str(row.id), "operation": action})
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise InventoryConflict("物件已由另一個操作建立，請重新同步") from exc
    except Exception:
        db.rollback()
        raise
    return {"applied": results}
