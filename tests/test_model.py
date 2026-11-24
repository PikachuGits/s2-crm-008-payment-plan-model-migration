"""ORM 模型可用性：枚举映射、写入读出与状态流转。"""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from crm.models import CrmPaymentPlan
from crm.models.payment_plan import InvoiceStatus, PaymentStatus

ORG = uuid.uuid4()


def seed(conn):
    """在 autocommit 连接里写一行企业桩与合同桩，返回 (contract_id, company_id)。"""
    company, contract = uuid.uuid4(), uuid.uuid4()
    conn.execute(
        text("INSERT INTO crm_company (id, org_id) VALUES (:id, :org)"),
        {"id": company, "org": ORG},
    )
    conn.execute(
        text("INSERT INTO crm_contract (id, org_id, company_id) VALUES (:id, :org, :c)"),
        {"id": contract, "org": ORG, "c": company},
    )
    return contract, company


def new_plan(contract, company, **kw):
    params = dict(
        org_id=ORG,
        site_id=uuid.uuid4(),
        contract_id=contract,
        company_id=company,
        installment_no=1,
        due_date=date.today() + timedelta(days=7),
        amount_due_fen=100_00,
    )
    params.update(kw)
    return CrmPaymentPlan(**params)


@pytest.fixture
def seeded(db):
    with db.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        yield seed(conn)


def test_model_roundtrip_and_enum_mapping(db, seeded):
    contract, company = seeded
    with Session(db) as s:
        plan = new_plan(contract, company)
        s.add(plan)
        s.commit()
        loaded = s.get(CrmPaymentPlan, plan.id)
        assert loaded.status is PaymentStatus.pending
        assert loaded.invoice_status is InvoiceStatus.not_invoiced
        assert loaded.amount_received_fen == 0
        assert loaded.id is not None and loaded.created_at is not None


def test_status_transitions_via_orm(db, seeded):
    contract, company = seeded
    with Session(db) as s:
        plan = new_plan(contract, company)
        s.add(plan)
        s.commit()

        plan.amount_received_fen = 40_00          # 部分回款
        plan.status = PaymentStatus.partial
        s.commit()
        got = s.scalar(select(CrmPaymentPlan.status).where(CrmPaymentPlan.id == plan.id))
        assert got is PaymentStatus.partial

        plan.amount_received_fen = 100_00         # 结清并开票
        plan.status = PaymentStatus.paid
        plan.invoice_status = InvoiceStatus.invoiced
        plan.invoice_no = "INV-20261124-001"
        plan.invoice_date = date.today()
        s.commit()
        plan_id = plan.id

    with Session(db) as s:
        loaded = s.get(CrmPaymentPlan, plan_id)
        assert (loaded.status, loaded.invoice_status, loaded.invoice_no) == (
            PaymentStatus.paid, InvoiceStatus.invoiced, "INV-20261124-001",
        )


def test_orm_violation_raises_integrity_error(db, seeded):
    contract, company = seeded
    with Session(db) as s:
        s.add(new_plan(contract, company, invoice_status=InvoiceStatus.invoiced, invoice_no=None))
        with pytest.raises(IntegrityError, match="invoiced_requires_invoice_no"):
            s.commit()


def test_orm_received_exceeding_due_rejected(db, seeded):
    contract, company = seeded
    with Session(db) as s:
        s.add(new_plan(contract, company, amount_received_fen=100_01))
        with pytest.raises(IntegrityError, match="amount_received_fen_range"):
            s.commit()


def test_orm_duplicate_installment_rejected(db, seeded):
    contract, company = seeded
    with Session(db) as s:
        s.add(new_plan(contract, company, installment_no=1))
        s.commit()
        s.add(new_plan(contract, company, installment_no=1))
        with pytest.raises(IntegrityError, match="uq_crm_payment_plan_contract_installment_active"):
            s.commit()


def test_soft_delete_via_orm_allows_reuse(db, seeded):
    contract, company = seeded
    with Session(db) as s:
        first = new_plan(contract, company, installment_no=1)
        s.add(first)
        s.commit()
        first.deleted_at = first.updated_at
        s.commit()
        s.add(new_plan(contract, company, installment_no=1))
        s.commit()  # 不抛错
        assert s.scalar(
            select(CrmPaymentPlan.id).where(
                CrmPaymentPlan.contract_id == contract, CrmPaymentPlan.deleted_at.is_(None)
            )
        ) is not None
