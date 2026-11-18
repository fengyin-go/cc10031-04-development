"""批次完整性对账 — 按责任归属核对目标/成功/隔离三源，输出可追责报告。

设计要点
--------
1. 三源勾稽：``目标条数``（批次登记）、``成功条数``（采集记录）、``隔离条数``
   （隔离记录）分别存放在三个对象中，本模块把它们还原到同一张对账表。
2. 责任归属：每条遗漏项都带 ``owner_flow``（责任流程）和 ``source_batch``
   （来源批次），因此任何一条缺失数据都能回查责任流程和来源批次。
3. 补采恢复：已被"新到达批次"恢复的隔离项不再计入待补采遗漏。
4. 归属唯一：拆分/冻结两种路径都只允许一个最终责任人；归属冲突不覆盖原
   责任人，只把批次推入第 4 阶段（处理终态）的"归属冲突待裁决"。
5. 重复导入：采集记录与隔离记录按 ``record_key`` 去重，只认第一条。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from app.store import (
    BatchIntegrityRegistry,
    CollectEntry,
    DirtyRecord,
    OwnershipDecision,
    ResupplyPlan,
    store,
)

# 遗漏项类型
KIND_INTERNAL = "流程内遗漏"
KIND_EXTERNAL = "外部隔离"
KIND_GAP = "采集缺口"

# 批次补采恢复的四个处理阶段；第 4 阶段是处理终态
STAGE_CODES = {1: "S1", 2: "S2", 3: "S3", 4: "S4"}
STAGE_NAMES = {1: "对账完成", 2: "归属确立", 3: "补采执行", 4: "处理终态"}

# 角色：viewer 为受控只读，flow_owner 只能操作本流程，admin 可代为裁决
ROLE_VIEWER = "viewer"
ROLE_FLOW_OWNER = "flow_owner"
ROLE_ADMIN = "admin"
WRITABLE_ROLES = {ROLE_FLOW_OWNER, ROLE_ADMIN}

OWNERSHIP_MODES = {"split": "按流程拆分", "freeze": "整批冻结统一认领"}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _dedupe_by_key(records: Iterable[Any], key_fn) -> list[Any]:
    """重复导入只认第一条记录。"""
    seen: set[str] = set()
    result: list[Any] = []
    for record in records:
        key = key_fn(record)
        if key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result


@dataclass
class BatchReconciliation:
    """单个批次的完整性对账上下文，供报告、归属和补采共用。"""

    row: dict[str, Any]
    batch_ref: str
    flow: str
    target: int
    collected: int
    quarantine_total: int
    recovered: int
    gap: int
    pending: int
    items: list[dict[str, Any]]

    @property
    def complete(self) -> bool:
        return self.pending == 0

    @property
    def flows(self) -> list[str]:
        ordered: list[str] = []
        for item in self.items:
            flow = item["owner_flow"]
            if flow and flow not in ordered:
                ordered.append(flow)
        return ordered


class BatchIntegrityService:
    """批次完整性报告与补采归属的唯一事实来源。"""

    def __init__(self, registry: BatchIntegrityRegistry | None = None) -> None:
        self.registry = registry or store.batch_integrity

    # ------------------------------------------------------------------
    # 对账
    # ------------------------------------------------------------------

    def find_batch(self, batch_ref: str) -> dict[str, Any] | None:
        for row in store.rows("batch_tracker"):
            if str(row.get("批次编号", "")).strip() == batch_ref:
                return row
            if str(row.get("id", "")).strip() == batch_ref:
                return row
        return None

    def reconcile(self, batch_ref: str) -> BatchReconciliation | None:
        row = self.find_batch(batch_ref)
        if row is None:
            return None
        batch_ref = str(row.get("批次编号", "")).strip()
        flow = str(row.get("触发流程", "")).strip()
        target = _as_int(row.get("目标条数"), 0)

        collect_entries = _dedupe_by_key(
            store.collect_records.list_by_batch(batch_ref), self._collect_key)
        dirty_records = _dedupe_by_key(
            store.dirty_vault.list_by_batch(batch_ref), self._dirty_key)

        collected = len(collect_entries)
        quarantine_total = len(dirty_records)
        pending_dirty: list[DirtyRecord] = []
        recovered = 0
        for record in dirty_records:
            arrival = store.arrivals.found_for(batch_ref, self._dirty_key(record))
            if not record.quarantined or arrival is not None or record.recovered_to:
                recovered += 1
                continue
            pending_dirty.append(record)

        gap = max(target - collected - quarantine_total, 0)

        items: list[dict[str, Any]] = []
        for record in pending_dirty:
            owner_flow = record.flow or flow
            items.append(self._item_from_quarantine(
                batch_ref=batch_ref, batch_flow=flow, record=record,
                owner_flow=owner_flow,
            ))
        for index in range(1, gap + 1):
            items.append(self._item_from_gap(
                batch_ref=batch_ref, batch_flow=flow, index=index))

        return BatchReconciliation(
            row=row, batch_ref=batch_ref, flow=flow, target=target,
            collected=collected, quarantine_total=quarantine_total,
            recovered=recovered, gap=gap, pending=len(items), items=items,
        )

    @staticmethod
    def _collect_key(entry: CollectEntry) -> str:
        return entry.record_key or f"COLLECT-{entry.entry_id}"

    @staticmethod
    def _dirty_key(record: DirtyRecord) -> str:
        return record.record_key or f"DIRTY-{record.record_id}"

    def _item_from_quarantine(self, *, batch_ref: str, batch_flow: str,
                              record: DirtyRecord, owner_flow: str) -> dict[str, Any]:
        internal = owner_flow == batch_flow
        kind = KIND_INTERNAL if internal else KIND_EXTERNAL
        key = self._dirty_key(record)
        return {
            "record_key": key,
            "kind": kind,
            "owner_flow": owner_flow,
            "source_batch": batch_ref,
            "source_module": record.source_module,
            "record_id": record.record_id,
            "reason": record.reason,
            "detected_at": record.detected_at,
            "recovered": False,
            "recovered_to": None,
            "internal": internal,
            "responsibility": [
                {"step": 1, "name": "批次登记", "detail": f"批次 {batch_ref} 由「{batch_flow}」触发"},
                {"step": 2, "name": "数据产出", "detail": f"{record.source_module} 写入隔离记录 #{record.record_id}"},
                {"step": 3, "name": "隔离原因", "detail": record.reason},
                {"step": 4, "name": "责任归属", "detail": f"责任流程「{owner_flow}」"},
                {"step": 5, "name": "来源批次", "detail": f"来源批次 {batch_ref}"},
            ],
        }

    def _item_from_gap(self, *, batch_ref: str, batch_flow: str,
                       index: int) -> dict[str, Any]:
        key = f"{batch_ref}-GAP-{index:02d}"
        return {
            "record_key": key,
            "kind": KIND_GAP,
            "owner_flow": batch_flow,
            "source_batch": batch_ref,
            "source_module": "data_extract",
            "record_id": None,
            "reason": "目标条数大于已采集与已隔离之和，存在未产出记录",
            "detected_at": "",
            "recovered": False,
            "recovered_to": None,
            "internal": True,
            "responsibility": [
                {"step": 1, "name": "批次登记", "detail": f"批次 {batch_ref} 目标 {self._target_of(batch_ref)} 条"},
                {"step": 2, "name": "数据产出", "detail": "采集流程未产出该记录，也未落入隔离区"},
                {"step": 3, "name": "责任归属", "detail": f"责任流程「{batch_flow}」"},
                {"step": 4, "name": "来源批次", "detail": f"来源批次 {batch_ref}"},
            ],
        }

    def _target_of(self, batch_ref: str) -> str:
        row = self.find_batch(batch_ref)
        return str(row.get("目标条数", "?")) if row else "?"

    # ------------------------------------------------------------------
    # 报告
    # ------------------------------------------------------------------

    def build_report(self, batch_ref: str) -> dict[str, Any] | None:
        recon = self.reconcile(batch_ref)
        if recon is None:
            return None
        recorded_missed = _as_int(recon.row.get("遗漏条数"), 0)
        delta = recon.pending - recorded_missed
        ownership = self.registry.get_ownership(recon.batch_ref)
        plans = self.registry.plans_for(recon.batch_ref)
        stage, terminal = self._stage(recon, ownership, plans)

        return {
            "batch_ref": recon.batch_ref,
            "batch_id": recon.row.get("id"),
            "flow": recon.flow,
            "collected_at": recon.row.get("采集时间", ""),
            "status": recon.row.get("status", ""),
            "check_status": recon.row.get("校验状态", ""),
            "target_count": recon.target,
            "success_count": recon.collected,
            "quarantine_count": recon.quarantine_total,
            "recovered_count": recon.recovered,
            "pending_count": recon.pending,
            "gap_count": recon.gap,
            "recorded_missed_count": recorded_missed,
            "reconciliation": {
                "formula": "目标条数 = 成功条数 + 隔离条数 + 采集缺口",
                "target": recon.target,
                "collected": recon.collected,
                "quarantined": recon.quarantine_total,
                "gap": recon.gap,
                "balanced": recon.target - recon.collected - recon.quarantine_total - recon.gap == 0,
                "recorded_missed": recorded_missed,
                "reconciled_missing": recon.pending,
                "delta_vs_recorded": delta,
            },
            "completeness": "完整" if recon.complete else "遗漏",
            "report_version": self.registry.report_version(recon.batch_ref),
            "flow_breakdown": self._flow_breakdown(recon),
            "items": recon.items,
            "missing_count": recon.pending,
            "ownership": ownership.as_dict() if ownership else None,
            "resupply_plans": [plan.as_dict() for plan in plans],
            "resupply_plan_versions": {
                flow: (self.registry.latest_plan(recon.batch_ref, flow).version
                       if self.registry.latest_plan(recon.batch_ref, flow) else 0)
                for flow in recon.flows
            },
            "recovery_stage": {
                "code": STAGE_CODES[stage],
                "stage": stage,
                "name": STAGE_NAMES[stage],
                "terminal_state": terminal,
            },
        }

    def _flow_breakdown(self, recon: BatchReconciliation) -> list[dict[str, Any]]:
        ownership = self.registry.get_ownership(recon.batch_ref)
        breakdown: list[dict[str, Any]] = []
        for flow in recon.flows:
            flow_items = [item for item in recon.items if item["owner_flow"] == flow]
            breakdown.append({
                "flow": flow,
                "internal": sum(1 for item in flow_items if item["internal"]),
                "external": sum(1 for item in flow_items if not item["internal"]),
                "missing": len(flow_items),
                "scope_keys": [item["record_key"] for item in flow_items],
                "is_batch_flow": flow == recon.flow,
                "has_ownership": bool(ownership and ownership.owner_flow == flow),
            })
        return breakdown

    def _stage(self, recon: BatchReconciliation,
               ownership: OwnershipDecision | None,
               plans: list[ResupplyPlan]) -> tuple[int, str]:
        if ownership is not None and ownership.conflict:
            # 归属冲突直接把批次推到第 4 阶段处理终态
            return 4, "归属冲突待裁决"
        if recon.complete:
            return 4, "补采完成"
        if not plans:
            return (2 if ownership is not None else 1), (
                "归属已确立，待生成补采任务" if ownership is not None else "待归属")
        return 3, "补采进行中"

    def list_reports(self, *, flow: str | None = None,
                     completeness: str | None = None,
                     keyword: str | None = None,
                     page: int = 1, size: int = 20) -> tuple[list[dict[str, Any]], int]:
        reports: list[dict[str, Any]] = []
        for row in store.rows("batch_tracker"):
            batch_ref = str(row.get("批次编号", "")).strip()
            if not batch_ref:
                continue
            if flow and flow != str(row.get("触发流程", "")).strip():
                # 也允许按"涉及流程"筛出含外部隔离的批次
                recon = self.reconcile(batch_ref)
                if recon is None or flow not in recon.flows:
                    continue
            report = self.build_report(batch_ref)
            if report is None:
                continue
            if completeness and report["completeness"] != completeness:
                continue
            if keyword and keyword.casefold() not in batch_ref.casefold():
                continue
            reports.append(report)
        total = len(reports)
        start = (page - 1) * size
        return reports[start:start + size], total

    # ------------------------------------------------------------------
    # 归属
    # ------------------------------------------------------------------

    def _permission_error(self, actor_role: str, actor_flow: str,
                          target_flow: str) -> str | None:
        if actor_role not in WRITABLE_ROLES:
            return "受控只读角色只能查看报告，不能改写补采责任人"
        if actor_role == ROLE_FLOW_OWNER and actor_flow != target_flow:
            return f"「{actor_flow or '未指定流程'}」不能改写其他流程「{target_flow}」的补采责任人"
        return None

    def establish_ownership(self, batch_ref: str, *, mode: str,
                            owner_flow: str, owner_person: str,
                            actor: str, actor_role: str,
                            actor_flow: str = "") -> dict[str, Any]:
        recon = self.reconcile(batch_ref)
        if recon is None:
            return {"ok": False, "message": f"批次 {batch_ref} 不存在"}
        if mode not in OWNERSHIP_MODES:
            return {"ok": False, "message": f"未知归属路径：{mode}"}
        owner_person = (owner_person or "").strip()
        owner_flow = (owner_flow or "").strip()
        if not owner_person:
            return {"ok": False, "message": "必须指定唯一最终责任人"}
        if not owner_flow:
            return {"ok": False, "message": "必须指定责任流程"}
        if owner_flow not in recon.flows:
            return {"ok": False, "message": f"流程「{owner_flow}」在本批次没有缺失项，无需认领"}

        permission = self._permission_error(actor_role, actor_flow, owner_flow)
        if permission:
            return {"ok": False, "message": permission}

        existing = self.registry.get_ownership(recon.batch_ref)
        if existing is not None and (
                existing.owner_person != owner_person
                or existing.owner_flow != owner_flow
                or existing.mode != mode):
            # 冲突：不覆盖原责任人，只登记冲突并推进到第 4 阶段处理终态
            existing.conflict = True
            existing.conflict_detail = (
                f"「{actor or owner_flow}」申请以 {OWNERSHIP_MODES[mode]} 方式由"
                f"「{owner_flow}/{owner_person}」认领，但批次已有最终责任人"
                f"「{existing.owner_flow}/{existing.owner_person}」")
            self.registry.bump_report_version(recon.batch_ref)
            return {
                "ok": False,
                "conflict": True,
                "message": "归属冲突：一个批次只能有一个最终责任人，已保留原责任人并标记待裁决",
                "ownership": existing.as_dict(),
                "recovery_stage": {"code": "S4", "stage": 4,
                                   "name": STAGE_NAMES[4],
                                   "terminal_state": "归属冲突待裁决"},
                "report": self.build_report(recon.batch_ref),
            }

        scopes: dict[str, list[str]] = {}
        if mode == "split":
            for flow in recon.flows:
                scopes[flow] = [
                    item["record_key"] for item in recon.items
                    if item["owner_flow"] == flow
                ]
            scopes.setdefault(owner_flow, [])
        else:
            scopes[owner_flow] = [item["record_key"] for item in recon.items]

        decision = OwnershipDecision(
            batch_ref=recon.batch_ref, mode=mode, owner_flow=owner_flow,
            owner_person=owner_person, item_scopes=scopes,
            version=(existing.version + 1 if existing else 1),
            decided_by=actor or owner_flow,
        )
        self.registry.set_ownership(decision)
        return {
            "ok": True,
            "message": f"已按「{OWNERSHIP_MODES[mode]}」确立唯一责任人 {owner_person}",
            "ownership": decision.as_dict(),
            "report": self.build_report(recon.batch_ref),
        }

    # ------------------------------------------------------------------
    # 补采任务清单（处理项丁）
    # ------------------------------------------------------------------

    def resolve_conflict(self, batch_ref: str, *, keep_owner_person: str = "",
                         actor: str = "", actor_role: str = "") -> dict[str, Any]:
        """裁决归属冲突：确认唯一最终责任人，冲突标记清除。

        只允许平台管理员裁决，避免任一流程自行改写其他流程的责任人。
        """
        if actor_role != ROLE_ADMIN:
            return {"ok": False, "message": "只有平台管理员可以裁决归属冲突"}
        recon = self.reconcile(batch_ref)
        if recon is None:
            return {"ok": False, "message": f"批次 {batch_ref} 不存在"}
        ownership = self.registry.get_ownership(recon.batch_ref)
        if ownership is None:
            return {"ok": False, "message": "批次尚未确立补采归属"}
        if not ownership.conflict:
            return {"ok": False, "message": "批次不存在归属冲突"}
        if keep_owner_person and keep_owner_person != ownership.owner_person:
            return {"ok": False,
                    "message": f"裁决只能确认既有最终责任人 {ownership.owner_person}"}
        ownership.conflict = False
        ownership.conflict_detail = (
            f"归属冲突已由 {actor or '平台管理员'} 裁决，维持唯一责任人"
            f"「{ownership.owner_flow}/{ownership.owner_person}」")
        self.registry.bump_report_version(recon.batch_ref)
        return {
            "ok": True,
            "message": f"冲突已裁决，最终责任人仍为 {ownership.owner_person}",
            "ownership": ownership.as_dict(),
            "report": self.build_report(recon.batch_ref),
        }

    def create_resupply_plan(self, batch_ref: str, *, flow: str,
                             owner_person: str = "", actor: str = "",
                             actor_role: str, actor_flow: str = "",
                             report_version: int | None = None) -> dict[str, Any]:
        recon = self.reconcile(batch_ref)
        if recon is None:
            return {"ok": False, "message": f"批次 {batch_ref} 不存在"}
        flow = (flow or "").strip()
        owner_person = (owner_person or "").strip()
        if not flow:
            return {"ok": False, "message": "必须指定发起补采的流程"}
        permission = self._permission_error(actor_role, actor_flow, flow)
        if permission:
            return {"ok": False, "message": permission}

        current_version = self.registry.report_version(recon.batch_ref)
        if report_version is not None and int(report_version) != current_version:
            return {"ok": False,
                    "message": f"报告已更新（当前版本 {current_version}），请基于最新批次完整性报告生成"}

        flow_items = [item for item in recon.items if item["owner_flow"] == flow]
        if not flow_items:
            return {"ok": False, "message": f"流程「{flow}」在本批次没有缺失项，无需补采"}

        ownership = self.registry.get_ownership(recon.batch_ref)
        if ownership is None and flow != recon.flow:
            return {"ok": False,
                    "message": f"批次尚未确立补采归属，「{flow}」的外部隔离项需先认领后才能补采"}
        if ownership is not None and ownership.conflict:
            return {"ok": False, "message": "批次归属存在冲突，需先完成裁决"}

        # 唯一最终责任人：与批次归属一致，不允许另立责任人
        if ownership is not None and ownership.owner_person:
            if not owner_person:
                owner_person = ownership.owner_person
            elif owner_person != ownership.owner_person:
                return {"ok": False,
                        "message": f"批次最终责任人为 {ownership.owner_person}，补采只能由其负责"}
        if not owner_person:
            return {"ok": False, "message": "必须为本流程缺失项指定责任人"}

        # 整批冻结时，只有认领流程可以发起补采
        if ownership is not None and ownership.mode == "freeze" and ownership.owner_flow != flow:
            return {"ok": False,
                    "message": f"批次已整批冻结并由「{ownership.owner_flow}」统一认领，其他流程只读"}

        # 重复导入只认第一条：同批次+流程+报告版本只保留首个清单
        for existing in self.registry.plans_for(recon.batch_ref, flow):
            if existing.report_version == current_version:
                return {
                    "ok": True,
                    "duplicated": True,
                    "message": "已存在同版本补采清单，返回首条记录",
                    "plan": existing.as_dict(),
                    "report": self.build_report(recon.batch_ref),
                }

        version = self.registry.next_plan_version(recon.batch_ref, flow)
        task_ref = self._enqueue_resupply_task(
            recon.batch_ref, flow, owner_person, version, len(flow_items))
        plan = ResupplyPlan(
            plan_id=0,
            batch_ref=recon.batch_ref,
            version=version,
            report_version=current_version,
            flow=flow,
            owner_person=owner_person,
            scope_keys=[item["record_key"] for item in flow_items],
            scope_batches=sorted({item["source_batch"] for item in flow_items}),
            scope_count=len(flow_items),
            task_ref=task_ref,
            created_by=actor or flow,
        )
        self.registry.add_plan(plan)

        # 联动批次状态与待办
        if recon.row.get("status") in ("已收集", "校验中"):
            recon.row["status"] = "有遗漏"
        recon.row["pending"] = True
        recon.row["abnormal"] = True

        return {
            "ok": True,
            "message": f"已生成处理项丁版本 v{version} 补采任务清单",
            "plan": plan.as_dict(),
            "report": self.build_report(recon.batch_ref),
        }

    def _enqueue_resupply_task(self, batch_ref: str, flow: str,
                               owner_person: str, version: int,
                               scope_count: int) -> str:
        """联动任务队列：补采清单成立即写入待办。"""
        tasks = store.rows("task_queue")
        task_no = f"RESU-{batch_ref}-{flow}-v{version}"
        for task in tasks:
            if task.get("任务编号") == task_no:
                task["当前状态"] = task["status"] = "排队中"
                task["pending"] = True
                return task_no
        new_id = max((int(t.get("id", 0)) for t in tasks), default=0) + 1
        tasks.append({
            "id": new_id,
            "任务编号": task_no,
            "流程名称": f"补采-{flow}",
            "提交时间": "2026-11-18",
            "优先级": "高",
            "预计执行": "立即",
            "排队时长": "0min",
            "当前状态": "排队中",
            "status": "排队中",
            "pending": True,
            "abnormal": False,
            "批次编号": batch_ref,
            "责任人": owner_person,
            "补采范围": str(scope_count),
        })
        return task_no

    # ------------------------------------------------------------------
    # 追溯
    # ------------------------------------------------------------------

    def trace_item(self, record_key: str,
                   batch_ref: str | None = None) -> dict[str, Any] | None:
        """回查单条遗漏：责任流程 + 来源批次 + 处理阶段。"""
        candidates = [batch_ref] if batch_ref else [
            str(row.get("批次编号", "")) for row in store.rows("batch_tracker")
        ]
        for ref in candidates:
            report = self.build_report(ref)
            if report is None:
                continue
            for item in report["items"]:
                if item["record_key"] == record_key:
                    return {
                        "found": True,
                        "item": item,
                        "batch_ref": report["batch_ref"],
                        "flow": report["flow"],
                        "ownership": report["ownership"],
                        "resupply_plans": report["resupply_plans"],
                        "recovery_stage": report["recovery_stage"],
                        "trace": item["responsibility"],
                    }
        return None

    def domain_snapshot(self) -> dict[str, Any]:
        reports, _ = self.list_reports(page=1, size=1_000_000)
        return {
            "module": "batch_tracker",
            "artifact": "批次完整性报告",
            "reports": len(reports),
            "complete": sum(1 for r in reports if r["completeness"] == "完整"),
            "missed": sum(1 for r in reports if r["completeness"] == "遗漏"),
            "missing_items": sum(r["missing_count"] for r in reports),
            "ownership": self.registry.snapshot(),
            "arrivals": store.arrivals.snapshot(),
        }


batch_integrity = BatchIntegrityService()
