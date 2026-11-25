# -*- coding: utf-8 -*-
"""到账核对：流水匹配引擎 + 核销服务（全部虚构数据，仅用于本单交付）。

规则优先级（工单原文【本单范围】）：
  R1 强匹配：附言含合同编号或分期编号，且金额等于该分期未收金额
             → 配置 auto_confirm_strong=true 时自动核销，否则生成建议
  R2 户名匹配：对方户名归一化（去空格、去括号）后等于开票抬头或 CRM 企业名，
             且金额等于该企业某一未收分期 → 建议
  R3 组合匹配：金额等于同一企业若干连续未收分期之和 → 建议并列出组合
  R4 部分付款：金额小于某分期未收金额 → 部分付款建议
  都不命中 → unmatched

口径细化（原文未逐条写死，这里写清以便复核）：
  - 归一化 = 去掉所有半角/全角空格、去掉括号字符本身（）()【】[]，不改变其余字符；
  - R1 含分期编号时以分期为准；仅含合同编号时在该合同未收分期里找金额相等者，
    唯一命中才算 R1，出现多个同额分期则 R1 不成立（记 multi_candidate 原因，交给 R2 继续判）；
  - R2 出现多个同额候选时取分期号最小者，并在 candidates 里列出全部候选；
  - R3 只考虑长度 >= 2 的连续期次区间（长度 1 已由 R2 覆盖），多组命中取期次最靠前的一组，
    并在 combos 里列出全部满足的组合；
  - R4 在金额小于未收金额的分期里取「未收金额最小」的那一期（并列取分期号最小），
    因为部分付款最可能对应金额最接近的那一期。
"""
import re
from copy import deepcopy

# ---------------------------------------------------------------- 归一化
_STRIP = str.maketrans({c: None for c in " \t\u3000（）()【】[]"})
_RE_INST = re.compile(r"I\d{2}")


def norm(name):
    """去空格（含全角）与括号字符本身。None 安全。"""
    return (name or "").translate(_STRIP)


def norm_compact(name):
    """便于人工核对的展示形式（与 norm 等价，只是给出可读输出）。"""
    return norm(name)


# ---------------------------------------------------------------- 初始数据（全部虚构）
# 分期：总额 total_cents、已收 received_cents，状态由二者推导
INITIAL_INSTALLMENTS = [
    # 企业 E1 杭州云栖智造科技有限公司（CRM：杭州云栖智造），合同 HT-2026-0101
    {"id": "I01", "contract_no": "HT-2026-0101", "org": "杭州云栖智造科技有限公司",
     "crm": "杭州云栖智造", "total_cents": 120000, "received_cents": 0},
    {"id": "I02", "contract_no": "HT-2026-0101", "org": "杭州云栖智造科技有限公司",
     "crm": "杭州云栖智造", "total_cents": 90000, "received_cents": 0},
    {"id": "I03", "contract_no": "HT-2026-0101", "org": "杭州云栖智造科技有限公司",
     "crm": "杭州云栖智造", "total_cents": 90000, "received_cents": 0},
    # 企业 E2 苏州明远数字医疗有限公司（CRM：苏州明远医疗），合同 HT-2026-0102
    {"id": "I04", "contract_no": "HT-2026-0102", "org": "苏州明远数字医疗有限公司",
     "crm": "苏州明远医疗", "total_cents": 85000, "received_cents": 0},
    {"id": "I05", "contract_no": "HT-2026-0102", "org": "苏州明远数字医疗有限公司",
     "crm": "苏州明远医疗", "total_cents": 45000, "received_cents": 0},
    {"id": "I06", "contract_no": "HT-2026-0102", "org": "苏州明远数字医疗有限公司",
     "crm": "苏州明远医疗", "total_cents": 45000, "received_cents": 0},
    # 企业 E3 宁波港盛供应链有限公司（CRM：宁波港盛供应链），合同 HT-2026-0103
    {"id": "I07", "contract_no": "HT-2026-0103", "org": "宁波港盛供应链有限公司",
     "crm": "宁波港盛供应链", "total_cents": 60000, "received_cents": 0},
    {"id": "I08", "contract_no": "HT-2026-0103", "org": "宁波港盛供应链有限公司",
     "crm": "宁波港盛供应链", "total_cents": 30000, "received_cents": 0},
    {"id": "I09", "contract_no": "HT-2026-0103", "org": "宁波港盛供应链有限公司",
     "crm": "宁波港盛供应链", "total_cents": 30000, "received_cents": 0},
    # 企业 E4 上海启元低碳材料有限公司（CRM：上海启元低碳），合同 HT-2026-0104
    {"id": "I10", "contract_no": "HT-2026-0104", "org": "上海启元低碳材料有限公司",
     "crm": "上海启元低碳", "total_cents": 150000, "received_cents": 0},
    {"id": "I11", "contract_no": "HT-2026-0104", "org": "上海启元低碳材料有限公司",
     "crm": "上海启元低碳", "total_cents": 70000, "received_cents": 0},
    {"id": "I12", "contract_no": "HT-2026-0104", "org": "上海启元低碳材料有限公司",
     "crm": "上海启元低碳", "total_cents": 70000, "received_cents": 0},
    # I13 与 I11、I12 等额，用来构造「同一金额对应两组不同连续区间」的 R3 多组场景
    {"id": "I13", "contract_no": "HT-2026-0104", "org": "上海启元低碳材料有限公司",
     "crm": "上海启元低碳", "total_cents": 70000, "received_cents": 0},
]

