"""约束测试：验收标准 2 / 3 / 4，以及默认值与外键。"""
import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError

ORG = uuid.uuid4()
SITE = uuid.uuid4()


def seed_company(conn) -> uuid.UUID:
    cid = uuid.uuid4()
    conn.execute(
        text("INSERT INTO crm_company (id, org_id) VALUES (:id, :org)"),
        {"id": cid, "org": ORG},
    )
    return cid


def seed_contract(conn, company_id) -> uuid.UUID:
    cid = uuid.uuid4()
    conn.execute(
        text("INSERT INTO crm_contract (id, org_id, company_id) VALUES (:id, :org, :c)"),
        {"id": cid, "org": ORG, "c": company_id},
    )
    conn.commit()
    return cid


def insert_plan(conn, contract_id, company_id, **overrides):
    """插一行回款计划；overrides 覆盖默认值，值为 None 表示显式写 NULL。"""
    params = dict(
        org_id=ORG,
        site_id=SITE,
        contract_id=contract_id,
        company_id=company_id,
        installment_no=1,
        due_date=date(2026, 12, 31),
        amount_due_fen=100_00,
    )
    params.update(overrides)
    cols = ", ".join(params)
    vals = ", ".join(f":{k}" for k in params)
    sql = text(f"INSERT INTO crm_payment_plan ({cols}) VALUES ({vals}) RETURNING id")
    # 用 SAVEPOINT 包住：被拒绝时回滚到保存点，连接不会停在 aborted 状态
    try:
        with conn.begin_nested():
            plan_id = conn.execute(sql, params).scalar()
    except DBAPIError:
        conn.rollback()
        raise
    conn.commit()
    return plan_id


@pytest.fixture
def contract(db):
    with db.connect() as conn:
        company = seed_company(conn)
        yield seed_contract(conn, company), company, conn


def test_defaults_and_valid_insert(contract):
    cid, company, conn = contract
    pid = insert_plan(conn, cid, company)
    row = conn.execute(
        text("SELECT * FROM crm_payment_plan WHERE id = :id"), {"id": pid}
    ).mappings().one()
    assert row["status"] == "pending"
    assert row["invoice_status"] == "not_invoiced"
    assert row["amount_received_fen"] == 0
    assert row["last_received_at"] is None
    assert row["merged_into_id"] is None
    assert row["invoice_no"] is None
    assert row["deleted_at"] is None
    assert row["created_at"] is not None and row["updated_at"] is not None


# ---------- 验收标准 2：同合同同期次唯一 ----------

def test_duplicate_installment_no_same_contract_rejected(contract):
    cid, company, conn = contract
    insert_plan(conn, cid, company, installment_no=1)
    with pytest.raises(IntegrityError, match="uq_crm_payment_plan_contract_installment_active"):
        insert_plan(conn, cid, company, installment_no=1)


def test_same_installment_no_different_contract_allowed(db):
    with db.connect() as conn:
        company = seed_company(conn)
        c1, c2 = seed_contract(conn, company), seed_contract(conn, company)
        insert_plan(conn, c1, company, installment_no=1)
        insert_plan(conn, c2, company, installment_no=1)  # 不抛错


def test_soft_deleted_row_frees_installment_no(contract):
    cid, company, conn = contract
    first = insert_plan(conn, cid, company, installment_no=2)
    conn.execute(
        text("UPDATE crm_payment_plan SET deleted_at = now() WHERE id = :id"), {"id": first}
    )
    second = insert_plan(conn, cid, company, installment_no=2)  # 不抛错
    assert second != first
    # 但未删除的两条仍然冲突
    with pytest.raises(IntegrityError):
        insert_plan(conn, cid, company, installment_no=2)


# ---------- 验收标准 3：金额 CHECK ----------

@pytest.mark.parametrize("bad", [-1, -100, 100_01, 999_99])
def test_amount_received_out_of_range_rejected(contract, bad):
    cid, company, conn = contract
    with pytest.raises(IntegrityError, match="amount_received_fen_range"):
        insert_plan(conn, cid, company, amount_received_fen=bad)


@pytest.mark.parametrize("ok", [0, 1, 50_00, 100_00])
def test_amount_received_in_range_accepted(contract, ok):
    cid, company, conn = contract
    insert_plan(conn, cid, company, amount_received_fen=ok)  # 不抛错


