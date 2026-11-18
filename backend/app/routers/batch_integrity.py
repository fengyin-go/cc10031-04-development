"""批次完整性报告 API：按责任归属对账、确立补采归属、生成补采任务清单。

固定路径必须在通用 /api/{module} 资源路由之前注册（见 app/routers/__init__.py）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    BatchConflictPayload,
    BatchOwnershipPayload,
    BatchOwnershipResult,
    BatchReportPage,
    BatchResupplyPayload,
)
from app.services.batch_integrity import batch_integrity
from app.store import store

router = APIRouter(prefix="/api/batch-integrity", tags=["批次完整性"])


@router.get("/reports", response_model=BatchReportPage)
def list_reports(
    flow: str | None = Query(default=None, description="按所属流程筛选"),
    completeness: str | None = Query(default=None, description="完整 / 遗漏"),
    keyword: str | None = Query(default=None, description="按批次编号检索"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> BatchReportPage:
    reports, total = batch_integrity.list_reports(
        flow=flow, completeness=completeness, keyword=keyword, page=page, size=size)
    return BatchReportPage(items=reports, total=total, page=page, size=size)


@router.get("/reports/{batch_ref}", response_model=dict)
def get_report(batch_ref: str) -> dict[str, Any]:
    report = batch_integrity.build_report(batch_ref)
    if report is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_ref} 不存在")
    return report


@router.post("/reports/{batch_ref}/ownership", response_model=BatchOwnershipResult)
def establish_ownership(batch_ref: str, payload: BatchOwnershipPayload) -> BatchOwnershipResult:
    result = batch_integrity.establish_ownership(
        batch_ref,
        mode=payload.mode,
        owner_flow=payload.owner_flow,
        owner_person=payload.owner_person,
        actor=payload.actor,
        actor_role=payload.actor_role,
        actor_flow=payload.actor_flow,
    )
    return BatchOwnershipResult(**result)


@router.post("/reports/{batch_ref}/ownership/resolve", response_model=BatchOwnershipResult)
def resolve_ownership_conflict(
    batch_ref: str, payload: BatchConflictPayload) -> BatchOwnershipResult:
    result = batch_integrity.resolve_conflict(
        batch_ref,
        keep_owner_person=payload.keep_owner_person,
        actor=payload.actor,
        actor_role=payload.actor_role,
    )
    return BatchOwnershipResult(**result)


@router.post("/reports/{batch_ref}/resupply", response_model=dict)
def create_resupply_plan(batch_ref: str, payload: BatchResupplyPayload) -> dict[str, Any]:
    result = batch_integrity.create_resupply_plan(
        batch_ref,
        flow=payload.flow,
        owner_person=payload.owner_person,
        actor=payload.actor,
        actor_role=payload.actor_role,
        actor_flow=payload.actor_flow,
        report_version=payload.report_version,
    )
    if not result.get("ok") and not result.get("duplicated"):
        # 业务拒绝仍返回 200，由前端展示原因；仅参数级错误走 400
        if "必须" in str(result.get("message", "")) or "不能" in str(result.get("message", "")):
            raise HTTPException(status_code=403, detail=result["message"])
        raise HTTPException(status_code=409, detail=result["message"])
    return result


@router.get("/trace/{record_key}", response_model=dict)
def trace_item(record_key: str, batch_ref: str | None = Query(default=None)) -> dict[str, Any]:
    traced = batch_integrity.trace_item(record_key, batch_ref=batch_ref)
    if traced is None:
        raise HTTPException(status_code=404, detail=f"遗漏记录 {record_key} 未找到")
    return traced


@router.get("/snapshot", response_model=dict)
def snapshot() -> dict[str, Any]:
    return {
        "integrity": batch_integrity.domain_snapshot(),
        "arrivals": [
            {
                "arrival_id": a.arrival_id,
                "new_batch_ref": a.new_batch_ref,
                "source_batch_ref": a.source_batch_ref,
                "recovered_key": a.recovered_key,
                "flow": a.flow,
                "arrived_at": a.arrived_at,
                "handled_by": a.handled_by,
            }
            for a in store.arrivals.list_all()
        ],
    }
