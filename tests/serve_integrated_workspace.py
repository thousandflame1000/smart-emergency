"""Isolated, local-only integration preview. No production credentials or scheduler."""
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(DATABASE_URL="sqlite:///" + (ROOT / ".workspace-preview.db").as_posix(),
                  APP_ENV="development", LINE_CHANNEL_ACCESS_TOKEN="local-preview-disabled",
                  LINE_CHANNEL_SECRET="local-preview-disabled", GEMINI_API_KEY="local-preview-disabled",
                  ADMIN_LINE_LOGIN="false", DEMO_PASSWORD="", TASK_WORKFLOW_V2="false")

from app.main import app
from app.database import Base, SessionLocal, engine
from app.models.user import User
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.resource_point import ResourcePoint
from app.models.care_relation import CareRelation
from app.models.checkin import DailyCheckin
from app.models.workspace import TopologyWorkspace
from app.services.workspace_bridge import merge_database
from app.services.workspace import Edge, GraphDocument, Node
from app.services.dispatch import propose_manual
from app.routers.workspace import WorkspaceWrite, create_workspace


def initialize(reset=False):
    if reset:
        assert engine.url.database.replace("\\", "/") == (ROOT / ".workspace-preview.db").as_posix()
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if db.query(User).first():
            return
        elder = User(name="林秀英", roles=["elderly"], lat=24.01, lng=120.61, address="社區東側 12 號")
        volunteer = User(name="陳志明", roles=["volunteer"], lat=24.00, lng=120.60, address="社區物資站")
        family = User(name="林佳惠", roles=["family"], lat=24.008, lng=120.607)
        other = User(name="黃國雄", roles=["elderly"], lat=24.005, lng=120.61, address="活動中心旁")
        db.add_all([elder, volunteer, family, other]); db.flush()
        water = CommunityResource(owner_id=volunteer.id, resource_type="water", name="飲用水整箱", quantity="20箱", lat=24, lng=120.60)
        food = CommunityResource(owner_id=volunteer.id, resource_type="food", name="常溫餐食", quantity="30份", lat=24, lng=120.60)
        need = CommunityNeed(requester_id=elder.id, need_type="water", quantity="5箱", urgency=4, lat=24.01, lng=120.61, description="停水，需補充飲用水", status="open")
        waiting = CommunityNeed(requester_id=other.id, need_type="food", quantity="2份", urgency=3, lat=24.005, lng=120.61, status="open")
        db.add_all([water, food, need, waiting,
                    CommunityNeed(requester_id=other.id, need_type="sos", description="需聯繫確認身體狀況", urgency=5, status="open"),
                    CareRelation(elderly_id=elder.id, contact_id=family.id, relation="family"),
                    CareRelation(elderly_id=other.id, contact_id=volunteer.id, relation="volunteer"),
                    DailyCheckin(elderly_id=elder.id, date=date.today(), status="ok", note="平安，家中停水"),
                    DailyCheckin(elderly_id=other.id, date=date.today(), status="help_needed"),
                    ResourcePoint(name="社區活動中心", point_type="community", lat=24.005, lng=120.605, capacity=80, current_load=12)])
        db.commit()
        propose_manual(str(waiting.id), str(food.id), db, actor_label="本機驗證")
        graph, _ = merge_database(GraphDocument(), db)
        tracked_need = next(n for n in graph.nodes if n.properties.get("db") == "need")
        graph.nodes.append(Node(
            id="incident-local",
            label="本機驗證事件",
            kind="incident",
            lat=24.006,
            lng=120.606,
            source="本機驗證",
            properties={"object_type": "incident", "status": "active"},
        ))
        graph.edges.append(Edge(
            id="incident-focus",
            source="incident-local",
            target=tracked_need.id,
            kind="custom",
            label="事件追蹤",
            directed=True,
            provenance="本機驗證",
        ))
        create_workspace(WorkspaceWrite(name="事件處置 · 本機驗證資料", graph=graph), db)


if __name__ == "__main__":
    import uvicorn
    initialize()
    uvicorn.run(app, host="127.0.0.1", port=8766, lifespan="off")
