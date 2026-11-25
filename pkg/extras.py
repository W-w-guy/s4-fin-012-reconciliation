# -*- coding: utf-8 -*-
"""本单特有产物：Alembic 风格迁移 SQL、HTTP 接口规格、R3 组合明细表。

全部由 pkg/engine.py 的常量生成，重跑字节一致（无随机、无时间戳）。
迁移脚本按客户【统一技术约定】写（PostgreSQL 16 + UUID + 软删除 + 金额存分），
但本包的运行与测试不需要数据库：业务逻辑在 pkg/ 内是纯函数，迁移只是交付物之一。
"""
import os

import engine as E

MIG1 = "migrations/0001_init.sql"
MIG2 = "migrations/0002_match_audit.sql"
API = "docs/api_spec.md"
COMBOS = "docs/combos.csv"


def _ddl():
    return """-- 0001_init.sql —— 回款与开票对接：分期、流水、核销三张表
-- 口径：金额一律以「分」存 BIGINT；业务表统一含 id(UUID)/org_id/site_id/审计字段/软删除；
--       核销合计不得超过流水金额由触发器兜底（应用层已先校验并返回 422）。
BEGIN;

CREATE TABLE installments (
    id              UUID PRIMARY KEY,
    org_id          UUID        NOT NULL,
    site_id         UUID,
    contract_no     VARCHAR(32) NOT NULL,
    installment_no  SMALLINT    NOT NULL,
    org_name        VARCHAR(128) NOT NULL,      -- 开票抬头
    crm_name        VARCHAR(128) NOT NULL,      -- CRM 企业名
    total_cents     BIGINT      NOT NULL CHECK (total_cents > 0),
    received_cents  BIGINT      NOT NULL DEFAULT 0 CHECK (received_cents >= 0),
    status          VARCHAR(24) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'partially_received', 'received')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      UUID,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ,
    CONSTRAINT uk_installment UNIQUE (contract_no, installment_no),
    CONSTRAINT ck_received_le_total CHECK (received_cents <= total_cents)
);

CREATE TABLE payment_flows (
    id              UUID PRIMARY KEY,
    org_id          UUID        NOT NULL,
    site_id         UUID,
    payer_raw       VARCHAR(128) NOT NULL,      -- 银行回单上的对方户名原样
    payer_norm      VARCHAR(128) NOT NULL,      -- 归一化（去空格、去括号）后，供 R2/R3 匹配
    amount_cents    BIGINT      NOT NULL CHECK (amount_cents > 0),
    memo            TEXT        NOT NULL DEFAULT '',
    matched_rule    VARCHAR(12) CHECK (matched_rule IN ('R1', 'R2', 'R3', 'R4', 'unmatched')),
    match_score     NUMERIC(4, 2),
    status          VARCHAR(24) NOT NULL DEFAULT 'unmatched'
                    CHECK (status IN ('unmatched', 'suggested', 'partially_allocated', 'allocated')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      UUID,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);
CREATE INDEX ix_flow_norm ON payment_flows (payer_norm) WHERE deleted_at IS NULL;

CREATE TABLE payment_receipts (
    id               UUID PRIMARY KEY,
    org_id           UUID   NOT NULL,
    flow_id          UUID   NOT NULL REFERENCES payment_flows (id),
    installment_id   UUID   NOT NULL REFERENCES installments (id),
    amount_cents     BIGINT NOT NULL CHECK (amount_cents > 0),
    operator_name    VARCHAR(64) NOT NULL,
    mode             VARCHAR(8)  NOT NULL CHECK (mode IN ('auto', 'manual')),
    idempotency_key  VARCHAR(64),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by       UUID,
    CONSTRAINT uk_receipt_idem UNIQUE (flow_id, idempotency_key)
);
CREATE INDEX ix_receipt_flow ON payment_receipts (flow_id);
CREATE INDEX ix_receipt_inst ON payment_receipts (installment_id);

-- 兜底：任何写入路径都不得让核销合计超过流水金额
CREATE OR REPLACE FUNCTION trg_receipt_no_overflow() RETURNS TRIGGER AS $$
DECLARE
    flow_amt BIGINT;
    used_amt BIGINT;
BEGIN
    SELECT amount_cents INTO flow_amt FROM payment_flows WHERE id = NEW.flow_id;
    SELECT COALESCE(SUM(amount_cents), 0) INTO used_amt
      FROM payment_receipts WHERE flow_id = NEW.flow_id AND id <> NEW.id;
    IF used_amt + NEW.amount_cents > flow_amt THEN
        RAISE EXCEPTION 'receipt total % exceeds flow amount %',
              used_amt + NEW.amount_cents, flow_amt
              USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_receipt_overflow
    BEFORE INSERT OR UPDATE ON payment_receipts
    FOR EACH ROW EXECUTE FUNCTION trg_receipt_no_overflow();

-- 兜底：核销后同步分期累计与状态
CREATE OR REPLACE FUNCTION trg_sync_installment() RETURNS TRIGGER AS $$
DECLARE
    total_amt BIGINT;
    got_amt   BIGINT;
BEGIN
    SELECT COALESCE(SUM(amount_cents), 0) INTO got_amt
      FROM payment_receipts WHERE installment_id = NEW.installment_id;
    SELECT total_cents INTO total_amt FROM installments WHERE id = NEW.installment_id;
    UPDATE installments
       SET received_cents = got_amt,
           status = CASE WHEN got_amt = 0 THEN 'pending'
                         WHEN got_amt >= total_amt THEN 'received'
                         ELSE 'partially_received' END,
           updated_at = now()
     WHERE id = NEW.installment_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_receipt_sync
    AFTER INSERT OR UPDATE ON payment_receipts
    FOR EACH ROW EXECUTE FUNCTION trg_sync_installment();

COMMIT;
"""


