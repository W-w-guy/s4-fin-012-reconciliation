# -*- coding: utf-8 -*-
"""本单 31 条 AC 断言。期望值一律**硬编码在本文件里**，与实际运行结果比对，
不从被测对象反推（避免「自己跟自己比」的假绿）。"""
import csv
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

import engine as E
import scenarios as SC
from kitlib.checks import check

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")

# ---------------- 硬编码期望（独立于 engine，故意手写一遍） ----------------
EXPECT = {
    # flow_id: (rule, target)
    "P01": ("R1", "I01"), "P02": ("R1", "I04"), "P03": ("R1", "I07"),
    "P04": ("R1", "I10"), "P05": ("R1", "I02"),
    "P06": ("R2", "I07"), "P07": ("R2", "I05"),
    "P16": ("R2", "I01"), "P18": ("R2", "I08"),
    "P08": ("R3", "I01"), "P09": ("R3", "I07"), "P10": ("R3", "I05"),
    "P15": ("R3", "I11"), "P17": ("R3", "I04"), "P20": ("R3", "I01"),
    "P11": ("R4", "I11"), "P12": ("R4", "I02"),
    "P13": ("unmatched", ""), "P14": ("unmatched", ""), "P19": ("unmatched", ""),
}
EXPECT_CANDS = {
    "P01": ["I01"], "P02": ["I04"], "P03": ["I07"], "P04": ["I10"], "P05": ["I02"],
    "P06": ["I07"], "P07": ["I05", "I06"], "P16": ["I01"], "P18": ["I08", "I09"],
    "P08": ["I01", "I02"], "P09": ["I07", "I08", "I09"], "P10": ["I05", "I06"],
    "P15": ["I11", "I12"], "P17": ["I04", "I05", "I06"], "P20": ["I01", "I02", "I03"],
    "P11": ["I11"], "P12": ["I02"],
    "P13": [], "P14": [], "P19": [],
}
# 覆盖类 -> 硬编码的流水清单（不能只判「每类至少 1」，要判具体是谁）
EXPECT_CATS = {
    "C01": ["P01", "P02", "P03", "P04", "P05"],
    "C02": ["P06", "P07", "P16", "P18"],
    "C03": ["P08", "P09", "P10", "P15", "P17", "P20"],
    "C04": ["P11", "P12"],
    "C05": ["P13", "P14", "P19"],
    "C06": ["P06"],
    "C07": ["P07", "P18"],
}
EXPECT_SCORE = {"R1": "1.00", "R2": "0.85", "R3": "0.70", "R4": "0.55", "unmatched": "0.00"}
EXPECT_N_FLOWS = 20
EXPECT_N_INST = 13