@pytest.mark.parametrize("bad", [0, -1])
def test_amount_due_must_be_positive(contract, bad):
    cid, company, conn = contract
    with pytest.raises(IntegrityError, match="amount_due_fen_positive"):
        insert_plan(conn, cid, company, amount_due_fen=bad, amount_received_fen=0)


def test_amount_null_rejected(contract):
    cid, company, conn = contract
    with pytest.raises(IntegrityError, match="amount_received_fen"):
        insert_plan(conn, cid, company, amount_received_fen=None)
    with pytest.raises(IntegrityError, match="amount_due_fen"):
        insert_plan(conn, cid, company, amount_due_fen=None)


@pytest.mark.parametrize("bad", [0, -1])
def test_installment_no_must_be_gte_1(contract, bad):
    cid, company, conn = contract
    with pytest.raises(IntegrityError, match="installment_no_gte_1"):
        insert_plan(conn, cid, company, installment_no=bad)


# ---------- 验收标准 4：已开票必须有发票号 ----------

def test_invoiced_without_invoice_no_rejected(contract):
    cid, company, conn = contract
    with pytest.raises(IntegrityError, match="invoiced_requires_invoice_no"):
        insert_plan(conn, cid, company, invoice_status="invoiced", invoice_no=None)


def test_invoiced_with_empty_invoice_no_rejected(contract):
    cid, company, conn = contract
    with pytest.raises(IntegrityError, match="invoiced_requires_invoice_no"):
        insert_plan(conn, cid, company, invoice_status="invoiced", invoice_no="")


def test_invoiced_with_invoice_no_accepted(contract):
    cid, company, conn = contract
    pid = insert_plan(
        conn, cid, company, invoice_status="invoiced",
        invoice_no="01234567890123456789", invoice_date=date(2026, 11, 1),
    )
    assert pid


@pytest.mark.parametrize("status", ["not_invoiced", "applied", "red_reversed"])
def test_other_invoice_statuses_do_not_require_invoice_no(contract, status):
    cid, company, conn = contract
    insert_plan(conn, cid, company, invoice_status=status, invoice_no=None)  # 不抛错


@pytest.mark.parametrize("bad", ["unknown", "INVOICED", "cancelled"])
def test_invalid_enum_values_rejected(contract, bad):
    """枚举列不接受枚举定义之外的值（PostgreSQL 原生枚举，大小写敏感）。"""
    cid, company, conn = contract
    with pytest.raises(DataError, match="crm_payment_plan_invoice_status_enum"):
        insert_plan(conn, cid, company, invoice_status=bad)
    with pytest.raises(DataError, match="crm_payment_plan_status_enum"):
        insert_plan(conn, cid, company, status=bad)


def test_invoice_no_longer_than_32_rejected(contract):
    cid, company, conn = contract
    with pytest.raises(DataError, match="character varying\\(32\\)"):
        insert_plan(conn, cid, company, invoice_no="x" * 33)


# ---------- 外键与自引用 ----------

def test_foreign_keys_enforced(db):
    with db.connect() as conn:
        company = seed_company(conn)
        cid = seed_contract(conn, company)
        with pytest.raises(IntegrityError, match="fk_crm_payment_plan_contract_id_crm_contract"):
            insert_plan(conn, uuid.uuid4(), company)
        with pytest.raises(IntegrityError, match="fk_crm_payment_plan_company_id_crm_company"):
            insert_plan(conn, cid, uuid.uuid4())


def test_merged_into_self_reference(contract):
    cid, company, conn = contract
    target = insert_plan(conn, cid, company, installment_no=1)
    merged = insert_plan(conn, cid, company, installment_no=2, merged_into_id=target)
    row = conn.execute(
        text("SELECT merged_into_id FROM crm_payment_plan WHERE id = :id"), {"id": merged}
    ).scalar()
    assert row == target
    with pytest.raises(IntegrityError, match="merged_into"):
        insert_plan(conn, cid, company, installment_no=3, merged_into_id=uuid.uuid4())


def test_required_public_fields(db):
    with db.connect() as conn:
        company = seed_company(conn)
        cid = seed_contract(conn, company)
        with pytest.raises(IntegrityError, match="org_id"):
            insert_plan(conn, cid, company, org_id=None)
        with pytest.raises(IntegrityError, match="site_id"):
            insert_plan(conn, cid, company, site_id=None)
        with pytest.raises(IntegrityError, match="due_date"):
            insert_plan(conn, cid, company, due_date=None)
