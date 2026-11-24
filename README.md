# S2-CRM-008 · 回款计划表模型与迁移

会员空间平台 · Stage ii 数据与权限底座 · 模块：CRM 核心对象

新建回款计划表 `crm_payment_plan` 的 SQLAlchemy 2 模型与 Alembic 迁移。**一行 = 一份合同的一个回款期次**，承载期次、应收/实收金额与开票状态。

技术栈：Python 3.11+ · SQLAlchemy 2 · Alembic · PostgreSQL 16 · pytest

## 目录

```
crm/
  base.py                    # 声明基类 + 统一公共字段
  metadata.py                # 约束命名约定（迁移名稳定、可重复）
  stubs.py                   # 合同/企业接口桩（依赖工单未交付，见「依赖与对接点」）
  models/payment_plan.py     # ★ 本单交付：crm_payment_plan 模型
alembic/
  env.py
  versions/a1c27801c008_create_crm_payment_plan.py   # ★ 本单交付：迁移
tests/
  conftest.py                # 空库 → alembic upgrade head
  test_migration.py          # 往返迁移、alembic check、结构断言
  test_constraints.py        # 唯一 / CHECK / 外键
  test_model.py              # ORM 枚举映射与状态流转
```

## 运行与测试

需要一个 PostgreSQL 16。测试会**清空目标库的 `public` schema**，因此库名必须含 `test`，否则拒绝运行。

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 方式一：Docker 起一个临时库（与本地默认连接串一致）
docker run --rm -d --name pg-crm008 -p 127.0.0.1:5581:5432 \
  -e POSTGRES_PASSWORD=postgres postgres:16

# 方式二：用已有的库，只需改连接串
export TEST_DATABASE_URL=postgresql+psycopg://user:pass@127.0.0.1:5432/my_db_test

