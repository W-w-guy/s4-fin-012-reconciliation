# 会员空间平台 · 到账自动核对：流水与分期的匹配规则与核销

本包为「到账自动核对」交付物：按 R1 强匹配、R2 户名归一化、R3 连续分期组合、R4 部分付款四级优先级的匹配引擎；核销服务（写 payment_receipts、同步 received_cents 与状态、支持一笔流水核销多个分期、合计超限返回 422、剩余记未分配余额、幂等键去重）；Alembic 风格迁移 SQL（PG 16，含超限与状态同步两个兜底触发器）；20 条构造流水的期望结果表；零依赖 HTTP 参考实现；测试与双平台实跑日志。

## 一条命令复跑

```
python tools/gen.py             # 重新生成 docs/ 下全部交付物
python tools/verify.py          # 逐条断言 AC（38 条），任何一条失败即非零退出
python -m pytest tests -q       # 用例逐条比对 CSV 期望值
python tools/mutate.py          # 变异测试：改坏引擎 7 处，每处都必须让 verify 变红
python tools/build_docs.py      # 由实跑结果重生成 README / DATA_PROVENANCE / ACCEPTANCE
python tools/make_zip.py        # 打包 + SHA256SUMS
```

## 目录

| 路径 | 内容 |
| --- | --- |
| `pkg/engine.py` | 业务规则引擎 + 示例定义 + 情形编号 |
| `pkg/render.py` | 逐例 Markdown 渲染 |
| `pkg/engine.py` | 匹配引擎：归一化、R1~R4 四级规则、优先级、分数与逐级未命中原因 |
| `pkg/receipt.py` | 核销服务：校验、写 payment_receipts、同步 received_cents 与状态、未分配余额、幂等 |
| `pkg/server.py` | 零依赖 HTTP 参考实现（标准库 http.server），暴露 422/404/201 语义 |
| `pkg/scenarios.py` | 10 个核销场景（合并付款三期、超限、部分付款、幂等、顺序累积等） |
| `migrations/0001_init.sql` | 分期 / 流水 / 核销三张表，含超限与状态同步两个 PG 触发器 |
| `migrations/0002_match_audit.sql` | 匹配审计表：记录命中规则、分数、候选、组合与逐级原因 |
| `docs/api_spec.md` | 接口规格：状态码约定、请求响应示例、与技术约定的对接点 |
| `docs/expected_results.csv` | 20 条流水的期望结果表（规则 / 目标分期 / 分数 / 候选 / 组合长度 / 是否自动核销） |
| `docs/combos.csv` | R3 组合明细，多组命中时全部列出并标注被采纳的那组 |
| `docs/flows.md` | 逐例说明：20 条流水的逐级判定过程 |
| `docs/coverage.csv` | 7 类情形覆盖对照表 |
| `tools/verify.py` | AC 断言器（38 条，非零退出可接 CI） |
| `tools/mutate.py` | 变异测试（7 处） |
| `tools/pii_scan.py` | PII 扫描（手机 / 邮箱 / 身份证 / 银行卡） |
| `report/` | 本次双平台实跑日志（打包时快照，不参与校验和） |

## 口径说明

- 规则优先级 R1 > R2 > R3 > R4 > unmatched，命中即停，不继续降级。
- 户名归一化 = 去掉所有半角/全角空格与括号字符本身（）()【】[]，其余字符不改。
- R1 附言含分期编号时以该分期为准；仅含合同编号时在该合同未收分期里找金额相等者，唯一才算 R1，多个同额则 R1 不成立并记录原因。
- R2 出现多个同额候选时取分期号最小者，并在 candidates 里列出全部候选。
- R3 只考虑同一合同下长度 ≥2 的连续期次区间；多组命中取期次最靠前的一组，全部组合记入 combos。
- R4 在金额小于未收金额的分期里取「未收金额最小」者（并列取分期号最小），因为部分付款最可能对应金额最接近的那一期。
- 核销先全量校验再写入：任一分摊不合法则一笔都不落账（避免分期待收金额漂移）。
- 核销合计超过流水金额、单期超过未收金额、金额非正整数、同一请求重复分摊、空分摊、operator 为空，一律 422；资源不存在 404。
- 一笔流水可核销多个分期，剩余部分记为未分配余额，不得凭空消化。
- 同一 idempotency_key 重放返回既有结果，不重复入账。
- auto_confirm_strong 只影响 R1：关闭时只出建议，打开时 R1 自动核销（mode=auto）；R2/R3/R4 无论开关如何都只出建议。