# 场景期望（同样手写）
EXPECT_S01 = {  # AC2：三期全 received
    "I07": (60000, "received"), "I08": (30000, "received"), "I09": (30000, "received"),
}
EXPECT_S03_RECEIVED = 50000          # AC4：I11 只收到 50000
EXPECT_S03_UNRECEIVED = 20000
EXPECT_S03_STATUS = "partially_received"
EXPECT_OVERFLOW_DETAIL = "核销合计 205000 分超过流水金额 120000 分"


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _load(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


def _csv(rel):
    return list(csv.DictReader(open(os.path.join(ROOT, rel), encoding="utf-8")))


def _http(base, method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def main():
    # ================= AC1：20 条流水覆盖 R1–R4 与无匹配，结果与期望表一致 =================
    check("V01 流水条数为 20、分期初始条数为 13",
          len(E.FLOWS) == EXPECT_N_FLOWS and len(E.INITIAL_INSTALLMENTS) == EXPECT_N_INST,
          "流水 %d / 分期 %d" % (len(E.FLOWS), len(E.INITIAL_INSTALLMENTS)))

    check("V02 流水号连续且无重复（P01~P20）",
          [f["id"] for f in E.FLOWS] == ["P%02d" % i for i in range(1, 21)],
          "实测前三条 %s … 共 %d 条" % ([f["id"] for f in E.FLOWS][:3], len(E.FLOWS)))

    got = {f["id"]: (E.evaluate(f)["rule"], E.evaluate(f)["target_installment"]) for f in E.FLOWS}
    mm = [(k, got[k], EXPECT[k]) for k in EXPECT if got[k] != EXPECT[k]]
    check("V03 20 条流水的命中规则与目标分期逐条等于手写期望表",
          not mm, "不一致 %s" % (mm or "无"))

    got_c = {f["id"]: E.evaluate(f)["candidates"] for f in E.FLOWS}
    mm_c = [(k, got_c[k], EXPECT_CANDS[k]) for k in EXPECT_CANDS if got_c[k] != EXPECT_CANDS[k]]
    check("V04 候选/组合逐条等于手写期望（含 R2 多候选与 R3 组合明细）",
          not mm_c, "不一致 %s" % (mm_c or "无"))

    got_s = {f["id"]: "%.2f" % E.evaluate(f)["score"] for f in E.FLOWS}
    mm_s = [(k, got_s[k], EXPECT_SCORE[EXPECT[k][0]]) for k in EXPECT
            if got_s[k] != EXPECT_SCORE[EXPECT[k][0]]]
    check("V05 分数严格按规则固定值（R1=1.00 / R2=0.85 / R3=0.70 / R4=0.55 / unmatched=0.00）",
          not mm_s, "不一致 %s" % (mm_s or "无"))

    cats = {c: sorted(f["id"] for f in E.FLOWS if c in f["cats"]) for c, _ in E.CATS}
    mm_cat = [(c, cats[c], EXPECT_CATS[c]) for c in EXPECT_CATS if cats[c] != EXPECT_CATS[c]]
    check("V06 7 类覆盖的流水清单逐类等于硬编码清单（不是只判每类≥1）",
          not mm_cat, "不一致 %s" % (mm_cat or "无"))

    check("V07 5 类必覆盖（R1/R2/R3/R4/无匹配）全部非空且无匹配有 3 条",
          all(len(EXPECT_CATS[c]) >= 1 for c in ("C01", "C02", "C03", "C04", "C05"))
          and len(EXPECT_CATS["C05"]) == 3,
          "R1=%d R2=%d R3=%d R4=%d 无匹配=%d"
          % tuple(len(EXPECT_CATS[c]) for c in ("C01", "C02", "C03", "C04", "C05")))

    allids = set(i for v in cats.values() for i in v)
    check("V08 7 类并集覆盖全部 20 条流水（无遗漏）",
          allids == set(f["id"] for f in E.FLOWS) and len(allids) == EXPECT_N_FLOWS,
          "覆盖 %d 条" % len(allids))

    rows = _csv("docs/expected_results.csv")
    # CSV 里 unmatched 的目标分期写作 "(无)"，比对前统一转回空串（否则是格式差异冒充内容差异）
    mm_csv = [(r["flow_id"], r["expected_rule"], r["expected_target"])
              for r in rows
              if (r["expected_rule"],
                  "" if r["expected_target"] == "(无)" else r["expected_target"])
              != EXPECT.get(r["flow_id"], (None, None))]
    check("V09 交付的期望结果表 20 行与手写期望表逐条一致",
          len(rows) == EXPECT_N_FLOWS and not mm_csv,
          "行数 %d；不一致 %s" % (len(rows), mm_csv or "无"))

    check("V10 期望结果表每行都记录了命中规则与分数（工单原文要求）",
          all(r["expected_rule"] and r["expected_score"] for r in rows),
          "缺列行数 %d" % sum(1 for r in rows if not (r["expected_rule"] and r["expected_score"])))

    check("V11 三条 unmatched 流水都给出了未命中原因（不是空白）",
          all(E.evaluate(f)["why"] and E.evaluate(f)["why"] != "R1/R2/R3/R4 均未命中"
              for f in E.FLOWS if E.evaluate(f)["rule"] == "unmatched"),
          "；".join("%s→%s" % (f["id"], E.evaluate(f)["why"])
                   for f in E.FLOWS if E.evaluate(f)["rule"] == "unmatched"))

    # 归一化本身要被直接测到（否则 R2 通过可能是巧合）
    check("V12 归一化去掉半角/全角空格与四类括号字符本身",
          E.norm(" 杭州云栖智造（科技）有限公司 ") == "杭州云栖智造科技有限公司"
          and E.norm("宁波港盛供应链　有限公司") == "宁波港盛供应链有限公司"
          and E.norm("A【B】[C](D)") == "ABCD",
          "三种输入 → %s / %s / %s" % (
              E.norm(" 杭州云栖智造（科技）有限公司 "),
              E.norm("宁波港盛供应链　有限公司"), E.norm("A【B】[C](D)")))

    # 规则优先级：同一流水若同时满足 R2 与 R3，必须是 R2（更靠前）
    multi = dict(id="X1", payer="上海启元低碳材料有限公司", amount_cents=70000, memo="")
    rx = E.match_flow(multi, E.INITIAL_INSTALLMENTS)
    check("V13 优先级生效：70000 分同时满足 R2（I11 未收）与 R3（I11+I12 需 140000 不成立）时取 R2",
          rx["rule"] == "R2" and rx["target_installment"] == "I11",
          "实测 %s / %s" % (rx["rule"], rx["target_installment"]))

    check("V14 优先级反证：R1 命中时不会退到 R2（P01 的 R1 与 R2 都成立）",
          E.match_flow(next(f for f in E.FLOWS if f["id"] == "P01"),
                       E.INITIAL_INSTALLMENTS)["rule"] == "R1"
          and "R2" not in E.evaluate(next(f for f in E.FLOWS if f["id"] == "P01"))["reasons"],
          "P01 命中 %s，reasons 键 %s"
          % (E.evaluate(next(f for f in E.FLOWS if f["id"] == "P01"))["rule"],
             list(E.evaluate(next(f for f in E.FLOWS if f["id"] == "P01"))["reasons"])))


    p15 = E.evaluate(next(f for f in E.FLOWS if f["id"] == "P15"))
    check("V14b R3 多组命中：P15 同时满足 I11+I12 与 I12+I13，取期次最靠前一组建议并列出全部组合",
          p15["rule"] == "R3" and p15["candidates"] == ["I11", "I12"]
          and p15["combos"] == [["I11", "I12"], ["I12", "I13"]],
          "采纳 %s；全部组合 %s" % (p15["candidates"], p15["combos"]))

    # ================= AC2：合并付款核销后 3 个分期均为 received =================
    s01 = SC.s01_merge_three()
    inst = s01["final"]["inst"]
    check("V15 AC2 合并付款 P09 核销 I07+I08+I09 后三期均为 received",
          all(inst[i]["status"] == "received" for i in EXPECT_S01),
          "；".join("%s=%s" % (i, inst[i]["status"]) for i in sorted(inst)))

    check("V16 AC2 三期 received_cents 分别等于各自总额（60000/30000/30000）",
          all(inst[i]["received_cents"] == EXPECT_S01[i][0] for i in EXPECT_S01)
          and all(inst[i]["unreceived_cents"] == 0 for i in EXPECT_S01),
          "；".join("%s=%d" % (i, inst[i]["received_cents"]) for i in sorted(inst)))

    check("V17 AC2 一笔流水产生 3 条核销记录，合计 120000 分、未分配 0",
          s01["final"]["receipt_count"] == 3
          and s01["final"]["allocated_cents"] == 120000
          and s01["final"]["unallocated_cents"] == 0,
          "记录 %s 条 / 已分配 %s / 未分配 %s / code=%s"
          % (s01["final"]["receipt_count"], s01["final"]["allocated_cents"],
             s01["final"]["unallocated_cents"], s01["final"].get("code")))

    check("V18 AC2 场景源流水 P09 由引擎判为 R3 且组合恰为 I07+I08+I09",
          E.evaluate(next(f for f in E.FLOWS if f["id"] == "P09"))["rule"] == "R3"
          and E.evaluate(next(f for f in E.FLOWS if f["id"] == "P09"))["candidates"]
          == ["I07", "I08", "I09"],
          "规则 %s / 组合 %s"
          % (E.evaluate(next(f for f in E.FLOWS if f["id"] == "P09"))["rule"],
             E.evaluate(next(f for f in E.FLOWS if f["id"] == "P09"))["candidates"]))

    # ================= AC3：核销合计超过流水金额返回 422 =================
    s02 = SC.s02_over_limit()
    check("V19 AC3 应用层：合计 205000 > 流水 120000 抛 422 且错误信息含具体数字",
          s02["final"]["code"] == 422
          and s02["final"]["detail"] == EXPECT_OVERFLOW_DETAIL,
          "code=%s detail=%s" % (s02["final"]["code"], s02["final"]["detail"]))

    check("V20 AC3 校验先于写入：失败后 payment_receipts 一条都没有（不是部分落账）",
          s02["final"]["receipt_count"] == 0
          and all(v["received_cents"] == 0 for v in s02["final"]["inst"].values()),
          "记录数 %d；分期已收 %s"
          % (s02["final"]["receipt_count"],
             {k: v["received_cents"] for k, v in s02["final"]["inst"].items()}))

    # HTTP 层真发请求：AC3 的原文是「返回 422」，必须走真实 HTTP 拿状态码
    sys.path.insert(0, os.path.join(ROOT, "pkg"))
    import server as SV
    srv, base, _t, _svc = SV.serve(0)
    try:
        code_h, body_h = _http(base, "POST", "/api/v1/flows/P09/receipts", {
            "allocations": [{"installment_id": "I07", "amount_cents": 60000},
                            {"installment_id": "I08", "amount_cents": 30000},
                            {"installment_id": "I09", "amount_cents": 30000},
                            {"installment_id": "I04", "amount_cents": 85000}],
            "operator": "李核销", "mode": "manual"})
        code_ok, body_ok = _http(base, "POST", "/api/v1/flows/P09/receipts", {
            "allocations": [{"installment_id": "I07", "amount_cents": 120000}],
            "operator": "李核销", "mode": "manual"})
        # 120000 分超过 I07 未收（60000），应为 422；换一个合法请求验 201
        code_201, body_201 = _http(base, "POST", "/api/v1/flows/P03/receipts", {
            "allocations": [{"installment_id": "I07", "amount_cents": 60000}],
            "operator": "张核销", "mode": "manual"})
        code_404, _b404 = _http(base, "GET", "/api/v1/installments/I99")
        code_health, _bh = _http(base, "GET", "/api/v1/health")
    finally:
        srv.shutdown()

    check("V21 AC3 HTTP 层：超限请求真实返回 422，detail 与期望逐字相同（实测非 import 内部函数）",
          code_h == 422 and body_h.get("detail") == EXPECT_OVERFLOW_DETAIL,
          "HTTP %s body=%s" % (code_h, json.dumps(body_h, ensure_ascii=False)))

    check("V22 单期超未收也返回 422（与合计超限是两条独立约束）",
          code_ok == 422,
          "HTTP %s body=%s" % (code_ok, json.dumps(body_ok, ensure_ascii=False)[:120]))

    check("V23 合法核销返回 201，不存在的分期返回 404，健康检查 200（反证 V21 不是恒 422）",
          code_201 == 201 and code_404 == 404 and code_health == 200,
          "201→%s / 404→%s / health→%s"
          % (code_201, code_404, code_health))

    check("V24 应用层单期超未收同样 422（S02b）",
          s02b_code(SC) == 422, "code=%s" % s02b_code(SC))

    # ================= AC4：部分付款后 partially_received 且 received_cents 正确 =================
    s03 = SC.s03_partial()
    i11 = s03["final"]["inst"]["I11"]
    check("V25 AC4 部分付款 50000 分后 I11 状态为 partially_received",
          i11["status"] == EXPECT_S03_STATUS,
          "status=%s" % i11["status"])

    check("V26 AC4 received_cents 恰为 50000、未收余 20000（总额 70000 减 50000）",
          i11["received_cents"] == EXPECT_S03_RECEIVED
          and i11["unreceived_cents"] == EXPECT_S03_UNRECEIVED,
          "received=%s unreceived=%s" % (i11["received_cents"], i11["unreceived_cents"]))

    rec = [tuple(x) for x in s03["final"]["receipts"]]
    check("V27 AC4 核销记录里记录的分期状态同步为 partially_received（不是只改表不回执）",
          rec == [("I11", 50000, "partially_received")],
          "实记 %s" % (rec,))

    s03b = SC.s03b_partial_twice()
    check("V28 两次部分付款累计满额后转 received（30000+40000=70000）",
          s03b["final"]["after_first"]["I11"]["status"] == "partially_received"
          and s03b["final"]["after_second"]["I11"]["status"] == "received"
          and s03b["final"]["after_second"]["I11"]["received_cents"] == 70000,
          "第一次 %s / 第二次 %s=%s"
          % (s03b["final"]["after_first"]["I11"]["status"],
             s03b["final"]["after_second"]["I11"]["status"],
             s03b["final"]["after_second"]["I11"]["received_cents"]))

    # ================= 其它交付一致性 =================
    s04 = SC.s04_auto_confirm()
    check("V29 auto_confirm_strong 只影响 R1：关闭时 false、打开时 true、R2 即使打开也 false",
          s04["final"] == {"off": False, "on": True, "r2_with_switch_on": False},
          "%s" % s04["final"])

    s05 = SC.s05_unallocated()
    f5 = s05["final"]
    check("V30 剩余部分记为未分配余额 60000，且不凭空消化成已核销",
          f5["unallocated_cents"] == 60000 and f5["view_unallocated"] == 60000
          and f5["allocated_cents"] == 60000
          and f5["inst"]["I08"]["received_cents"] == 0
          and not f5.get("code"),
          "code=%s unallocated=%s allocated=%s I08=%s"
          % (f5.get("code"), f5["unallocated_cents"], f5["allocated_cents"],
             f5["inst"]["I08"]["received_cents"]))

    s06 = SC.s06_idempotent()
    check("V31 幂等键重放不重复入账：两次同 key 后 payment_receipts 仅 1 条",
          s06["final"]["receipt_count"] == 1 and s06["final"]["replay_flag"] is True,
          "记录 %d 条 / 重放标记 %s"
          % (s06["final"]["receipt_count"], s06["final"]["replay_flag"]))

    s07 = SC.s07_sequential()
    check("V32 三条流水依次核销三期后全 received（核销是累积而非覆盖）",
          all(s07["final"]["inst"][i]["status"] == "received" for i in ("I07", "I08", "I09"))
          and s07["final"]["receipt_count"] == 3
          and s07["final"]["codes"] == [0, 0, 0],
          "；".join("%s=%s" % (i, s07["final"]["inst"][i]["status"])
                   for i in ("I07", "I08", "I09")))

    s08 = SC.s08_bad_requests()
    check("V33 四类非法请求返回码与期望逐条一致（404/422/422/422）",
          s08["final"]["got"] == s08["final"]["expect"],
          "%s" % s08["final"]["got"])

    # 迁移与接口文档是交付物，必须真的存在且含关键约束
    ddl = _read("migrations/0001_init.sql")
    check("V34 迁移含 payment_receipts 表与「核销合计不得超流水」的数据库兜底触发器",
          "CREATE TABLE payment_receipts" in ddl
          and "trg_receipt_no_overflow" in ddl
          and "ERRCODE = 'check_violation'" in ddl,
          "表 %s / 触发器 %s"
          % ("payment_receipts" in ddl, "trg_receipt_no_overflow" in ddl))

    check("V35 迁移三张表均含 UUID 主键、金额 BIGINT（分）、软删除与审计字段（对齐技术约定）",
          all(x in ddl for x in ("payment_flows", "installments", "payment_receipts"))
          and len(re.findall(r"^\s*id\s+UUID PRIMARY KEY", ddl, re.M)) == 3
          and ddl.count("BIGINT") >= 4
          and ddl.count("deleted_at      TIMESTAMPTZ") == 2,
          "UUID 主键 %d 处 / BIGINT %d 处 / deleted_at %d 处"
          % (len(re.findall(r"^\s*id\s+UUID PRIMARY KEY", ddl, re.M)), ddl.count("BIGINT"),
             ddl.count("deleted_at      TIMESTAMPTZ")))

    api = _read("docs/api_spec.md")
    check("V36 接口规格写明 422 的六种触发条件与对应状态码表",
          "422" in api and "合计超过流水金额" in api
          and all(x in api for x in ("404", "400", "201", "200")),
          "含状态码表 %s" % ("| 422 |" in api))

    md = _read("docs/flows.md")
    heads = len(re.findall(r"^## P\d\d ", md, re.M))
    check("V37 逐例 Markdown 含 20 个小节，且每条都写出命中规则与逐级判定",
          heads == EXPECT_N_FLOWS and md.count("逐级判定") == EXPECT_N_FLOWS,
          "小节 %d / 逐级判定 %d 处" % (heads, md.count("逐级判定")))


def s02b_code(SC_):
    for r in SC_.run_all():
        if r["id"] == "S02b":
            return r["final"]["code"]
    return None