# CRM 里存在但尚无分期记录的企业（用于「户名认得、无分期」的边界）
KNOWN_ORGS_NO_INSTALLMENT = [
    {"org": "严州文创发展有限公司", "crm": "严州文创"},
]

AUTO_CONFIRM_STRONG = False  # 默认只出建议；场景 S04 会把它打开验自动核销

SCORE = {"R1": 1.00, "R2": 0.85, "R3": 0.70, "R4": 0.55, "unmatched": 0.00}


# ---------------------------------------------------------------- 状态推导
def status_of(inst):
    r, t = inst["received_cents"], inst["total_cents"]
    if r <= 0:
        return "pending"
    return "received" if r >= t else "partially_received"


def unreceived(inst):
    return inst["total_cents"] - inst["received_cents"]


def org_of(inst):
    return inst["org"]


def _by_id(insts):
    return {i["id"]: i for i in insts}


def _org_installments(payer, insts):
    """户名归一化后与 org 或 crm 相等的企业，返回该企业未收完的分期（按期次排序）。"""
    p = norm(payer)
    out = [i for i in insts if unreceived(i) > 0 and (norm(i["org"]) == p or norm(i["crm"]) == p)]
    return sorted(out, key=lambda i: i["id"])


# ---------------------------------------------------------------- 规则实现
def _r1(flow, insts, auto_confirm):
    """R1 强匹配。返回 (hit, target_id, candidates, reason)。"""
    memo = flow.get("memo") or ""
    ids = _RE_INST.findall(memo)
    amt = flow["amount_cents"]
    if ids:
        for iid in ids:
            inst = _by_id(insts).get(iid)
            if inst and unreceived(inst) == amt:
                return True, iid, [iid], "附言含分期编号 %s 且金额等于该分期未收金额" % iid
        return False, None, [], "附言含分期编号但金额不等于该分期未收金额"
    contracts = [i["contract_no"] for i in insts if i["contract_no"] and i["contract_no"] in memo]
    if contracts:
        cno = contracts[0]
        cands = sorted([i["id"] for i in insts
                        if i["contract_no"] == cno and unreceived(i) == amt])
        if len(cands) == 1:
            return True, cands[0], cands, "附言含合同编号 %s 且金额唯一对应未收分期" % cno
        if len(cands) > 1:
            return False, None, cands, "合同 %s 下存在 %d 个同额未收分期，无法唯一确定" % (cno, len(cands))
        return False, None, [], "附言含合同编号 %s 但无同额未收分期" % cno
    return False, None, [], "附言不含合同编号或分期编号"


def _r2(flow, insts):
    amt = flow["amount_cents"]
    mine = _org_installments(flow["payer"], insts)
    if not mine:
        return False, None, [], "户名未匹配到任何企业（或其无未收分期）"
    cands = [i for i in mine if unreceived(i) == amt]
    if cands:
        ids = [c["id"] for c in cands]
        return True, ids[0], ids, "户名归一化匹配且金额等于未收分期"
    return False, None, [], "户名已匹配但金额不等于任何单一未收分期"


def _consecutive_combos(group, amt):
    """同一合同下按期次排序，找所有长度 >=2 的连续未收分期之和等于 amt 的区间。"""
    out = []
    n = len(group)
    for i in range(n):
        s = 0
        for j in range(i, n):
            s += unreceived(group[j])
            if j > i and s == amt:
                out.append([g["id"] for g in group[i:j + 1]])
    return out


def _r3(flow, insts):
    amt = flow["amount_cents"]
    group = _org_installments(flow["payer"], insts)
    by_contract = {}
    for g in group:
        by_contract.setdefault(g["contract_no"], []).append(g)
    combos = []
    for cno in sorted(by_contract):
        combos.extend(_consecutive_combos(by_contract[cno], amt))
    if combos:
        return True, combos[0][0], combos[0], "金额等于连续 %d 期未收之和" % len(combos[0]), combos
    if not group:
        return False, None, [], "户名未匹配到任何企业（无法组合同一企业的连续分期）", []
    return False, None, [], "户名已匹配但金额不等于任何连续 2 期及以上的未收组合", []


