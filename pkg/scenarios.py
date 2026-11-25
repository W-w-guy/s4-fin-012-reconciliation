# -*- coding: utf-8 -*-
"""核销场景（AC2 / AC3 / AC4 与几条边界），全部在独立快照上执行，可单条手算复核。

每个场景返回 dict：{"id", "desc", "steps", "final"}，
final 里是断言要看的实际值（分期状态、received_cents、HTTP 状态码、未分配余额）。

★ 所有核销调用都走 _safe 包装：业务校验失败被记成 final["code"]，不让异常冒出场景函数。
   理由：变异测试把约束改坏时，断言必须**失败**（打印哪条断言不成立），
   而不是让断言器自己抛异常崩掉——崩溃虽然也是非零退出，但说不清是哪条规则失效。
"""

import os as _os
import sys as _sys
# 允许两种入口：`python -m pkg.server`（从包根起）与 `python pkg/server.py`（直接跑）。
# 同目录的 engine/receipt 是平铺模块，需要把本目录加进 sys.path 才能 import。
_HERE = _os.path.dirname(_os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

from copy import deepcopy

import engine as E
from receipt import ReceiptError, ReceiptService, status_of, unreceived


def _fresh(flows=None):
    return ReceiptService(flows=flows or E.FLOWS)


def _safe(fn):
    """返回 (结果或 None, 错误码)。只在业务校验失败时返回码，程序错误照常抛。"""
    try:
        return fn(), 0
    except ReceiptError as e:
        return None, e.code


def _snap(svc, ids):
    return {i: {"received_cents": svc.inst(i)["received_cents"],
                "status": status_of(svc.inst(i)),
                "unreceived_cents": unreceived(svc.inst(i))} for i in ids}


def _flow_of(fid):
    return next(f for f in E.FLOWS if f["id"] == fid)


# ---------------------------------------------------------------- AC2
def s01_merge_three():
    """合并付款流水 P09（宁波港盛 120000 分）核销 I07+I08+I09 三期 → 三期均 received。"""
    svc = _fresh()
    out, code = _safe(lambda: svc.apply("P09", [
        {"installment_id": "I07", "amount_cents": 60000},
        {"installment_id": "I08", "amount_cents": 30000},
        {"installment_id": "I09", "amount_cents": 30000}], "李核销", mode="manual"))
    return {"id": "S01", "desc": "AC2 合并付款核销三期",
            "steps": [(r["installment_id"], r["amount_cents"]) for r in (out or {}).get("receipts", [])],
            "final": {"code": code,
                      "inst": _snap(svc, ["I07", "I08", "I09"]),
                      "allocated_cents": (out or {}).get("allocated_cents"),
                      "unallocated_cents": (out or {}).get("unallocated_cents"),
                      "receipt_count": len(svc.receipts)}}


# ---------------------------------------------------------------- AC3
def s02_over_limit():
    """核销合计 205000 分 > 流水 120000 分 → 422，且一笔都没写进 payment_receipts。"""
    svc = _fresh()
    code, detail = 0, ""
    try:
        svc.apply("P09", [{"installment_id": "I07", "amount_cents": 60000},
                          {"installment_id": "I08", "amount_cents": 30000},
                          {"installment_id": "I09", "amount_cents": 30000},
                          {"installment_id": "I04", "amount_cents": 85000}],
                  "李核销")
    except ReceiptError as e:
        code, detail = e.code, e.detail
    return {"id": "S02", "desc": "AC3 核销合计超过流水金额 → 422",
            "steps": [("alloc", 60000), ("alloc", 30000), ("alloc", 30000), ("alloc", 85000)],
            "final": {"code": code, "detail": detail,
                      "receipt_count": len(svc.receipts),
                      "inst": _snap(svc, ["I07", "I08", "I09", "I04"])}}


def s02b_over_unreceived():
    """单期分摊超过该期未收金额 → 422（与 AC3 是两条不同约束）。"""
    svc = _fresh()
    code, detail = 0, ""
    try:
        svc.apply("P01", [{"installment_id": "I08", "amount_cents": 40000}], "李核销")
    except ReceiptError as e:
        code, detail = e.code, e.detail
    return {"id": "S02b", "desc": "单期分摊超过未收金额 → 422",
            "final": {"code": code, "detail": detail, "receipt_count": len(svc.receipts)}}


# ---------------------------------------------------------------- AC4
def s03_partial():
    """部分付款：P11（上海启元 50000 分）核销 I11 → partially_received，received_cents=50000。"""
    svc = _fresh()
    out, code = _safe(lambda: svc.apply(
        "P11", [{"installment_id": "I11", "amount_cents": 50000}], "王出纳", mode="manual"))
    return {"id": "S03", "desc": "AC4 部分付款",
            "steps": [("I11", 50000)],
            "final": {"code": code,
                      "inst": _snap(svc, ["I11"]),
                      "receipts": [(r["installment_id"], r["amount_cents"], r["installment_status"])
                                   for r in (out or {}).get("receipts", [])],
                      "unallocated_cents": (out or {}).get("unallocated_cents")}}


def s03b_partial_twice():
    """同一分期两次部分付款累计正好满额 → 第二次后转 received（比例闸门的另半）。"""
    svc = _fresh()
    _o1, c1 = _safe(lambda: svc.apply(
        "P11", [{"installment_id": "I11", "amount_cents": 30000}], "王出纳"))
    mid = _snap(svc, ["I11"])
    _o2, c2 = _safe(lambda: svc.apply(
        "P15", [{"installment_id": "I11", "amount_cents": 40000}], "王出纳"))
    return {"id": "S03b", "desc": "两次部分付款累计满额转 received",
            "final": {"code_first": c1, "code_second": c2,
                      "after_first": mid, "after_second": _snap(svc, ["I11"])}}


# ---------------------------------------------------------------- 其它边界
def s04_auto_confirm():
    """auto_confirm_strong=true 时 R1 命中即自动核销（mode=auto），false 时只出建议。"""
    off = E.match_flow(_flow_of("P01"), deepcopy(E.INITIAL_INSTALLMENTS), auto_confirm=False)
    on = E.match_flow(_flow_of("P01"), deepcopy(E.INITIAL_INSTALLMENTS), auto_confirm=True)
    r2_on = E.match_flow(_flow_of("P06"), deepcopy(E.INITIAL_INSTALLMENTS), auto_confirm=True)
    return {"id": "S04", "desc": "auto_confirm_strong 开关只影响 R1",
            "final": {"off": off["auto_confirmed"], "on": on["auto_confirmed"],
                      "r2_with_switch_on": r2_on["auto_confirmed"]}}


def s05_unallocated():
    """一笔流水只核销一部分分期时，剩余记为未分配余额（不得凭空消化）。"""
    svc = _fresh()
    out, code = _safe(lambda: svc.apply(
        "P09", [{"installment_id": "I07", "amount_cents": 60000}], "李核销"))
    view = svc.flow_view("P09")
    return {"id": "S05", "desc": "剩余部分记为未分配余额",
            "final": {"code": code,
                      "unallocated_cents": (out or {}).get("unallocated_cents"),
                      "view_unallocated": view["unallocated_cents"],
                      "allocated_cents": view["allocated_cents"],
                      "inst": _snap(svc, ["I07", "I08"])}}


def s06_idempotent():
    """同一 idempotency_key 重放不重复入账。"""
    svc = _fresh()
    a, ca = _safe(lambda: svc.apply("P09", [{"installment_id": "I07", "amount_cents": 60000}],
                                    "李核销", idempotency_key="dedupe-P09-1"))
    b, cb = _safe(lambda: svc.apply("P09", [{"installment_id": "I07", "amount_cents": 60000}],
                                    "李核销", idempotency_key="dedupe-P09-1"))
    return {"id": "S06", "desc": "幂等键重放不重复入账",
            "final": {"code_first": ca, "code_second": cb,
                      "first_receipts": len((a or {}).get("receipts", [])),
                      "replay_flag": (b or {}).get("idempotent_replay"),
                      "replay_returned_receipts": len([r for r in (b or {}).get("receipts", [])
                                                       if r.get("id")]),
                      "receipt_count": len(svc.receipts),
                      "inst": _snap(svc, ["I07"])}}


def s07_sequential():
    """三条流水依次核销宁波港盛三期，最终三期均 received（核销是累积的，不是彼此覆盖）。"""
    svc = _fresh()
    codes = []
    for fid, iid, amt in (("P03", "I07", 60000), ("P06", "I08", 30000), ("P18", "I09", 30000)):
        _o, c = _safe(lambda f=fid, i=iid, a=amt: svc.apply(
            f, [{"installment_id": i, "amount_cents": a}], "张核销"))
        codes.append(c)
    return {"id": "S07", "desc": "三条流水依次核销三期",
            "final": {"codes": codes, "inst": _snap(svc, ["I07", "I08", "I09"]),
                      "receipt_count": len(svc.receipts)}}


def s08_bad_requests():
    """四类非法请求：分期不存在 404、金额为零 422、重复分摊 422、空分摊 422。"""
    cases = [
        ("分期不存在", [{"installment_id": "I99", "amount_cents": 100}], 404),
        ("金额为零", [{"installment_id": "I07", "amount_cents": 0}], 422),
        ("重复分摊", [{"installment_id": "I07", "amount_cents": 100},
                      {"installment_id": "I07", "amount_cents": 200}], 422),
        ("空分摊", [], 422),
    ]
    got = []
    for name, alloc, _exp in cases:
        svc = _fresh()
        _o, code = _safe(lambda a=alloc: svc.apply("P09", a, "张核销"))
        got.append((name, code))
    return {"id": "S08", "desc": "四类非法请求的返回码",
            "final": {"got": got, "expect": [(n, c) for n, _, c in cases]}}


ALL = [s01_merge_three, s02_over_limit, s02b_over_unreceived, s03_partial,
       s03b_partial_twice, s04_auto_confirm, s05_unallocated, s06_idempotent,
       s07_sequential, s08_bad_requests]


def run_all():
    return [f() for f in ALL]
