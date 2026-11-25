# -*- coding: utf-8 -*-
"""逐例 Markdown 渲染（本单唯一需要人工措辞的文件）。"""
import engine as E


def md_head():
    return ["# 流水匹配期望结果集（20 条，全部虚构）", "",
            "每条流水都在**同一份初始快照**上独立评估（12 个未收分期、received_cents 全为 0），",
            "因此每条都可单独手算复核，不依赖前一条的执行结果。", "",
            "规则优先级 R1 > R2 > R3 > R4 > unmatched；分数 R1=1.00 / R2=0.85 / R3=0.70 / R4=0.55 / unmatched=0.00。",
            "", "> 企业名、合同号、分期号与金额均为虚构；不含真实个人信息与企业信息，也不含客户名称。", ""]


def _fmt_cands(r):
    if not r["candidates"]:
        return "（无）"
    if r["rule"] == "R3":
        return "组合 " + "+".join(r["candidates"])
    return "、".join(r["candidates"])


def md_section(c):
    r = E.evaluate(c)
    L = ["## %s %s" % (c["id"], c["note"]), "",
         "| 项 | 值 |", "| --- | --- |",
         "| 对方户名（原样） | `%s` |" % c["payer"],
         "| 户名归一化后 | `%s` |" % E.norm(c["payer"]),
         "| 附言 | %s |" % (("`%s`" % c["memo"]) if c["memo"] else "（空）"),
         "| 金额（分） | %d |" % c["amount_cents"],
         "| 命中规则 | **%s** |" % r["rule"],
         "| 分数 | %.2f |" % r["score"],
         "| 目标分期 | %s |" % (r["target_installment"] or "（无）"),
         "| 候选 | %s |" % _fmt_cands(r), ""]
    L += ["逐级判定：", "", "| 规则 | 结论 |", "| --- | --- |"]
    for k in ("R1", "R2", "R3", "R4"):
        L.append("| %s | %s |" % (k, r["reasons"].get(k, "（上一级已命中，未继续判定）")))
    L += [""]
    if r["rule"] == "R3" and len(r["combos"]) > 1:
        L += ["多组命中时全部列出（取第一组为建议）：", ""]
        for i, combo in enumerate(r["combos"], 1):
            L.append("- 组合 %d：%s%s" % (i, "+".join(combo), "（采纳）" if i == 1 else ""))
        L += [""]
    return L