def _r4(flow, insts):
    amt = flow["amount_cents"]
    mine = _org_installments(flow["payer"], insts)
    if not mine and _org_known_without_installment(flow["payer"]):
        return False, None, [], "户名在 CRM 中存在，但该企业没有任何未收分期"
    if not mine:
        return False, None, [], "户名未匹配到任何企业"
    group = [i for i in mine if amt < unreceived(i)]
    if group:
        best = min(group, key=lambda i: (unreceived(i), i["id"]))
        return True, best["id"], [best["id"]], "金额小于未收金额，按部分付款建议"
    return False, None, [], "金额不小于该企业任何未收分期的未收金额"


def _org_known_without_installment(payer):
    p = norm(payer)
    return any(norm(o["org"]) == p or norm(o["crm"]) == p for o in KNOWN_ORGS_NO_INSTALLMENT)


def match_flow(flow, insts, auto_confirm=AUTO_CONFIRM_STRONG):
    """对单条流水跑匹配，返回建议字典（含命中规则、分数、目标分期、理由）。"""
    reasons = {}
    hit, target, cands, why = _r1(flow, insts, auto_confirm)
    reasons["R1"] = why
    combos = []
    if not hit:
        hit, target, cands, why = _r2(flow, insts)
        reasons["R2"] = why
    if not hit:
        r3 = _r3(flow, insts)
        hit, target, cands, why, combos = r3
        reasons["R3"] = why
    if not hit:
        hit, target, cands, why = _r4(flow, insts)
        reasons["R4"] = why
    if hit:
        rule = [k for k in ("R1", "R2", "R3", "R4") if reasons.get(k) == why][0]
    else:
        rule = "unmatched"
        reasons["unmatched"] = "R1/R2/R3/R4 均未命中"
    auto = bool(rule == "R1" and auto_confirm)
    return {"flow_id": flow["id"], "rule": rule, "score": SCORE[rule],
            "target_installment": target or "", "candidates": cands,
            "combo_len": len(cands) if rule == "R3" else 0,
            "combos": combos, "auto_confirmed": auto, "why": why,
            "reasons": {k: reasons[k] for k in ("R1", "R2", "R3", "R4") if k in reasons}}


# ---------------------------------------------------------------- 20 条构造流水
FLOWS = [
    dict(id="P01", payer="杭州云栖智造科技有限公司", amount_cents=120000,
         memo="合同 HT-2026-0101 首期款 I01", note="R1：附言含分期编号且金额等于该期未收"),
    dict(id="P02", payer="苏州明远数字医疗有限公司", amount_cents=85000,
         memo="HT-2026-0102-I04", note="R1：附言含分期编号，金额相等"),
    dict(id="P03", payer="宁波港盛供应链有限公司", amount_cents=60000,
         memo="HT-2026-0103 I07 首期", note="R1：附言含分期编号与合同编号，金额相等"),
    dict(id="P04", payer="上海启元低碳材料有限公司", amount_cents=150000,
         memo="合同 HT-2026-0104 首期", note="R1：只含合同编号，该合同下金额唯一对应 I10"),
    dict(id="P05", payer="杭州云栖智造科技有限公司", amount_cents=90000,
         memo="HT-2026-0101 I02", note="R1：附言含分期编号，金额相等"),
    dict(id="P06", payer=" 宁波港盛供应链有限公司 ", amount_cents=60000,
         memo="HT-2026-0103 I08", note="附言指向 I08 但金额是 I07 的未收额 → R1 不成立，降级 R2 命中 I07；户名前后带空格，验归一化"),
    dict(id="P07", payer="苏州明远数字医疗有限公司", amount_cents=45000,
         memo="", note="R2：I05 与 I06 同额，取分期号最小者并列出候选"),
    dict(id="P08", payer="杭州云栖智造科技有限公司", amount_cents=210000,
         memo="合并支付第 1-2 期", note="R3：I01+I02 连续两期之和"),
    dict(id="P09", payer="宁波港盛供应链有限公司", amount_cents=120000,
         memo="合并支付 3 期", note="R3：I07+I08+I09 连续三期之和（AC2 场景源流水）"),
    dict(id="P10", payer="苏州明远数字医疗有限公司", amount_cents=90000,
         memo="合并两期", note="R3：I05+I06 连续两期之和"),
    dict(id="P11", payer="上海启元低碳材料有限公司", amount_cents=50000,
         memo="", note="R4：金额小于 I11 未收额，按部分付款建议（AC4 场景源流水）"),
    dict(id="P12", payer="杭州云栖智造科技有限公司", amount_cents=30000,
         memo="", note="R4：未收额最小的并列期次取分期号最小者 I02"),
    dict(id="P13", payer="严州文创发展有限公司", amount_cents=50000,
         memo="", note="unmatched：户名在 CRM 里存在但无任何分期记录"),
    dict(id="P14", payer="深圳市启明贸易有限公司", amount_cents=120000,
         memo="", note="unmatched：户名不匹配任何企业"),
    dict(id="P15", payer="上海启元低碳材料有限公司", amount_cents=140000,
         memo="", note="R3：I11+I12 连续两期之和"),
    dict(id="P16", payer="杭州云栖智造（科技）有限公司", amount_cents=120000,
         memo="", note="R2：户名带全角括号，去括号后与开票抬头相等"),
    dict(id="P17", payer="苏州明远数字医疗有限公司", amount_cents=175000,
         memo="", note="R3：I04+I05+I06 连续三期之和"),
    dict(id="P18", payer="宁波港盛供应链 有限公司", amount_cents=30000,
         memo="", note="R2：户名中间含空格；I08 与 I09 同额取 I08"),
    dict(id="P19", payer="上海启元低碳材料有限公司", amount_cents=200000,
         memo="合同 HT-2026-0104 部分款", note="unmatched：大于任何单一未收，且不等于任何连续组合"),
    dict(id="P20", payer="杭州云栖智造科技有限公司", amount_cents=300000,
         memo="合并全清", note="R3：I01+I02+I03 三期满额之和"),
]