def _ddl2():
    return """-- 0002_match_audit.sql —— 匹配审计：每条建议记录命中规则、分数与候选
-- 为什么单独一张表：AC1 要求「每条建议记录命中规则与分数」，
-- 把它放在流水行上只够存最终结论，存不下 R3 的组合明细与 R2 的多候选。
BEGIN;

CREATE TABLE match_suggestions (
    id               UUID PRIMARY KEY,
    org_id           UUID NOT NULL,
    flow_id          UUID NOT NULL REFERENCES payment_flows (id),
    rule             VARCHAR(12) NOT NULL
                     CHECK (rule IN ('R1', 'R2', 'R3', 'R4', 'unmatched')),
    score            NUMERIC(4, 2) NOT NULL,
    target_installment_id UUID REFERENCES installments (id),
    candidates       JSONB NOT NULL DEFAULT '[]'::jsonb,   -- R2 多候选 / R3 全部组合
    combo_len        SMALLINT NOT NULL DEFAULT 0,
    auto_confirmed   BOOLEAN NOT NULL DEFAULT false,
    reason           TEXT NOT NULL,
    rule_trace       JSONB NOT NULL DEFAULT '{}'::jsonb,   -- R1~R4 逐级未命中原因，便于排障
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_sugg_flow ON match_suggestions (flow_id);
CREATE UNIQUE INDEX uk_sugg_flow_latest ON match_suggestions (flow_id, created_at);

-- 常用查询：待人工确认的建议（分数低先看）
CREATE VIEW v_pending_suggestions AS
SELECT s.flow_id, f.payer_raw, f.amount_cents, s.rule, s.score,
       s.target_installment_id, s.candidates, s.reason
  FROM match_suggestions s
  JOIN payment_flows f ON f.id = s.flow_id
 WHERE f.status IN ('unmatched', 'suggested')
 ORDER BY s.score ASC, f.created_at ASC;

COMMIT;
"""


