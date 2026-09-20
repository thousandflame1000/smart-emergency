# -*- coding: utf-8 -*-
"""開放資料工作區 ↔ 平台資料庫。

工作區原本是獨立的圖資料，和正式的需求、物資、長者資料完全沒有連結。這裡把資料庫的真實資料
轉成工作區的物件，用固定的 `db:` 識別碼前綴，之後每次同步都是「就地更新」：位置、接駁連線與使用者
拖動過的版面都保留，只有資料庫已經不存在的物件才會移除。分配試算的結果則可以寫回成派遣建議。

對應方式：
  長者／需求者     → 人員（person）。未滿足的需求單掛在需求者身上，成為「需求」紀錄
  志工登記的物資   → 物資（supply），每份物資一筆「供應」紀錄
  社區固定資源點   → 設施（facility），不推定庫存
數量取自登記文字前面的數字（例如「20箱」→ 20），單位統一為「份」，品項用中文名稱，
這樣供應與需求的品項單位才會完全相同、可以互相配對。
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.resource_point import ResourcePoint
from app.models.user import User
from app.services.workspace import GraphDocument, LogisticsRecord, Node

PREFIX = "db:"
UNIT = "份"
ITEM_ZH = {"water": "飲用水", "demo_water": "飲用水", "food": "食物", "first_aid": "急救用品", "shelter": "庇護所",
           "vehicle": "交通工具", "tool": "工具", "other": "其他物資"}
DEMAND_STATUSES = ("open", "suggested")


def is_db_id(value: str) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def _quantity(text) -> int:
    match = re.match(r"\s*(\d+)", text or "")
    return min(int(match.group(1)), 1_000_000) if match else 1


def _item(kind: str) -> str:
    return ITEM_ZH.get(kind, kind or "其他物資")


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


def database_nodes(db: Session) -> tuple[list[Node], dict]:
    from app.services.dispatch import _vulnerability_pts
    stamp = _stamp()
    nodes: dict[str, Node] = {}

    def person(user: User) -> Node:
        node_id = f"{PREFIX}person:{user.id}"
        if node_id not in nodes:
            nodes[node_id] = Node(
                id=node_id, label=user.name or "未命名", kind="person", lat=user.lat, lng=user.lng,
                quantity=1, available=user.is_active is not False, source=f"平台資料庫（{stamp}）",
                properties={"db": "user", "roles": list(user.roles or []), "address": user.address or "",
                            "vulnerability": round(_vulnerability_pts(user.id, db), 1) if "elderly" in (user.roles or []) else None})
        return nodes[node_id]

    elders = 0
    for user in db.query(User).filter(User.is_active != False).all():  # noqa: E712
        if "elderly" in (user.roles or []) and user.lat is not None:
            person(user)
            elders += 1

    demands = 0
    for need in db.query(CommunityNeed).filter(CommunityNeed.status.in_(DEMAND_STATUSES),
                                               CommunityNeed.need_type != "sos").all():
        requester = need.requester
        if requester is None:
            continue
        node = person(requester)
        if len(node.logistics) >= 30:
            continue
        node.logistics.append(LogisticsRecord(
            id=f"{PREFIX}need:{need.id}", role="demand", item=_item(need.need_type), unit=UNIT,
            quantity=_quantity(need.quantity), priority=min(max(need.urgency or 3, 1), 5),
            source=f"平台資料庫需求單（{'系統建議待確認' if need.status == 'suggested' else '待媒合'}）"))
        demands += 1

    supplies = 0
    for res in db.query(CommunityResource).all():
        node_id = f"{PREFIX}res:{res.id}"
        owner = res.owner.name if res.owner else "?"
        nodes[node_id] = Node(
            id=node_id, label=f"{res.name}（{owner}）", kind="supply", lat=res.lat, lng=res.lng,
            quantity=_quantity(res.quantity), available=bool(res.is_available), source=f"平台資料庫志工物資（{stamp}）",
            properties={"db": "resource", "owner": owner, "address": res.address or ""},
            logistics=[LogisticsRecord(id=node_id, role="supply", item=_item(res.resource_type), unit=UNIT,
                                       quantity=_quantity(res.quantity), source="平台資料庫志工物資")])
        supplies += 1

    points = 0
    for point in db.query(ResourcePoint).filter(ResourcePoint.is_active == True).all():  # noqa: E712
        node_id = f"{PREFIX}point:{point.id}"
        nodes[node_id] = Node(
            id=node_id, label=point.name, kind="facility", lat=point.lat, lng=point.lng, quantity=0,
            available=True, source=f"平台資料庫資源點（{point.source or 'manual'}）",
            properties={"db": "point", "point_type": point.point_type, "address": point.address or "",
                        "capacity": point.capacity, "current_load": point.current_load})
        points += 1

    return list(nodes.values()), {"elders": elders, "demands": demands, "supplies": supplies, "points": points}


def merge_database(graph: GraphDocument, db: Session) -> tuple[GraphDocument, dict]:
    """Update the workspace in place: keep layout, access edges and every non-database object."""
    fresh, counts = database_nodes(db)
    fresh_by_id = {n.id: n for n in fresh}
    old_ids = {n.id for n in graph.nodes if is_db_id(n.id)}
    removed = old_ids - fresh_by_id.keys()

    kept: list[Node] = []
    for node in graph.nodes:
        if node.id in removed:
            continue
        if node.id in fresh_by_id:
            new = fresh_by_id[node.id]
            layout = node.properties.get("_layout")
            new.properties = {**new.properties, **({"_layout": layout} if layout else {})}
            kept.append(new)
        else:
            kept.append(node)
    added = [n for n in fresh if n.id not in old_ids]
    merged = GraphDocument(
        nodes=kept + added,
        edges=[e for e in graph.edges if e.source not in removed and e.target not in removed],
    )
    counts.update(added=len(added), updated=len(old_ids & fresh_by_id.keys()), removed=len(removed))
    return merged, counts