CATS = [
    ("C01", "R1 强匹配（强键 + 金额相等）"),
    ("C02", "R2 户名归一化匹配（去空格 / 去括号）"),
    ("C03", "R3 连续分期组合匹配（2 期与 3 期）"),
    ("C04", "R4 部分付款"),
    ("C05", "无匹配（户名不认 / 有户名无分期 / 金额不匹配任何组合）"),
    ("C06", "边界降级（R1 金额不符时降级到 R2）"),
    ("C07", "多候选并列（同额分期取分期号最小并列出候选）"),
]

COLS = ["flow_id", "payer_raw", "payer_norm", "memo", "amount_cents",
        "expected_rule", "expected_target", "expected_score",
        "expected_candidates", "expected_combo_len", "expected_auto"]


def case_id(c):
    return c["id"]


def cats_of(c):
    return c["cats"]


def evaluate(c):
    """在初始快照上跑匹配（每条流水独立评估，互不影响）。"""
    return match_flow(c, deepcopy(INITIAL_INSTALLMENTS))


def row_of(c):
    r = evaluate(c)
    return {
        "flow_id": c["id"],
        "payer_raw": c["payer"],
        "payer_norm": norm(c["payer"]),
        "memo": c["memo"],
        "amount_cents": c["amount_cents"],
        "expected_rule": r["rule"],
        "expected_target": r["target_installment"] or "(无)",
        "expected_score": "%.2f" % r["score"],
        "expected_candidates": ",".join(r["candidates"]) or "(无)",
        "expected_combo_len": r["combo_len"],
        "expected_auto": "true" if r["auto_confirmed"] else "false",
    }


# ---------------------------------------------------------------- 情形归类
# 覆盖类与流水是多对一关系；C06/C07 是「同一流水既是某规则又构成边界」的叠加标注。
FLOW_CATS = {
    "P01": ["C01"], "P02": ["C01"], "P03": ["C01"], "P04": ["C01"], "P05": ["C01"],
    "P06": ["C02", "C06"],          # 户名带空格 + R1 金额不符降级到 R2
    "P07": ["C02", "C07"],          # 户名匹配 + I05/I06 同额并列
    "P16": ["C02"], "P18": ["C02", "C07"],
    "P08": ["C03"], "P09": ["C03"], "P10": ["C03"],
    "P15": ["C03"], "P17": ["C03"], "P20": ["C03"],
    "P11": ["C04"], "P12": ["C04"],
    "P13": ["C05"], "P14": ["C05"], "P19": ["C05"],
}
for _f in FLOWS:
    _f["cats"] = FLOW_CATS[_f["id"]]



# ---------------------------------------------------------------- 跨模块约束（抽到 engine 便于变异测试）
# 核销合计上限判定放在这里而不是 receipt.py：变异测试只改 pkg/engine.py，
# 约束写在 receipt.py 里会导致「改不动 = 变异本身失效」。
def alloc_overflow(total_cents, flow_cents):
    """核销合计是否超过流水金额。超过即调用方返回 422。"""
    return total_cents > flow_cents

# rule_kit 契约：CASES 是示例集的入口名（本单的「示例」就是 20 条构造流水）
CASES = FLOWS
