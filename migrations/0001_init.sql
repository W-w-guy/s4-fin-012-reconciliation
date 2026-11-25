-- 0001_init.sql —— 回款与开票对接：分期、流水、核销三张表
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
