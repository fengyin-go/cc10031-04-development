"""批次完整性对账的示例数据：采集记录、隔离记录、补采到达批次。

采集批次本身仍在 ``app.seed.SEED_ROWS["batch_tracker"]`` 中维护，这里补齐
对账所需的三个数据源，使目标数/成功数/隔离数可以互相勾稽：

- 采集记录：批次实际入库的结构化数据行（CollectRecord）
- 隔离记录：校验失败或解析异常被隔离的数据行（DirtyRecord），带责任流程
- 补采到达批次：新到达批次把早先隔离记录恢复回来的凭据（ResupplyArrival）

数据刻意覆盖三种场景：
1. 同批次同时存在"流程内遗漏"和"外部隔离记录"
2. 目标数 > 采集数 + 隔离数，存在"采集缺口"
3. 隔离记录已被新到达批次恢复，不再计入待补采遗漏
"""
from __future__ import annotations

from typing import Any

# 批次编号 -> 采集记录条数，与 batch_tracker 的"成功条数"保持一致
COLLECT_COUNTS: dict[str, int] = {
    "BATC-20261101-01": 20,
    "BATC-20261101-02": 30,
    "BATC-20261102-01": 12,
    "BATC-20261102-02": 20,
    "BATC-20261103-01": 16,
    "BATC-20261103-02": 8,
}

# 隔离记录：来源模块、责任流程、原因、是否已被新到达批次恢复
QUARANTINE_SPECS: list[dict[str, str]] = [
    # 批次1：流程内 3 条 + 外部流程隔离 1 条（混合归属场景）
    {"batch_ref": "BATC-20261101-01", "source": "data_extract",
     "flow": "商品价格采集流程", "reason": "字段缺失",
     "record_key": "BATC-20261101-01-Q01", "recovered_to": ""},
    {"batch_ref": "BATC-20261101-01", "source": "data_extract",
     "flow": "商品价格采集流程", "reason": "格式非法",
     "record_key": "BATC-20261101-01-Q02", "recovered_to": ""},
    {"batch_ref": "BATC-20261101-01", "source": "data_extract",
     "flow": "商品价格采集流程", "reason": "解析超时",
     "record_key": "BATC-20261101-01-Q03", "recovered_to": ""},
    {"batch_ref": "BATC-20261101-01", "source": "result_verify",
     "flow": "订单明细采集流程", "reason": "外部隔离：字段校验未通过",
     "record_key": "BATC-20261101-01-Q04", "recovered_to": ""},
    # 批次3：流程内 3 条 + 外部流程隔离 1 条，另存在 2 条采集缺口
    {"batch_ref": "BATC-20261102-01", "source": "result_verify",
     "flow": "订单明细采集流程", "reason": "字段缺失",
     "record_key": "BATC-20261102-01-Q01", "recovered_to": ""},
    {"batch_ref": "BATC-20261102-01", "source": "result_verify",
     "flow": "订单明细采集流程", "reason": "格式非法",
     "record_key": "BATC-20261102-01-Q02", "recovered_to": ""},
    {"batch_ref": "BATC-20261102-01", "source": "result_verify",
     "flow": "订单明细采集流程", "reason": "重复主键",
     "record_key": "BATC-20261102-01-Q03", "recovered_to": ""},
    {"batch_ref": "BATC-20261102-01", "source": "data_extract",
     "flow": "商品价格采集流程", "reason": "外部隔离：解析超时",
     "record_key": "BATC-20261102-01-Q04", "recovered_to": ""},
    # 批次6：2 条隔离，其中 1 条已被新到达批次 BATC-20261105-01 恢复
    {"batch_ref": "BATC-20261103-02", "source": "data_extract",
     "flow": "商品价格采集流程", "reason": "解析超时",
     "record_key": "BATC-20261103-02-Q01", "recovered_to": "BATC-20261105-01"},
    {"batch_ref": "BATC-20261103-02", "source": "data_extract",
     "flow": "商品价格采集流程", "reason": "字段缺失",
     "record_key": "BATC-20261103-02-Q02", "recovered_to": ""},
]

# 新到达批次补采恢复的隔离记录：新批次把来源批次的隔离项恢复回来
ARRIVAL_SPECS: list[dict[str, Any]] = [
    {
        "new_batch_ref": "BATC-20261105-01",
        "source_batch_ref": "BATC-20261103-02",
        "quarantine_key": "BATC-20261103-02-Q01",
        "flow": "商品价格采集流程",
        "arrived_at": "2026-11-05 09:20",
        "handled_by": "商品价格采集流程",
    },
]


def iter_collect_entries() -> list[dict[str, Any]]:
    """按批次生成确定性的采集记录。"""
    entries: list[dict[str, Any]] = []
    flow_by_batch = {
        "BATC-20261101-01": "商品价格采集流程",
        "BATC-20261101-02": "商品价格采集流程",
        "BATC-20261102-01": "订单明细采集流程",
        "BATC-20261102-02": "订单明细采集流程",
        "BATC-20261103-01": "库存快照采集流程",
        "BATC-20261103-02": "商品价格采集流程",
    }
    for batch_ref, count in COLLECT_COUNTS.items():
        flow = flow_by_batch[batch_ref]
        for index in range(1, count + 1):
            entries.append({
                "batch_ref": batch_ref,
                "source": "data_extract",
                "record_key": f"{batch_ref}-R{index:03d}",
                "fields": {
                    "序号": index,
                    "名称": f"{flow}-记录-{index:03d}",
                    "来源流程": flow,
                    "采集批次": batch_ref,
                },
                "validated": True,
            })
    return entries


def seed_batch_integrity(store: Any) -> None:
    """把采集记录、隔离记录和补采到达批次写入内存仓库。"""
    key_to_record_id: dict[str, int] = {}

    for entry in iter_collect_entries():
        record = store.collect_records.restore(
            batch_ref=entry["batch_ref"],
            source=entry["source"],
            fields=entry["fields"],
            record_key=entry["record_key"],
            validated=entry["validated"],
        )
        key_to_record_id[entry["record_key"]] = record.entry_id

    for spec in QUARANTINE_SPECS:
        recovered_to = spec.get("recovered_to") or None
        record = store.dirty_vault.restore(
            source=spec["source"],
            batch_ref=spec["batch_ref"],
            raw_data={
                "记录键": spec["record_key"],
                "来源批次": spec["batch_ref"],
                "责任流程": spec["flow"],
            },
            reason=spec["reason"],
            flow=spec["flow"],
            record_key=spec["record_key"],
            quarantined=recovered_to is None,
            recovered_to=recovered_to,
        )
        key_to_record_id[spec["record_key"]] = record.record_id

    for arrival in ARRIVAL_SPECS:
        store.arrivals.add(
            new_batch_ref=arrival["new_batch_ref"],
            source_batch_ref=arrival["source_batch_ref"],
            quarantine_record_id=key_to_record_id.get(arrival["quarantine_key"], 0),
            recovered_key=arrival["quarantine_key"],
            flow=arrival["flow"],
            arrived_at=arrival["arrived_at"],
            handled_by=arrival["handled_by"],
        )
