"""create crm_payment_plan

Revision ID: a1c27801c008
Revises:
Create Date: 2026-11-24 10:30:00.000000

工单 S2-CRM-008：回款计划表（期次、应收实收金额、开票状态）。
依赖工单的合同表 / 企业表尚未交付，默认先建最小接口桩；
合同/企业工单落地后，以环境变量 CRM_WITH_STUBS=0 运行即可跳过桩表。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from crm.stubs import STUB_COMMENT, StubCompany, StubContract, stubs_enabled

revision = "a1c27801c008"
down_revision = None
branch_labels = None
depends_on = None

STATUS_VALUES = ("pending", "due", "partial", "paid", "overdue", "written_off")
INVOICE_STATUS_VALUES = ("not_invoiced", "applied", "invoiced", "red_reversed")


def _stub_is_ours(bind, name: str) -> bool:
    """桩表是本次迁移建的还是别人（真实表）的：看表注释。"""
    return bind.execute(
        sa.text("SELECT obj_description(CAST(:t AS regclass), 'pg_class')"),
        {"t": name},
    ).scalar() == STUB_COMMENT


def upgrade() -> None:
    bind = op.get_bind()
    stubs_created = []
    if stubs_enabled():
        for stub in (StubCompany.__table__, StubContract.__table__):
            if not sa.inspect(bind).has_table(stub.name):
                stub.create(bind)
                stubs_created.append(stub.name)

    status_enum = postgresql.ENUM(*STATUS_VALUES, name="crm_payment_plan_status_enum")
    invoice_enum = postgresql.ENUM(
        *INVOICE_STATUS_VALUES, name="crm_payment_plan_invoice_status_enum"
    )
    # 两个枚举由下方 create_table 内联创建，不要在这里重复 create

    op.create_table(
        "crm_payment_plan",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("contract_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("installment_no", sa.SmallInteger(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("amount_due_fen", sa.BigInteger(), nullable=False),
        sa.Column("amount_received_fen", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            status_enum,
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "invoice_status",
            invoice_enum,
            server_default=sa.text("'not_invoiced'"),
            nullable=False,
        ),
        sa.Column("invoice_no", sa.String(length=32), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=True),
        sa.Column("remark", sa.String(length=200), nullable=True),
        sa.Column("merged_into_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("installment_no >= 1", name="ck_crm_payment_plan_installment_no_gte_1"),
        sa.CheckConstraint("amount_due_fen > 0", name="ck_crm_payment_plan_amount_due_fen_positive"),
        sa.CheckConstraint(
            "amount_received_fen BETWEEN 0 AND amount_due_fen",
            name="ck_crm_payment_plan_amount_received_fen_range",
        ),
        sa.CheckConstraint(
            "invoice_status <> 'invoiced' OR (invoice_no IS NOT NULL AND btrim(invoice_no) <> '')",
            name="ck_crm_payment_plan_invoiced_requires_invoice_no",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["crm_company.id"], name="fk_crm_payment_plan_company_id_crm_company"),
        sa.ForeignKeyConstraint(["contract_id"], ["crm_contract.id"], name="fk_crm_payment_plan_contract_id_crm_contract"),
        sa.PrimaryKeyConstraint("id", name="pk_crm_payment_plan"),
        comment="回款计划：一行 = 一份合同的一个回款期次",
    )
    op.create_foreign_key(
        "fk_crm_payment_plan_merged_into_id_crm_payment_plan",
        "crm_payment_plan",
        "crm_payment_plan",
        ["merged_into_id"],
        ["id"],
    )
    op.create_index(
        "ix_crm_payment_plan_org_company_due",
        "crm_payment_plan",
        ["org_id", "company_id", "due_date"],
    )
    op.create_index(
        "ix_crm_payment_plan_org_invoice_status",
        "crm_payment_plan",
        ["org_id", "invoice_status"],
    )
    op.create_index(
        "ix_crm_payment_plan_org_status_due",
        "crm_payment_plan",
        ["org_id", "status", "due_date"],
    )
    op.create_index(
        "uq_crm_payment_plan_contract_installment_active",
        "crm_payment_plan",
        ["contract_id", "installment_no"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index("uq_crm_payment_plan_contract_installment_active", table_name="crm_payment_plan")
    op.drop_index("ix_crm_payment_plan_org_status_due", table_name="crm_payment_plan")
    op.drop_index("ix_crm_payment_plan_org_invoice_status", table_name="crm_payment_plan")
    op.drop_index("ix_crm_payment_plan_org_company_due", table_name="crm_payment_plan")
    op.drop_constraint(
        "fk_crm_payment_plan_merged_into_id_crm_payment_plan",
        "crm_payment_plan",
        type_="foreignkey",
    )
    op.drop_table("crm_payment_plan")

    postgresql.ENUM(name="crm_payment_plan_invoice_status_enum").drop(bind, checkfirst=True)
    postgresql.ENUM(name="crm_payment_plan_status_enum").drop(bind, checkfirst=True)

    # 只删本迁移建的接口桩；真实合同/企业表（注释不同）不动
    for stub in (StubContract.__table__, StubCompany.__table__):
        if sa.inspect(bind).has_table(stub.name) and _stub_is_ours(bind, stub.name):
            stub.drop(bind)
