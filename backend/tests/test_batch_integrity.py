"""批次完整性对账与补采归属测试。

覆盖：三源核对、责任归属追溯、补采恢复、受控只读、唯一责任人、
拆分/冻结两条路径、补采清单按报告版本生成、重复导入只认第一条。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.batch_integrity import (
    KIND_EXTERNAL,
    KIND_GAP,
    KIND_INTERNAL,
    BatchIntegrityService,
)
from app.store import BatchIntegrityRegistry, store

BATCH_MIXED = "BATC-20261101-01"      # 流程内 3 条 + 外部隔离 1 条
BATCH_GAP = "BATC-20261102-01"        # 流程内 3 条 + 外部 1 条 + 采集缺口 2 条
BATCH_RECOVERED = "BATC-20261103-02"  # 1 条已由新到达批次恢复
BATCH_COMPLETE = "BATC-20261101-02"
BATCH_CHECKING = "BATC-20261103-01"   # 校验中但已平账

FLOW_PRICE = "商品价格采集流程"
FLOW_ORDER = "订单明细采集流程"


@pytest.fixture()
def svc() -> BatchIntegrityService:
    # 每个用例独立的归属台账，避免用例之间互相污染
    return BatchIntegrityService(registry=BatchIntegrityRegistry())


# ---------------------------------------------------------------------------
# 对账
# ---------------------------------------------------------------------------

def test_report_reconciles_three_sources(svc: BatchIntegrityService) -> None:
    report = svc.build_report(BATCH_MIXED)
    assert report is not None
    recon = report["reconciliation"]
    assert recon["target"] == 24
    assert recon["collected"] == 20
    assert recon["quarantined"] == 4
    assert recon["balanced"] is True
    assert recon["reconciled_missing"] == recon["recorded_missed"] == 4
    assert report["completeness"] == "遗漏"


def test_gap_items_are_reported(svc: BatchIntegrityService) -> None:
    report = svc.build_report(BATCH_GAP)
    assert report["gap_count"] == 2
    kinds = [item["kind"] for item in report["items"]]
    assert kinds.count(KIND_GAP) == 2
    assert report["pending_count"] == 6


def test_every_item_traces_owner_flow_and_source_batch(svc: BatchIntegrityService) -> None:
    report = svc.build_report(BATCH_MIXED)
    for item in report["items"]:
        assert item["owner_flow"]
        assert item["source_batch"] == BATCH_MIXED
        assert len(item["responsibility"]) >= 3
        assert item["responsibility"][-1]["detail"].endswith(BATCH_MIXED)


def test_mixed_batch_splits_internal_and_external(svc: BatchIntegrityService) -> None:
    report = svc.build_report(BATCH_MIXED)
    internal = [i for i in report["items"] if i["kind"] == KIND_INTERNAL]
    external = [i for i in report["items"] if i["kind"] == KIND_EXTERNAL]
    assert len(internal) == 3
    assert len(external) == 1
    assert {i["owner_flow"] for i in internal} == {FLOW_PRICE}
    assert {i["owner_flow"] for i in external} == {FLOW_ORDER}
    breakdown = {g["flow"]: g for g in report["flow_breakdown"]}
    assert breakdown[FLOW_PRICE]["internal"] == 3
    assert breakdown[FLOW_ORDER]["external"] == 1


def test_recovered_arrival_is_not_counted_again(svc: BatchIntegrityService) -> None:
    report = svc.build_report(BATCH_RECOVERED)
    assert report["recovered_count"] == 1
    assert report["pending_count"] == 1
    keys = [i["record_key"] for i in report["items"]]
    assert "BATC-20261103-02-Q01" not in keys
    assert "BATC-20261103-02-Q02" in keys


def test_complete_batch_has_no_items(svc: BatchIntegrityService) -> None:
    report = svc.build_report(BATCH_COMPLETE)
    assert report["completeness"] == "完整"
    assert report["items"] == []
    assert report["recovery_stage"]["code"] == "S4"


def test_list_reports_filters_by_flow_and_completeness(svc: BatchIntegrityService) -> None:
    missed, total = svc.list_reports(completeness="遗漏")
    assert total == len(missed) == 3
    complete, _ = svc.list_reports(completeness="完整")
    assert all(r["completeness"] == "完整" for r in complete)

    # 按流程筛选时，含该流程外部隔离的批次也要能筛出来
    order_reports, _ = svc.list_reports(flow=FLOW_ORDER)
    refs = {r["batch_ref"] for r in order_reports}
    assert BATCH_MIXED in refs
    assert BATCH_GAP in refs


def test_trace_item_returns_responsibility_chain(svc: BatchIntegrityService) -> None:
    traced = svc.trace_item("BATC-20261101-01-Q04")
    assert traced is not None
    assert traced["item"]["owner_flow"] == FLOW_ORDER
    assert traced["batch_ref"] == BATCH_MIXED
    assert traced["trace"][-1]["name"] == "来源批次"


def test_duplicate_keys_only_count_first(svc: BatchIntegrityService) -> None:
    """重复导入只认第一条：同 record_key 的采集记录和隔离记录都只计一次。"""
    before = svc.build_report(BATCH_MIXED)

    store.collect_records.add(BATCH_MIXED, "data_extract", {"重复": True},
                              record_key="BATC-20261101-01-R001")
    store.collect_records.add(BATCH_MIXED, "data_extract", {"重复": True},
                              record_key="BATC-20261101-01-R001")
    store.dirty_vault.quarantine("data_extract", BATCH_MIXED, {"重复": True},
                                 "重复导入", flow=FLOW_PRICE,
                                 record_key="BATC-20261101-01-Q01")

    after = svc.build_report(BATCH_MIXED)
    assert after["success_count"] == before["success_count"]
    assert after["quarantine_count"] == before["quarantine_count"]
    assert after["pending_count"] == before["pending_count"]


# ---------------------------------------------------------------------------
# 归属权限
# ---------------------------------------------------------------------------

def test_viewer_cannot_write_ownership(svc: BatchIntegrityService) -> None:
    result = svc.establish_ownership(
        BATCH_MIXED, mode="split", owner_flow=FLOW_PRICE, owner_person="张三",
        actor="只读用户", actor_role="viewer", actor_flow=FLOW_PRICE)
    assert result["ok"] is False
    assert "只读" in result["message"]
    assert svc.registry.get_ownership(BATCH_MIXED) is None


def test_flow_owner_cannot_write_other_flow(svc: BatchIntegrityService) -> None:
    result = svc.establish_ownership(
        BATCH_MIXED, mode="split", owner_flow=FLOW_ORDER, owner_person="李四",
        actor="张三", actor_role="flow_owner", actor_flow=FLOW_PRICE)
    assert result["ok"] is False
    assert FLOW_PRICE in result["message"]


def test_flow_owner_cannot_generate_other_flow_resupply(svc: BatchIntegrityService) -> None:
    result = svc.create_resupply_plan(
        BATCH_MIXED, flow=FLOW_ORDER, actor="张三",
        actor_role="flow_owner", actor_flow=FLOW_PRICE)
    assert result["ok"] is False


def test_viewer_cannot_generate_resupply(svc: BatchIntegrityService) -> None:
    result = svc.create_resupply_plan(
        BATCH_MIXED, flow=FLOW_PRICE, actor="只读用户",
        actor_role="viewer", actor_flow=FLOW_PRICE)
    assert result["ok"] is False
    assert "只读" in result["message"]


# ---------------------------------------------------------------------------
# 归属确立：拆分 / 冻结 / 冲突
# ---------------------------------------------------------------------------

def test_split_establishes_single_owner(svc: BatchIntegrityService) -> None:
    result = svc.establish_ownership(
        BATCH_MIXED, mode="split", owner_flow=FLOW_PRICE, owner_person="张三",
        actor="张三", actor_role="flow_owner", actor_flow=FLOW_PRICE)
    assert result["ok"] is True
    ownership = result["ownership"]
    assert ownership["owner_person"] == "张三"
    assert ownership["owner_flow"] == FLOW_PRICE
    assert len(ownership["item_scopes"][FLOW_PRICE]) == 3
    assert len(ownership["item_scopes"][FLOW_ORDER]) == 1
    assert result["report"]["recovery_stage"]["code"] == "S2"


def test_freeze_claims_whole_batch(svc: BatchIntegrityService) -> None:
    result = svc.establish_ownership(
        BATCH_MIXED, mode="freeze", owner_flow=FLOW_PRICE, owner_person="张三",
        actor="张三", actor_role="flow_owner", actor_flow=FLOW_PRICE)
    assert result["ok"] is True
    scopes = result["ownership"]["item_scopes"]
    assert list(scopes) == [FLOW_PRICE]
    assert len(scopes[FLOW_PRICE]) == 4  # 含外部隔离项，整批统一认领


def test_second_owner_conflicts_and_sets_terminal_stage(svc: BatchIntegrityService) -> None:
    first = svc.establish_ownership(
        BATCH_MIXED, mode="split", owner_flow=FLOW_PRICE, owner_person="张三",
        actor="张三", actor_role="flow_owner", actor_flow=FLOW_PRICE)
    assert first["ok"] is True

    second = svc.establish_ownership(
        BATCH_MIXED, mode="freeze", owner_flow=FLOW_ORDER, owner_person="李四",
        actor="平台管理员", actor_role="admin")
    assert second["ok"] is False
    assert second["conflict"] is True
    # 原责任人保留，不被覆盖
    assert second["ownership"]["owner_person"] == "张三"
    # 冲突改变批次补采恢复的第 4 阶段处理终态
    assert second["recovery_stage"]["code"] == "S4"
    assert second["recovery_stage"]["terminal_state"] == "归属冲突待裁决"
    assert svc.build_report(BATCH_MIXED)["recovery_stage"]["code"] == "S4"


def test_conflict_blocks_resupply_until_resolved(svc: BatchIntegrityService) -> None:
    svc.establish_ownership(
        BATCH_MIXED, mode="split", owner_flow=FLOW_PRICE, owner_person="张三",
        actor="张三", actor_role="flow_owner", actor_flow=FLOW_PRICE)
    svc.establish_ownership(
        BATCH_MIXED, mode="freeze", owner_flow=FLOW_ORDER, owner_person="李四",
        actor="平台管理员", actor_role="admin")
    result = svc.create_resupply_plan(
        BATCH_MIXED, flow=FLOW_PRICE, actor="张三",
        actor_role="flow_owner", actor_flow=FLOW_PRICE)
    assert result["ok"] is False
    assert "冲突" in result["message"]


# ---------------------------------------------------------------------------
# 补采任务清单
# ---------------------------------------------------------------------------

def _own(svc: BatchIntegrityService, batch_ref: str = BATCH_MIXED,
         flow: str = FLOW_PRICE, person: str = "张三", mode: str = "split") -> dict:
    result = svc.establish_ownership(
        batch_ref, mode=mode, owner_flow=flow, owner_person=person,
        actor=person, actor_role="flow_owner", actor_flow=flow)
    assert result["ok"] is True
    return result


def test_resupply_plan_covers_only_own_flow(svc: BatchIntegrityService) -> None:
    owned = _own(svc)
    result = svc.create_resupply_plan(
        BATCH_MIXED, flow=FLOW_PRICE, actor="张三",
        actor_role="flow_owner", actor_flow=FLOW_PRICE,
        report_version=owned["report"]["report_version"])
    assert result["ok"] is True
    plan = result["plan"]
    assert plan["version"] == 1
    assert plan["flow"] == FLOW_PRICE
    assert plan["owner_person"] == "张三"
    assert plan["scope_count"] == 3
    assert all(key.startswith(BATCH_MIXED) for key in plan["scope_keys"])
    assert all("-Q0" in key for key in plan["scope_keys"])
    # 只为本流程缺失项写入：不含外部流程的 Q04
    assert "BATC-20261101-01-Q04" not in plan["scope_keys"]


def test_resupply_plan_requires_current_report_version(svc: BatchIntegrityService) -> None:
    owned = _own(svc)
    stale = svc.create_resupply_plan(
        BATCH_MIXED, flow=FLOW_PRICE, actor="张三",
        actor_role="flow_owner", actor_flow=FLOW_PRICE, report_version=1)
    assert stale["ok"] is False
    assert "最新" in stale["message"]
    fresh = svc.create_resupply_plan(
        BATCH_MIXED, flow=FLOW_PRICE, actor="张三",
        actor_role="flow_owner", actor_flow=FLOW_PRICE,
        report_version=owned["report"]["report_version"])
    assert fresh["ok"] is True


def test_resupply_plan_is_idempotent_per_report_version(svc: BatchIntegrityService) -> None:
    owned = _own(svc)
    version = owned["report"]["report_version"]
    first = svc.create_resupply_plan(
        BATCH_MIXED, flow=FLOW_PRICE, actor="张三",
        actor_role="flow_owner", actor_flow=FLOW_PRICE, report_version=version)
    second = svc.create_resupply_plan(
        BATCH_MIXED, flow=FLOW_PRICE, actor="张三",
        actor_role="flow_owner", actor_flow=FLOW_PRICE, report_version=version)
    assert second.get("duplicated") is True
    assert second["plan"]["plan_id"] == first["plan"]["plan_id"]
    assert len(svc.registry.plans_for(BATCH_MIXED, FLOW_PRICE)) == 1


def test_resupply_plan_enqueues_todo_and_updates_batch(svc: BatchIntegrityService) -> None:
    owned = _own(svc, batch_ref=BATCH_RECOVERED, flow=FLOW_PRICE, person="赵六")
    before = len(store.rows("task_queue"))
    result = svc.create_resupply_plan(
        BATCH_RECOVERED, flow=FLOW_PRICE, actor="赵六",
        actor_role="flow_owner", actor_flow=FLOW_PRICE,
        report_version=owned["report"]["report_version"])
    assert result["ok"] is True
    tasks = store.rows("task_queue")
    assert len(tasks) == before + 1
    task = tasks[-1]
    assert task["任务编号"] == result["plan"]["task_ref"]
    assert task["责任人"] == "赵六"
    assert task["status"] == "排队中"
    row = svc.find_batch(BATCH_RECOVERED)
    assert row["status"] == "有遗漏"
    assert row["pending"] is True

    # 同一版本重复生成不重复写待办
    again = svc.create_resupply_plan(
        BATCH_RECOVERED, flow=FLOW_PRICE, actor="赵六",
        actor_role="flow_owner", actor_flow=FLOW_PRICE,
        report_version=owned["report"]["report_version"])
    assert again.get("duplicated") is True
    assert len(store.rows("task_queue")) == before + 1


def test_resupply_plan_respects_single_owner(svc: BatchIntegrityService) -> None:
    owned = _own(svc)
    result = svc.create_resupply_plan(
        BATCH_MIXED, flow=FLOW_PRICE, owner_person="李四", actor="张三",
        actor_role="flow_owner", actor_flow=FLOW_PRICE,
        report_version=owned["report"]["report_version"])
    assert result["ok"] is False
    assert "张三" in result["message"]


def test_freeze_blocks_other_flow_resupply(svc: BatchIntegrityService) -> None:
    _own(svc, mode="freeze")
    price = svc.create_resupply_plan(
        BATCH_MIXED, flow=FLOW_PRICE, actor="张三",
        actor_role="flow_owner", actor_flow=FLOW_PRICE)
    assert price["ok"] is True  # 认领流程可以补采
    order = svc.create_resupply_plan(
        BATCH_MIXED, flow=FLOW_ORDER, actor="李四",
        actor_role="flow_owner", actor_flow=FLOW_ORDER)
    assert order["ok"] is False
    assert "只读" in order["message"] or "冻结" in order["message"]


def test_flow_owner_can_resupply_gap_batch(svc: BatchIntegrityService) -> None:
    owned = _own(svc, batch_ref=BATCH_GAP, flow=FLOW_ORDER, person="王五")
    result = svc.create_resupply_plan(
        BATCH_GAP, flow=FLOW_ORDER, actor="王五", actor_role="flow_owner",
        actor_flow=FLOW_ORDER, report_version=owned["report"]["report_version"])
    assert result["ok"] is True
    assert result["plan"]["scope_count"] == 5  # 3 隔离 + 2 采集缺口
    assert any("-GAP-" in key for key in result["plan"]["scope_keys"])


def test_report_stage_progresses(svc: BatchIntegrityService) -> None:
    assert svc.build_report(BATCH_MIXED)["recovery_stage"]["code"] == "S1"
    owned = _own(svc)
    assert owned["report"]["recovery_stage"]["code"] == "S2"
    result = svc.create_resupply_plan(
        BATCH_MIXED, flow=FLOW_PRICE, actor="张三", actor_role="flow_owner",
        actor_flow=FLOW_PRICE, report_version=owned["report"]["report_version"])
    assert result["report"]["recovery_stage"]["code"] == "S3"


def test_ownership_missing_batch_and_unknown_mode(svc: BatchIntegrityService) -> None:
    assert svc.establish_ownership(
        "BATC-NOT-EXIST", mode="split", owner_flow=FLOW_PRICE,
        owner_person="张三", actor="张三", actor_role="admin")["ok"] is False
    assert svc.establish_ownership(
        BATCH_MIXED, mode="unknown", owner_flow=FLOW_PRICE,
        owner_person="张三", actor="张三", actor_role="admin")["ok"] is False
    assert svc.establish_ownership(
        BATCH_MIXED, mode="split", owner_flow=FLOW_PRICE,
        owner_person="", actor="张三", actor_role="admin")["ok"] is False


def test_only_admin_can_resolve_conflict(svc: BatchIntegrityService) -> None:
    _own(svc)
    conflict = svc.establish_ownership(
        BATCH_MIXED, mode="freeze", owner_flow=FLOW_PRICE, owner_person="李四",
        actor="李四", actor_role="flow_owner", actor_flow=FLOW_PRICE)
    assert conflict["conflict"] is True

    denied = svc.resolve_conflict(
        BATCH_MIXED, keep_owner_person="张三", actor="李四", actor_role="flow_owner")
    assert denied["ok"] is False
    assert svc.build_report(BATCH_MIXED)["recovery_stage"]["code"] == "S4"

    resolved = svc.resolve_conflict(
        BATCH_MIXED, keep_owner_person="张三", actor="平台管理员", actor_role="admin")
    assert resolved["ok"] is True
    assert resolved["ownership"]["conflict"] is False
    assert resolved["ownership"]["owner_person"] == "张三"
    # 裁决后回到归属阶段，可继续补采
    assert resolved["report"]["recovery_stage"]["code"] == "S2"
    plan = svc.create_resupply_plan(
        BATCH_MIXED, flow=FLOW_PRICE, actor="张三", actor_role="flow_owner",
        actor_flow=FLOW_PRICE, report_version=resolved["report"]["report_version"])
    assert plan["ok"] is True


def test_resolve_conflict_must_keep_existing_owner(svc: BatchIntegrityService) -> None:
    _own(svc)
    svc.establish_ownership(
        BATCH_MIXED, mode="freeze", owner_flow=FLOW_PRICE, owner_person="李四",
        actor="李四", actor_role="flow_owner", actor_flow=FLOW_PRICE)
    result = svc.resolve_conflict(
        BATCH_MIXED, keep_owner_person="李四", actor="平台管理员", actor_role="admin")
    assert result["ok"] is False


def test_domain_snapshot_counts_reports(svc: BatchIntegrityService) -> None:
    snapshot = svc.domain_snapshot()
    assert snapshot["reports"] == len(store.rows("batch_tracker"))
    assert snapshot["missed"] >= 3
    assert snapshot["arrivals"]["total"] >= 1
