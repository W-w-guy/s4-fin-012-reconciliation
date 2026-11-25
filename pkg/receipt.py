# -*- coding: utf-8 -*-
"""核销服务：把流水金额分摊到分期，写 payment_receipts，同步更新分期状态。

工单原文（【本单范围】末段）：
  「核销写 payment_receipts（流水 ID、分期 ID、核销金额（分）、核销人、方式 auto / manual），
    同步更新分期 received_cents 与状态；一笔流水可核销多个分期，合计不得超过流水金额，
    剩余部分记为未分配余额。」

约束（都会返回 422，业务校验失败而不是 500）：
  1. 分摊合计 > 流水金额         → 422 合计超过流水金额
  2. 某分期分摊额 > 该期未收金额 → 422 超出未收金额
  3. 分摊金额 <= 0 或非整数      → 422 金额必须为正整数分
  4. 同一请求内同一分期出现两次  → 422 重复分摊
资源不存在返回 404；已按同一流水重复提交且幂等键相同则返回既有结果（幂等）。
"""

import os as _os
import sys as _sys
# 允许两种入口：`python -m pkg.server`（从包根起）与 `python pkg/server.py`（直接跑）。
# 同目录的 engine/receipt 是平铺模块，需要把本目录加进 sys.path 才能 import。
_HERE = _os.path.dirname(_os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

from copy import deepcopy

from engine import INITIAL_INSTALLMENTS, alloc_overflow, status_of, unreceived


class ReceiptError(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class ReceiptService:
    """内存实现，可被 HTTP 层或测试直接调用；每次实例化都是一份独立快照。"""

    def __init__(self, installments=None, flows=None):
        self.installments = deepcopy(installments if installments is not None
                                     else INITIAL_INSTALLMENTS)
        self.flows = {f["id"]: deepcopy(f) for f in (flows or [])}
        self.receipts = []          # payment_receipts 表的内存形态
        self.unallocated = {}       # flow_id -> 未分配余额（分）
        self._seq = 0

    # ---------------- 查询
    def inst(self, iid):
        for i in self.installments:
            if i["id"] == iid:
                return i
        return None

    def flow(self, fid):
        return self.flows.get(fid)

    def allocated_total(self, fid):
        return sum(r["amount_cents"] for r in self.receipts if r["flow_id"] == fid)

    def flow_view(self, fid):
        f = self.flow(fid)
        if not f:
            raise ReceiptError(404, "流水 %s 不存在" % fid)
        used = self.allocated_total(fid)
        return {"flow_id": fid, "amount_cents": f["amount_cents"],
                "allocated_cents": used,
                "unallocated_cents": f["amount_cents"] - used,
                "receipts": [r for r in self.receipts if r["flow_id"] == fid]}

    def inst_view(self, iid):
        i = self.inst(iid)
        if not i:
            raise ReceiptError(404, "分期 %s 不存在" % iid)
        return {"installment_id": iid, "total_cents": i["total_cents"],
                "received_cents": i["received_cents"],
                "unreceived_cents": unreceived(i), "status": status_of(i)}

    # ---------------- 核销
    def apply(self, fid, allocations, operator, mode="manual", idempotency_key=None):
        f = self.flow(fid)
        if not f:
            raise ReceiptError(404, "流水 %s 不存在" % fid)
        if idempotency_key:
            for r in self.receipts:
                if r.get("idempotency_key") == idempotency_key and r["flow_id"] == fid:
                    return {"flow_id": fid, "idempotent_replay": True,
                            "receipts": [r for r in self.receipts if r["flow_id"] == fid],
                            "unallocated_cents": self.flow_view(fid)["unallocated_cents"]}
        if not isinstance(allocations, list) or not allocations:
            raise ReceiptError(422, "allocations 不能为空")
        if mode not in ("auto", "manual"):
            raise ReceiptError(422, "mode 只能是 auto 或 manual")
        if not operator:
            raise ReceiptError(422, "operator（核销人）不能为空")

        seen = set()
        clean = []
        for a in allocations:
            iid = a.get("installment_id")
            amt = a.get("amount_cents")
            if not isinstance(amt, int) or isinstance(amt, bool) or amt <= 0:
                raise ReceiptError(422, "分摊金额必须为正整数（分）：%r" % (amt,))
            if iid in seen:
                raise ReceiptError(422, "同一请求内分期 %s 重复分摊" % iid)
            seen.add(iid)
            inst = self.inst(iid)
            if not inst:
                raise ReceiptError(404, "分期 %s 不存在" % iid)
            if amt > unreceived(inst):
                raise ReceiptError(422, "分期 %s 分摊 %d 分超出未收金额 %d 分"
                                   % (iid, amt, unreceived(inst)))
            clean.append((iid, amt))

        total = sum(a for _, a in clean)
        if alloc_overflow(total, f["amount_cents"]):
            raise ReceiptError(422, "核销合计 %d 分超过流水金额 %d 分"
                               % (total, f["amount_cents"]))

        made = []
        for iid, amt in clean:
            inst = self.inst(iid)
            inst["received_cents"] += amt
            self._seq += 1
            rec = {"id": "R%04d" % self._seq, "flow_id": fid, "installment_id": iid,
                   "amount_cents": amt, "operator": operator, "mode": mode,
                   "installment_status": status_of(inst)}
            if idempotency_key:
                rec["idempotency_key"] = idempotency_key
            self.receipts.append(rec)
            made.append(rec)
        return {"flow_id": fid, "receipts": made,
                "allocated_cents": total,
                "unallocated_cents": f["amount_cents"] - self.allocated_total(fid)}
