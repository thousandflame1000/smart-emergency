# -*- coding: utf-8 -*-
import json
from datetime import date

from app.models.alert import Alert
from app.models.care_relation import CareRelation
from app.models.checkin import DailyCheckin
from app.models.dispatch_event import DispatchEvent
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.resource_point import ResourcePoint
from app.models.user import User
from app.services import ontology


def _seed_ontology_demo(db):
    elder = User(name="Alice", roles=["elderly"], lat=24.151, lng=120.681)
    contact = User(name="Bob", roles=["family"], lat=24.151, lng=120.681)
    volunteer = User(name="Casey", roles=["volunteer"], lat=24.150, lng=120.680)
    db.add_all([elder, contact, volunteer])
    db.commit()

    need = CommunityNeed(
        requester_id=elder.id,
        need_type="water",
        description="Needs drinking water",
        urgency=4,
        status="suggested",
        lat=24.151,
        lng=120.681,
    )
    resource = CommunityResource(
        owner_id=volunteer.id,
        resource_type="water",
        name="Bottled water",
        lat=24.150,
        lng=120.680,
        is_available=False,
    )
    db.add_all([need, resource])
    db.commit()
    need.matched_resource_id = resource.id

    db.add(ResourcePoint(name="Shelter A", point_type="shelter", capacity=50, current_load=7))
    db.add(Alert(elderly_id=elder.id, alert_type="no_response_3h", status="sent"))
    db.add(DailyCheckin(elderly_id=elder.id, date=date.today(), status="no_response"))
    db.add(CareRelation(elderly_id=elder.id, contact_id=contact.id, relation="family", notify_order=1))
    event = DispatchEvent(
        action="confirm_dispatch",
        outcome="matched",
        need_id=need.id,
        resource_id=resource.id,
        actor_label="manager",
        previous_status="suggested",
        new_status="matched",
        details_json=json.dumps({"volunteer_notified": True}),
    )
    db.add(event)
    db.commit()
    return elder, contact, volunteer, need, resource, event


def test_schema_exposes_ontology_primitives():
    schema = ontology.schema()

    assert "Person" in schema["object_types"]
    assert "ResourceRequest" in schema["object_types"]
    assert "DecisionEvent" in schema["object_types"]
    assert "RoadNode" in schema["object_types"]
    assert "RoadEdge" in schema["object_types"]
    assert any(link["id"] == "MATCHED_TO" for link in schema["link_types"])
    assert any(link["id"] == "ACTION_ON" for link in schema["link_types"])
    assert any(link["id"] == "ROAD_CONNECTS" for link in schema["link_types"])
    assert any(action["id"] == "confirm_dispatch" for action in schema["actions"])
    assert any(action["id"] == "mutate_road_topology" for action in schema["actions"])
    assert any(action["id"] == "inspect_operational_risks" for action in schema["actions"])
    assert any(action["id"] == "task_delivered" for action in schema["actions"])
    assert any(action["id"] == "task_decline" for action in schema["actions"])
    assert any(fn["id"] == "batch_assignment" for fn in schema["functions"])
    assert any(fn["id"] == "operational_reasoning" for fn in schema["functions"])


def test_graph_links_people_requests_resources_and_alerts(db):
    elder, contact, volunteer, need, resource, event = _seed_ontology_demo(db)

    graph = ontology.graph(db)
    edge_ids = {edge["id"] for edge in graph["edges"]}
    node_ids = {node["id"] for node in graph["nodes"]}

    assert f"Person:{elder.id}" in node_ids
    assert f"ResourceRequest:{need.id}" in node_ids
    assert f"Resource:{resource.id}" in node_ids
    assert f"DecisionEvent:{event.id}" in node_ids
    assert f"REQUESTED_BY:ResourceRequest:{need.id}->Person:{elder.id}" in edge_ids
    assert f"MATCHED_TO:ResourceRequest:{need.id}->Resource:{resource.id}" in edge_ids
    assert f"OWNED_BY:Resource:{resource.id}->Person:{volunteer.id}" in edge_ids
    assert f"CARE_CONTACT:Person:{elder.id}->Person:{contact.id}" in edge_ids
    assert f"ACTION_ON:DecisionEvent:{event.id}->ResourceRequest:{need.id}" in edge_ids
    assert f"ACTION_RESOURCE:DecisionEvent:{event.id}->Resource:{resource.id}" in edge_ids
    assert "RoadNode:yuli" in node_ids
    assert any(edge.startswith("ROAD_CONNECTS:RoadEdge:changbin__yuli") for edge in edge_ids)
    assert graph["metrics"]["active_alerts"] == 1
    assert graph["metrics"]["decision_events"] == 1


def test_decision_context_exposes_explainable_match(db):
    elder, _, _, need, resource, event = _seed_ontology_demo(db)

    ctx = ontology.decision_context(db, str(need.id))

    assert ctx["object"]["raw_id"] == str(need.id)
    assert ctx["requester"]["raw_id"] == str(elder.id)
    assert ctx["matched_resource"]["raw_id"] == str(resource.id)
    assert ctx["decision_events"][0]["raw_id"] == str(event.id)
    assert ctx["recommended_action"]["action_id"] == "confirm_dispatch"
    assert ctx["constraints"]["human_confirmation_required"] is True