pytest -v
```

测试结果（PostgreSQL 16 / Python 3.12 / SQLAlchemy 2.1.9 / Alembic 1.20.1）：**43 passed**。

### 手动执行迁移

```bash
export DATABASE_URL=postgresql+psycopg://user:pass@127.0.0.1:5432/your_db
alembic upgrade head
alembic check        # 期望：No new upgrade operations detected.
alembic downgrade base
```

连接串只从环境变量读取，不写入任何文件。

## 字段说明

### 统一公共字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID PK | `gen_random_uuid()` 服务端默认 |
| `org_id` | UUID NOT NULL | 租户 |
| `site_id` | UUID NOT NULL | 站点 |
| `created_at` | timestamptz NOT NULL | `now()` |
| `created_by` | UUID NULL | 操作人（用户表由其他工单提供，暂不建外键） |
| `updated_at` | timestamptz NOT NULL | `now()`，ORM 更新时自动刷新 |
| `deleted_at` | timestamptz NULL | 软删除；为 NULL 表示有效 |

### 业务字段

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `contract_id` | UUID | NOT NULL，FK → `crm_contract.id` | 所属合同 |
| `company_id` | UUID | NOT NULL，FK → `crm_company.id` | 从合同冗余，便于按企业筛选 |
| `installment_no` | smallint | NOT NULL，`>= 1` | 期次 |
| `due_date` | date | NOT NULL | 应收日期 |
| `amount_due_fen` | bigint | NOT NULL，`> 0` | 应收金额（分） |
| `amount_received_fen` | bigint | NOT NULL，默认 `0`，`0 ≤ 值 ≤ amount_due_fen` | 实收金额（分） |
| `last_received_at` | timestamptz | NULL | 最近到账日期 |
| `status` | enum | NOT NULL，默认 `pending` | 回款状态 |
| `invoice_status` | enum | NOT NULL，默认 `not_invoiced` | 开票状态 |
| `invoice_no` | varchar(32) | NULL；`invoice_status='invoiced'` 时必填 | 发票号码 |
| `invoice_date` | date | NULL | 开票日期 |
| `remark` | varchar(200) | NULL | 备注 |
| `merged_into_id` | UUID | NULL，自引用 FK → 本表 `id` | 期次合并时指向合并入的那条 |

金额一律以「分」存整数。

### 约束与索引

| 名称 | 类型 | 定义 |
| --- | --- | --- |
| `uq_crm_payment_plan_contract_installment_active` | 部分唯一索引 | `(contract_id, installment_no) WHERE deleted_at IS NULL` |
| `ck_crm_payment_plan_installment_no_gte_1` | CHECK | `installment_no >= 1` |
| `ck_crm_payment_plan_amount_due_fen_positive` | CHECK | `amount_due_fen > 0` |
| `ck_crm_payment_plan_amount_received_fen_range` | CHECK | `amount_received_fen BETWEEN 0 AND amount_due_fen`（同时拒绝负数与超收） |
| `ck_crm_payment_plan_invoiced_requires_invoice_no` | CHECK | 已开票时 `invoice_no` 必须非 NULL 且非空白串 |
| `ix_crm_payment_plan_org_status_due` | 索引 | `(org_id, status, due_date)` |
| `ix_crm_payment_plan_org_company_due` | 索引 | `(org_id, company_id, due_date)` |
| `ix_crm_payment_plan_org_invoice_status` | 索引 | `(org_id, invoice_status)` |

部分唯一索引的含义：同一合同下，**未删除**的记录期次号不重复；软删除或合并后的旧期次不占用期次号。

## 状态说明

### 回款状态 `status`（`crm_payment_plan_status_enum`）

| 值 | 含义 | 典型触发 |
| --- | --- | --- |
| `pending` | 未到期 | 默认值；`due_date` 尚未到 |
| `due` | 到期待收 | 到达 `due_date` 且未收款 |
| `partial` | 部分回款 | `0 < amount_received_fen < amount_due_fen` |
| `paid` | 已结清 | `amount_received_fen = amount_due_fen` |
| `overdue` | 逾期 | 超过 `due_date` 仍未结清 |
| `written_off` | 已核销 | 确认无法收回，核销处理 |

### 开票状态 `invoice_status`（`crm_payment_plan_invoice_status_enum`）

| 值 | 含义 | 对 `invoice_no` 的要求 |
| --- | --- | --- |
| `not_invoiced` | 未开票 | 默认值，无要求 |
| `applied` | 已申请 | 无要求 |
| `invoiced` | 已开票 | **必须有 `invoice_no`**（CHECK 强制） |
| `red_reversed` | 已红冲 | 无要求（保留原发票号用于追溯） |

状态由业务层流转，本单只提供存储与约束，不做状态机校验。

## 验收标准对照

| # | 验收标准 | 对应测试 |
| --- | --- | --- |
| 1 | upgrade / downgrade 往返成功，`alembic check` 零漂移 | `test_migration.py::test_upgrade_head_twice_and_alembic_check_zero_drift`、`test_model_metadata_matches_database` |
| 2 | 同合同两条未删除记录同期次被拒绝 | `test_constraints.py::test_duplicate_installment_no_same_contract_rejected` |
| 3 | 实收大于应收或为负被 CHECK 拒绝 | `test_constraints.py::test_amount_received_out_of_range_rejected`（-1 / -100 / 超收 / 大幅超收） |
| 4 | 已开票且发票号为空被拒绝 | `test_constraints.py::test_invoiced_without_invoice_no_rejected`、`..._with_empty_invoice_no_rejected` |

## 依赖与对接点

本单无前置依赖。回款计划通过外键引用合同与企业，而这两张表的模型由其他工单交付，为不阻塞本单：

- `crm/stubs.py` 提供 `crm_company`、`crm_contract` 两张**最小接口桩**（只含被引用的列），迁移在目标表不存在时才创建。
- 合同/企业工单落地后，设置 **`CRM_WITH_STUBS=0`** 运行 Alembic：迁移不再创建桩表，回款计划的外键直接指向真实表；随后删除 `stubs.py` 即可。
- `downgrade` 只删除带桩注释的表，不会误删真实合同/企业表。
- 对接点：`crm_contract.id`、`crm_contract.company_id`、`crm_company.id`。`org_id` / `site_id` / `created_by` 暂不建外键，待组织、站点、用户模型落地后在对应工单补。

## 数据与保密

测试全部使用运行时随机生成的 UUID 与虚构金额，不含任何真实个人信息或企业信息；未引入任何第三方 SDK。
