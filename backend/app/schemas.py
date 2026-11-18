"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None


class BatchReportPage(PageResult[dict]):
    """批次完整性报告分页结果。"""


class BatchOwnershipPayload(BaseModel):
    mode: str = Field(default="split", description="split 按流程拆分 / freeze 整批冻结")
    owner_flow: str
    owner_person: str
    actor: str = ""
    actor_role: str = Field(default="flow_owner", description="viewer / flow_owner / admin")
    actor_flow: str = ""


class BatchOwnershipResult(BaseModel):
    ok: bool
    message: str
    conflict: bool = False
    ownership: dict[str, Any] | None = None
    recovery_stage: dict[str, Any] | None = None
    report: dict[str, Any] | None = None


class BatchConflictPayload(BaseModel):
    keep_owner_person: str = ""
    actor: str = ""
    actor_role: str = Field(default="admin", description="只有 admin 可以裁决冲突")


class BatchResupplyPayload(BaseModel):
    flow: str
    owner_person: str = ""
    actor: str = ""
    actor_role: str = Field(default="flow_owner", description="viewer / flow_owner / admin")
    actor_flow: str = ""
    report_version: int | None = Field(
        default=None, description="处理项丁必须基于该版本的批次完整性报告生成")