def _api():
    L = ["# 回款核对接口规格（HTTP 层）", "",
         "本包内置一个**零依赖**参考实现（`pkg/server.py`，标准库 `http.server`），",
         "可直接起服务验证下面每一条状态码；正式接入时按本文契约换成 FastAPI 路由即可，语义不变。", "",
         "```",
         "python -m pkg.server --port 8000",
         "```", "",
         "## 状态码约定", "",
         "| 码 | 含义 | 触发条件 |", "| --- | --- | --- |",
         "| 200 | 查询成功 / 幂等重放 | GET 命中；POST 带相同 idempotency_key 重放 |",
         "| 201 | 核销成功 | 分摊写入 payment_receipts，分期状态已同步 |",
         "| 400 | 请求体非法 | body 不是合法 JSON |",
         "| 404 | 资源不存在 | 流水或分期 id 不存在 |",
         "| 422 | 业务校验失败 | 合计超过流水金额 / 单期超过未收金额 / 金额非正整数 / 同一请求重复分摊 / 空分摊 / operator 为空 |",
         "", "## 接口", "",
         "### GET /api/v1/health", "", "返回 `{\"ok\": true}`。", "",
         "### GET /api/v1/installments/{iid}", "",
         "返回分期总额、已收、未收与状态：", "",
         "```json",
         '{"installment_id":"I07","total_cents":60000,"received_cents":0,'
         '"unreceived_cents":60000,"status":"pending"}',
         "```", "",
         "### GET /api/v1/flows/{fid}", "",
         "返回流水金额、已核销合计与**未分配余额**：", "",
         "```json",
         '{"flow_id":"P09","amount_cents":120000,"allocated_cents":60000,'
         '"unallocated_cents":60000,"receipts":[...]}',
         "```", "",
         "### POST /api/v1/flows/{fid}/receipts", "",
         "请求：", "", "```json",
         '{"allocations":[{"installment_id":"I07","amount_cents":60000},',
         '                {"installment_id":"I08","amount_cents":30000},',
         '                {"installment_id":"I09","amount_cents":30000}],',
         ' "operator":"李核销","mode":"manual","idempotency_key":"dedupe-P09-1"}',
         "```", "",
         "成功 201：", "", "```json",
         '{"flow_id":"P09","receipts":[{"id":"R0001","flow_id":"P09",'
         '"installment_id":"I07","amount_cents":60000,"operator":"李核销",'
         '"mode":"manual","installment_status":"received"}],'
         '"allocated_cents":120000,"unallocated_cents":0}',
         "```", "",
         "合计超限 422：", "", "```json",
         '{"detail":"核销合计 205000 分超过流水金额 120000 分"}',
         "```", "",
         "实现要点：**先全量校验再写入**。任一分摊不合法则一笔都不落账",
         "（S02 场景实测 receipt_count=0），避免部分写入造成的分期待收金额漂移。", "",
         "### POST /api/v1/match/run", "",
         "对全部 unmatched 流水跑匹配引擎：", "", "```json",
         '{"auto_confirm_strong": false}', "```", "",
         "返回每条流水的命中规则与目标分期；`auto_confirm_strong=true` 时 R1 命中会",
         "自动核销（mode=auto），R2/R3/R4 无论开关如何都只出建议。", "",
         "## 与客户技术约定的对接点", "",
         "| 约定 | 本单如何满足 |", "| --- | --- |",
         "| 后端 Python 3.11+ / FastAPI | 业务逻辑在 `pkg/engine.py`、`pkg/receipt.py` 内为纯函数，",
         "参考实现用标准库 `http.server`；换成 FastAPI 只需把 `ReceiptService.apply` 挂到路由上 |",
         "| SQLAlchemy 2 / Alembic | 迁移 SQL 见 `migrations/0001_init.sql`、`0002_match_audit.sql`，"
         "表结构含 UUID 主键、软删除、created_by/updated_at |",
         "| PostgreSQL 16 | DDL 使用 PG 语法（`JSONB`、`plpgsql` 触发器、部分索引） |",
         "| 金额以分存整数 | 全部金额字段为 `BIGINT`，单位分；接口不做浮点换算 |",
         "| 配置与密钥只从环境变量 | 参考实现无配置项；正式版数据库连接串读 `DATABASE_URL` |",
         "| 不引入境外数据 SDK | 全程标准库，无外部网络调用 |",
         "| 依赖其他工单时用桩 | 本单【依赖】为「无」，未引入外部工单接口 |", ""]
    return "\n".join(L)


def _combos_csv():
    rows = [["flow_id", "amount_cents", "combo_index", "installment_ids",
             "combo_sum_cents", "is_chosen"]]
    for f in E.FLOWS:
        r = E.match_flow(f, E.INITIAL_INSTALLMENTS)
        if r["rule"] != "R3":
            continue
        for idx, combo in enumerate(r["combos"], 1):
            by_id = {i["id"]: i for i in E.INITIAL_INSTALLMENTS}
            s = sum(by_id[c]["total_cents"] - by_id[c]["received_cents"] for c in combo)
            rows.append([f["id"], f["amount_cents"], idx, "+".join(combo), s,
                         "true" if idx == 1 else "false"])
    return rows


def write_extras(docs_dir):
    root = os.path.dirname(os.path.abspath(docs_dir))
    out = []
    for rel, body in ((MIG1, _ddl()), (MIG2, _ddl2()), (API, _api())):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        out.append(p)
    # R3 组合明细（含未选中的组合，证明「多组命中」也被记录）
    sys_path = os.path.dirname(os.path.abspath(__file__))
    import csv
    p = os.path.join(root, COMBOS)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerows(_combos_csv())
    out.append(p)
    return out
