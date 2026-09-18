from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.base_types import GUID
from app.models.need import CommunityNeed
from app.models.road_workflow import RoadObservation, TaskRoute
from app.models.task_workflow import Proposal, Task, TaskEvent
from app.services import road_network
from app.services.proposal_workflow import ProposalService
from app.services.task_commands import ACTIVE_TASK_STATUSES, TaskCommandService


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class RoadReplanningError(Exception):
    status_code = 400


class RoadSegmentNotFoundError(RoadReplanningError):
    status_code = 404


class TaskRouteNotFoundError(RoadReplanningError):
    status_code = 404


class RouteUnavailableError(RoadReplanningError):
    status_code = 409


class TaskRouteService:
    def __init__(self, db: Session):
        self.db = db

    def attach_route(
        self,
        *,
        task_id: str,
        start_node: str,
        end_node: str,
        proposal_id: str | None = None,
    ) -> TaskRoute:
        try:
            task = (
                self.db.query(Task)
                .filter(Task.id == task_id)
                .with_for_update()
                .first()
            )
            if not task:
                raise TaskRouteNotFoundError("Task not found")
            result = road_network.route_between_nodes(self.db, start_node, end_node)
            if not result.get("reachable"):
                raise RouteUnavailableError("No route is available between the selected nodes")

            now = _utcnow()
            for current in (
                self.db.query(TaskRoute)
                .filter(TaskRoute.task_id == task.id, TaskRoute.status == "ACTIVE")
                .all()
            ):
                current.status = "SUPERSEDED"
                current.superseded_at = now

            route = TaskRoute(
                task_id=task.id,
                proposal_id=proposal_id or task.proposal_id,
                start_node=start_node,
                end_node=end_node,
                node_path=result["path"],
                segment_ids=result["segment_ids"],
                distance_km=result["distance_km"],
                status="ACTIVE",
            )
            self.db.add(route)
            self.db.flush()
            task.route_reference = str(route.id)
            self.db.commit()
            return self.db.get(TaskRoute, route.id)
        except Exception:
            self.db.rollback()
            raise

    def active_routes_using_segment(self, road_segment_id: str) -> list[TaskRoute]:
        routes = (
            self.db.query(TaskRoute)
            .join(Task, Task.id == TaskRoute.task_id)
            .filter(
                TaskRoute.status == "ACTIVE",
                Task.status.in_(ACTIVE_TASK_STATUSES),
            )
            .all()
        )
        return [route for route in routes if road_segment_id in (route.segment_ids or [])]


class RoadReplanningService:
    def __init__(self, db: Session):
        self.db = db

    def observe(
        self,
        *,
        road_segment_id: str,
        state: str,
        source: str,
        observed_at: datetime | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = state.upper()
        if state not in {"OPEN", "SLOW", "BLOCKED"}:
            raise ValueError("state must be OPEN, SLOW, or BLOCKED")
        if not any(
            edge["id"] == road_segment_id
            for edge in road_network.sandbox_snapshot(self.db)["edges"]
        ):
            raise RoadSegmentNotFoundError("Road segment not found")

        observation = RoadObservation(
            road_segment_id=road_segment_id,
            state=state,
            source=source,
            observed_at=observed_at or _utcnow(),
            details_json=details or {},
        )
        self.db.add(observation)
        self.db.commit()
        self.db.refresh(observation)

        result = {
            "observation_id": str(observation.id),
            "road_segment_id": road_segment_id,
            "state": state,
            "affected_task_ids": [],
            "proposal_ids": [],
            "alerts": [],
        }
        latest = (
            self.db.query(RoadObservation)
            .filter(RoadObservation.road_segment_id == road_segment_id)
            .order_by(
                RoadObservation.observed_at.desc(),
                RoadObservation.created_at.desc(),
                RoadObservation.id.desc(),
            )
            .first()
        )
        if (
            state != "BLOCKED"
            or str(latest.id) != str(observation.id)
        ):
            return result

        routes = TaskRouteService(self.db).active_routes_using_segment(road_segment_id)
        for route in routes:
            outcome = self._block_and_replan(route, observation)
            result["affected_task_ids"].append(str(route.task_id))
            if outcome.get("proposal_id"):
                result["proposal_ids"].append(outcome["proposal_id"])
            if outcome.get("alert"):
                result["alerts"].append(outcome["alert"])
        return result

    def _block_and_replan(
        self,
        route: TaskRoute,
        observation: RoadObservation,
    ) -> dict[str, str]:
        task = self.db.get(Task, route.task_id)
        route.status = "BLOCKED"
        self.db.flush()
        if task.status != "BLOCKED":
            task = TaskCommandService(self.db).block_task_for_road_change(
                str(task.id),
                task.version,
                road_segment_id=observation.road_segment_id,
                observation_id=str(observation.id),
            )
        else:
            self.db.commit()

        alternative = road_network.route_between_nodes(
            self.db,
            route.start_node,
            route.end_node,
        )
        if not alternative.get("reachable"):
            return self._record_unavailable(task, route, observation)

        original = self.db.get(Proposal, task.proposal_id) if task.proposal_id else None
        need = self.db.get(CommunityNeed, task.need_id)
        if (
            not original
            or not need
            or not (original.candidate_resource_id or original.candidate_facility_id)
        ):
            return self._record_unavailable(task, route, observation)

        new_route_id = GUID.new()
        proposal = ProposalService(self.db).create_from_dispatch_suggestion(
            need=need,
            algorithm="road-replan",
            algorithm_version="1",
            score=original.score,
            explanation_json={
                "reason": "active route blocked",
                "blocked_segment_id": observation.road_segment_id,
                "observation_id": str(observation.id),
                "previous_proposal_id": str(original.id),
                "previous_route_id": str(route.id),
                "path": alternative["path"],
                "segment_ids": alternative["segment_ids"],
                "distance_km": alternative["distance_km"],
            },
            candidate_resource_id=original.candidate_resource_id,
            candidate_assignee_id=original.candidate_assignee_id,
            candidate_facility_id=original.candidate_facility_id,
            route_reference=new_route_id,
        )
        proposed_route = TaskRoute(
            id=new_route_id,
            task_id=task.id,
            proposal_id=proposal.id,
            start_node=route.start_node,
            end_node=route.end_node,
            node_path=alternative["path"],
            segment_ids=alternative["segment_ids"],
            distance_km=alternative["distance_km"],
            status="PROPOSED",
        )
        self.db.add(proposed_route)
        self.db.add(
            TaskEvent(
                task_id=task.id,
                task_version=task.version,
                event_type="REPLAN_PROPOSED",
                actor_id=None,
                metadata_json={
                    "proposal_id": str(proposal.id),
                    "route_id": new_route_id,
                    "previous_route_id": str(route.id),
                    "observation_id": str(observation.id),
                },
            )
        )
        self.db.commit()
        return {"proposal_id": str(proposal.id)}

    def _record_unavailable(
        self,
        task: Task,
        route: TaskRoute,
        observation: RoadObservation,
    ) -> dict[str, str]:
        route.status = "UNREACHABLE"
        message = (
            f"No alternative route for Task {task.id} after segment "
            f"{observation.road_segment_id} was blocked"
        )
        self.db.add(
            TaskEvent(
                task_id=task.id,
                task_version=task.version,
                event_type="REPLAN_UNAVAILABLE",
                actor_id=None,
                metadata_json={
                    "severity": "critical",
                    "message": message,
                    "route_id": str(route.id),
                    "observation_id": str(observation.id),
                },
            )
        )
        self.db.commit()
        return {"alert": message}
