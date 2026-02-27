"""Review lifecycle endpoints (queries + mutations)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from converge import event_log, reviews
from converge.api.auth import require_operator, require_viewer
from converge.api.schemas import (
    ReviewAssignBody,
    ReviewCancelBody,
    ReviewCompleteBody,
    ReviewEscalateBody,
    ReviewRequestBody,
)

router = APIRouter(tags=["reviews"])


@router.get("/reviews")
def reviews_list_http(
    request: Request,
    tenant_id: str | None = None,
    intent_id: str | None = None,
    status: str | None = None,
    reviewer: str | None = None,
    limit: int = 50,
    principal: dict = Depends(require_viewer),
):
    """List review tasks with optional filters."""
    tenant = principal.get("tenant") or tenant_id
    tasks = event_log.list_review_tasks(
        intent_id=intent_id, status=status,
        reviewer=reviewer, tenant_id=tenant, limit=limit,
    )
    return {"reviews": [t.to_dict() for t in tasks], "total": len(tasks)}


@router.get("/reviews/summary")
def reviews_summary_http(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    """Review task summary for dashboard."""
    tenant = principal.get("tenant") or tenant_id
    return reviews.review_summary(tenant_id=tenant)


@router.post("/reviews")
def request_review_http(
    request: Request,
    body: ReviewRequestBody,
    principal: dict = Depends(require_operator),
):
    tenant = principal.get("tenant") or body.tenant_id
    try:
        task = reviews.request_review(
            body.intent_id,
            trigger=body.trigger,
            reviewer=body.reviewer,
            priority=body.priority,
            tenant_id=tenant,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return task.to_dict()


@router.post("/reviews/{task_id}/assign")
def assign_review_http(
    task_id: str,
    request: Request,
    body: ReviewAssignBody,
    principal: dict = Depends(require_operator),
):
    try:
        task = reviews.assign_review(task_id, body.reviewer)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return task.to_dict()


@router.post("/reviews/{task_id}/complete")
def complete_review_http(
    task_id: str,
    request: Request,
    body: ReviewCompleteBody,
    principal: dict = Depends(require_operator),
):
    try:
        task = reviews.complete_review(
            task_id, resolution=body.resolution, notes=body.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return task.to_dict()


@router.post("/reviews/{task_id}/cancel")
def cancel_review_http(
    task_id: str,
    request: Request,
    body: ReviewCancelBody,
    principal: dict = Depends(require_operator),
):
    try:
        task = reviews.cancel_review(task_id, reason=body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return task.to_dict()


@router.post("/reviews/{task_id}/escalate")
def escalate_review_http(
    task_id: str,
    request: Request,
    body: ReviewEscalateBody,
    principal: dict = Depends(require_operator),
):
    try:
        task = reviews.escalate_review(task_id, reason=body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return task.to_dict()
