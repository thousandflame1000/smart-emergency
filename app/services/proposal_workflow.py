from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.need import CommunityNeed
from app.models.task_workflow import Approval, Proposal, Task, TaskEvent
from app.models.user import User


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


def proposal_fingerprint(
    *,
    need_id: Any,
    algorithm: str,
    algorithm_version: str,
    score: float | None,
    explanation_json: dict[str, Any],
    candidate_resource_id: Any = None,
    candidate_assignee_id: Any = None,
    candidate_facility_id: Any = None,
    route_reference: str | None = None,
) -> str:
    """Hash the decision payload an approver is actually reviewing."""
    payload = {
        "need_id": str(need_id),
        "algorithm": algorithm,
        "algorithm_version": algorithm_version,
        "score": score,
        "explanation": explanation_json,
        "candidate_resource_id": str(candidate_resource_id) if candidate_resource_id else None,
        "candidate_assignee_id": str(candidate_assignee_id) if candidate_assignee_id else None,
        "candidate_facility_id": str(candidate_facility_id) if candidate_facility_id else None,
        "route_reference": route_reference,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def proposal_fingerprint_for_model(proposal: Proposal) -> str:
    return proposal_fingerprint(
        need_id=proposal.need_id,
        algorithm=proposal.algorithm,
        algorithm_version=proposal.algorithm_version,
        score=proposal.score,
        explanation_json=proposal.explanation_json or {},
        candidate_resource_id=proposal.candidate_resource_id,
        candidate_assignee_id=proposal.candidate_assignee_id,
        candidate_facility_id=proposal.candidate_facility_id,
        route_reference=proposal.route_reference,
    )


class ProposalWorkflowError(Exception):
    status_code = 400
    code = "proposal_workflow_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ProposalNotFoundError(ProposalWorkflowError):
    status_code = 404
    code = "proposal_not_found"


class ProposalAuthorizationError(ProposalWorkflowError):
    status_code = 403
    code = "proposal_forbidden"


class ProposalVersionConflictError(ProposalWorkflowError):
    status_code = 409
    code = "proposal_version_conflict"


class ProposalFingerprintConflictError(ProposalWorkflowError):
    status_code = 409
    code = "proposal_fingerprint_conflict"


class InvalidProposalStateError(ProposalWorkflowError):
    status_code = 409
    code = "invalid_proposal_state"


@dataclass(frozen=True)
class ApprovalResult:
    proposal: Proposal
    approval: Approval
    task: Task


class ProposalService:
    """Creates versioned proposals without owning the caller's transaction."""

    def __init__(self, db: Session):
        self.db = db

    def create_from_dispatch_suggestion(
        self,
        *,
        need: CommunityNeed,
        algorithm: str,
        algorithm_version: str,
        score: float | None,
        explanation_json: dict[str, Any],
        candidate_resource_id: Any = None,
        candidate_assignee_id: Any = None,
        candidate_facility_id: Any = None,
        route_reference: str | None = None,
        created_by: Any = None,
    ) -> Proposal:
        if not candidate_resource_id and not candidate_facility_id:
            raise ValueError("A dispatch proposal requires a resource or facility candidate")

        fingerprint = proposal_fingerprint(
            need_id=need.id,
            algorithm=algorithm,
            algorithm_version=algorithm_version,
            score=score,
            explanation_json=explanation_json,
            candidate_resource_id=candidate_resource_id,
            candidate_assignee_id=candidate_assignee_id,
            candidate_facility_id=candidate_facility_id,
            route_reference=route_reference,
        )
        current = (
            self.db.query(Proposal)
            .filter(
                Proposal.need_id == need.id,
                Proposal.status == "PENDING_APPROVAL",
            )
            .order_by(Proposal.created_at.desc())
            .first()
        )
        if current and current.fingerprint == fingerprint:
            return current
        if current:
            current.status = "SUPERSEDED"
            current.version += 1

        proposal = Proposal(
            need_id=need.id,
            status="PENDING_APPROVAL",
            created_by=created_by,
            algorithm=algorithm,
            algorithm_version=algorithm_version,
            score=score,
            explanation_json=explanation_json,
            candidate_resource_id=candidate_resource_id,
            candidate_assignee_id=candidate_assignee_id,
            candidate_facility_id=candidate_facility_id,
            route_reference=route_reference,
            fingerprint=fingerprint,
            version=1,
        )
        self.db.add(proposal)
        self.db.flush()
        return proposal


class ApprovalService:
    """Approves one exact proposal revision and creates its Task atomically."""

    def __init__(self, db: Session):
        self.db = db

    def approve(
        self,
        *,
        proposal_id: str,
        actor_id: str,
        expected_version: int,
        expected_fingerprint: str,
        comment: str | None = None,
    ) -> ApprovalResult:
        try:
            actor = (
                self.db.query(User)
                .filter(User.id == actor_id, User.is_active == True)
                .first()
            )
            if not actor or not actor.has_role("admin"):
                raise ProposalAuthorizationError("Only an active administrator can approve proposals")

            proposal = (
                self.db.query(Proposal)
                .filter(Proposal.id == proposal_id)
                .with_for_update()
                .first()
            )
            if not proposal:
                raise ProposalNotFoundError("Proposal not found")
            if proposal.status != "PENDING_APPROVAL":
                raise InvalidProposalStateError(
                    f"Proposal in {proposal.status} cannot be approved"
                )
            if proposal.version != expected_version:
                raise ProposalVersionConflictError(
                    f"Expected proposal version {expected_version}, current version is {proposal.version}"
                )
            if (
                proposal.fingerprint != expected_fingerprint
                or proposal_fingerprint_for_model(proposal) != proposal.fingerprint
            ):
                raise ProposalFingerprintConflictError(
                    "Proposal contents no longer match the reviewed fingerprint"
                )

            need = self.db.get(CommunityNeed, proposal.need_id)
            if not need:
                raise ProposalNotFoundError("Proposal need not found")

            changed = self.db.execute(
                update(Proposal)
                .where(
                    Proposal.id == proposal.id,
                    Proposal.status == "PENDING_APPROVAL",
                    Proposal.version == expected_version,
                    Proposal.fingerprint == expected_fingerprint,
                )
                .values(status="APPROVED", version=expected_version + 1)
            )
            if changed.rowcount != 1:
                raise ProposalVersionConflictError("Proposal changed during approval")

            approved_at = _utcnow()
            approval = Approval(
                proposal_id=proposal.id,
                actor_id=actor.id,
                decision="APPROVE",
                comment=comment,
                proposal_version=expected_version,
                created_at=approved_at,
            )
            task = Task(
                need_id=need.id,
                proposal_id=proposal.id,
                task_type="FULFILL_COMMUNITY_NEED",
                status="APPROVED",
                priority=max(1, min(int(need.urgency or 3), 5)),
                destination_reference=f"community_need:{need.id}",
                version=1,
                approved_by=actor.id,
                approved_at=approved_at,
            )
            self.db.add_all([approval, task])
            self.db.flush()
            self.db.add(
                TaskEvent(
                    task_id=task.id,
                    task_version=1,
                    event_type="TASK_CREATED",
                    actor_id=actor.id,
                    metadata_json={
                        "approval_id": str(approval.id),
                        "proposal_id": str(proposal.id),
                        "proposal_version": expected_version,
                        "proposal_fingerprint": expected_fingerprint,
                        "need_id": str(need.id),
                    },
                )
            )
            self.db.commit()
            return ApprovalResult(
                proposal=self.db.get(Proposal, proposal.id),
                approval=self.db.get(Approval, approval.id),
                task=self.db.get(Task, task.id),
            )
        except Exception:
            self.db.rollback()
            raise
