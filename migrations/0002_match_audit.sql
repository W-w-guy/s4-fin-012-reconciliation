-- 0002_match_audit.sql —— 匹配审计：每条建议记录命中规则、分数与候选
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
