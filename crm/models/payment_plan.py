"""回款计划表模型：crm_payment_plan。一行 = 一份合同的一个回款期次；金额一律以「分」存整数。"""
import uuid
from datetime import date, datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from crm.base import Base, PublicFieldsMixin


class PaymentStatus(str, PyEnum):
    pending = "pending"          # 未到期
    due = "due"                  # 到期待收
    partial = "partial"          # 部分回款
    paid = "paid"                # 已结清
    overdue = "overdue"          # 逾期
    written_off = "written_off"  # 已核销


class InvoiceStatus(str, PyEnum):
    not_invoiced = "not_invoiced"  # 未开票
    applied = "applied"            # 已申请
    invoiced = "invoiced"          # 已开票
    red_reversed = "red_reversed"  # 已红冲


def _values(enum_cls):
    return [e.value for e in enum_cls]


class CrmPaymentPlan(PublicFieldsMixin, Base):
    __tablename__ = "crm_payment_plan"
    __table_args__ = (
        # 未删除的记录内，同一合同期次号唯一
        Index(
            "uq_crm_payment_plan_contract_installment_active",
            "contract_id",
            "installment_no",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_crm_payment_plan_org_status_due", "org_id", "status", "due_date"),
        Index("ix_crm_payment_plan_org_company_due", "org_id", "company_id", "due_date"),
        Index("ix_crm_payment_plan_org_invoice_status", "org_id", "invoice_status"),
        CheckConstraint("installment_no >= 1", name="installment_no_gte_1"),
        CheckConstraint("amount_due_fen > 0", name="amount_due_fen_positive"),
        CheckConstraint(
            "amount_received_fen BETWEEN 0 AND amount_due_fen",
            name="amount_received_fen_range",
        ),
        CheckConstraint(
            "invoice_status <> 'invoiced' OR (invoice_no IS NOT NULL AND btrim(invoice_no) <> '')",
            name="invoiced_requires_invoice_no",
        ),
        {"comment": "回款计划：一行 = 一份合同的一个回款期次"},
    )

    contract_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crm_contract.id"), nullable=False
    )
    # 从合同冗余，便于按企业筛选
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crm_company.id"), nullable=False
    )
    installment_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_due_fen: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_received_fen: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    last_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="crm_payment_plan_status_enum", values_callable=_values),
        nullable=False,
        server_default=PaymentStatus.pending.value,
    )
    invoice_status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, name="crm_payment_plan_invoice_status_enum", values_callable=_values),
        nullable=False,
        server_default=InvoiceStatus.not_invoiced.value,
    )
    invoice_no: Mapped[str | None] = mapped_column(String(32))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    remark: Mapped[str | None] = mapped_column(String(200))
    # 期次合并时指向合并入的那条期次
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm_payment_plan.id", use_alter=True, name="fk_crm_payment_plan_merged_into_id_crm_payment_plan")
    )
