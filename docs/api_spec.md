# 回款核对接口规格（HTTP 层）

本包内置一个**零依赖**参考实现（`pkg/server.py`，标准库 `http.server`），
可直接起服务验证下面每一条状态码；正式接入时按本文契约换成 FastAPI 路由即可，语义不变。

```
python -m pkg.server --port 8000
```

## 状态码约定

| 码 | 含义 | 触发条件 |
| --- | --- | --- |
| 200 | 查询成功 / 幂等重放 | GET 命中；POST 带相同 idempotency_key 重放 |
| 201 | 核销成功 | 分摊写入 payment_receipts，分期状态已同步 |
| 400 | 请求体非法 | body 不是合法 JSON |
| 404 | 资源不存在 | 流水或分期 id 不存在 |
| 422 | 业务校验失败 | 合计超过流水金额 / 单期超过未收金额 / 金额非正整数 / 同一请求重复分摊 / 空分摊 / operator 为空 |

## 接口

### GET /api/v1/health

返回 `{"ok": true}`。

### GET /api/v1/installments/{iid}

返回分期总额、已收、未收与状态：

```json
{"installment_id":"I07","total_cents":60000,"received_cents":0,"unreceived_cents":60000,"status":"pending"}
```

### GET /api/v1/flows/{fid}

返回流水金额、已核销合计与**未分配余额**：

```json
{"flow_id":"P09","amount_cents":120000,"allocated_cents":60000,"unallocated_cents":60000,"receipts":[...]}
```

### POST /api/v1/flows/{fid}/receipts

请求：

```json
{"allocations":[{"installment_id":"I07","amount_cents":60000},
                {"installment_id":"I08","amount_cents":30000},
                {"installment_id":"I09","amount_cents":30000}],
 "operator":"李核销","mode":"manual","idempotency_key":"dedupe-P09-1"}
```

成功 201：

```json
{"flow_id":"P09","receipts":[{"id":"R0001","flow_id":"P09","installment_id":"I07","amount_cents":60000,"operator":"李核销","mode":"manual","installment_status":"received"}],"allocated_cents":120000,"unallocated_cents":0}
```

合计超限 422：

```json
{"detail":"核销合计 205000 分超过流水金额 120000 分"}
```

实现要点：**先全量校验再写入**。任一分摊不合法则一笔都不落账
（S02 场景实测 receipt_count=0），避免部分写入造成的分期待收金额漂移。

### POST /api/v1/match/run

对全部 unmatched 流水跑匹配引擎：

```json
{"auto_confirm_strong": false}
```

返回每条流水的命中规则与目标分期；`auto_confirm_strong=true` 时 R1 命中会
自动核销（mode=auto），R2/R3/R4 无论开关如何都只出建议。

## 与客户技术约定的对接点

| 约定 | 本单如何满足 |
| --- | --- |
| 后端 Python 3.11+ / FastAPI | 业务逻辑在 `pkg/engine.py`、`pkg/receipt.py` 内为纯函数，
参考实现用标准库 `http.server`；换成 FastAPI 只需把 `ReceiptService.apply` 挂到路由上 |
| SQLAlchemy 2 / Alembic | 迁移 SQL 见 `migrations/0001_init.sql`、`0002_match_audit.sql`，表结构含 UUID 主键、软删除、created_by/updated_at |
| PostgreSQL 16 | DDL 使用 PG 语法（`JSONB`、`plpgsql` 触发器、部分索引） |
| 金额以分存整数 | 全部金额字段为 `BIGINT`，单位分；接口不做浮点换算 |
| 配置与密钥只从环境变量 | 参考实现无配置项；正式版数据库连接串读 `DATABASE_URL` |
| 不引入境外数据 SDK | 全程标准库，无外部网络调用 |
| 依赖其他工单时用桩 | 本单【依赖】为「无」，未引入外部工单接口 |
