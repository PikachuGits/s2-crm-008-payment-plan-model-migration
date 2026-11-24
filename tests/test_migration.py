"""迁移往返与结构一致性：验收标准 1。"""
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from conftest import alembic
from crm.models import Base

EXPECTED_COLUMNS = {
    "id", "org_id", "site_id", "created_at", "created_by", "updated_at", "deleted_at",
    "contract_id", "company_id", "installment_no", "due_date",
    "amount_due_fen", "amount_received_fen", "last_received_at",
    "status", "invoice_status", "invoice_no", "invoice_date", "remark", "merged_into_id",
}
EXPECTED_CHECKS = {
    "ck_crm_payment_plan_installment_no_gte_1",
    "ck_crm_payment_plan_amount_due_fen_positive",
    "ck_crm_payment_plan_amount_received_fen_range",
    "ck_crm_payment_plan_invoiced_requires_invoice_no",
}
EXPECTED_INDEXES = {
    "uq_crm_payment_plan_contract_installment_active",
    "ix_crm_payment_plan_org_status_due",
    "ix_crm_payment_plan_org_company_due",
    "ix_crm_payment_plan_org_invoice_status",
}


def test_upgrade_head_twice_and_alembic_check_zero_drift(engine):
    """upgrade / downgrade 往返各两遍，且模型与库结构零漂移。"""
    for _ in range(2):
        down = alembic("downgrade", "base")
        assert down.returncode == 0, down.stderr
        tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
        assert tables == set(), f"downgrade 后仍有残留表：{tables}"
        enums = _enum_names(engine)
        assert enums == set(), f"downgrade 后仍有残留枚举：{enums}"

        up = alembic("upgrade", "head")
        assert up.returncode == 0, up.stderr

    chk = alembic("check")
    assert chk.returncode == 0, chk.stdout + chk.stderr
    assert "No new upgrade operations detected" in chk.stdout


def test_model_metadata_matches_database(engine):
    """autogenerate 比对：数据库 == 模型（与 alembic check 同源，便于定位差异）。"""
    with engine.connect() as conn:
        mc = MigrationContext.configure(
            conn, opts={"compare_type": True, "compare_server_default": True}
        )
        diffs = list(compare_metadata(mc, Base.metadata))
    assert diffs == []


def test_table_shape(engine):
    insp = inspect(engine)
    cols = {c["name"]: c for c in insp.get_columns("crm_payment_plan")}
    assert set(cols) == EXPECTED_COLUMNS

    assert cols["amount_due_fen"]["type"].__class__.__name__ == "BIGINT"
    assert cols["amount_received_fen"]["type"].__class__.__name__ == "BIGINT"
    assert cols["installment_no"]["type"].__class__.__name__ == "SMALLINT"
    assert str(cols["due_date"]["type"].__class__.__name__) in ("DATE",)
    assert not cols["amount_received_fen"]["nullable"]
    assert cols["merged_into_id"]["nullable"]

    uniques = {u["name"] for u in insp.get_indexes("crm_payment_plan") if u["unique"]}
    assert uniques == {"uq_crm_payment_plan_contract_installment_active"}
    checks = {c["name"] for c in insp.get_check_constraints("crm_payment_plan")}
    assert checks == EXPECTED_CHECKS
    indexes = {i["name"] for i in insp.get_indexes("crm_payment_plan")}
    assert indexes >= EXPECTED_INDEXES


def test_partial_unique_index_predicate(engine):
    with engine.connect() as conn:
        pred = conn.execute(
            text(
                "SELECT pg_get_indexdef('uq_crm_payment_plan_contract_installment_active'::regclass)"
            )
        ).scalar()
    assert "WHERE" in pred and "deleted_at IS NULL" in pred
    assert "contract_id, installment_no" in pred


def test_index_columns(engine):
    insp = inspect(engine)
    got = {i["name"]: i["column_names"] for i in insp.get_indexes("crm_payment_plan")}
    assert got["ix_crm_payment_plan_org_status_due"] == ["org_id", "status", "due_date"]
    assert got["ix_crm_payment_plan_org_company_due"] == ["org_id", "company_id", "due_date"]
    assert got["ix_crm_payment_plan_org_invoice_status"] == ["org_id", "invoice_status"]


def test_enum_types_and_values(engine):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT t.typname, array_agg(e.enumlabel ORDER BY e.enumsortorder) "
                "FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid "
                "WHERE t.typname LIKE 'crm\\_payment\\_plan%' GROUP BY t.typname"
            )
        ).fetchall()
    enums = dict(rows)
    assert enums["crm_payment_plan_status_enum"] == [
        "pending", "due", "partial", "paid", "overdue", "written_off",
    ]
    assert enums["crm_payment_plan_invoice_status_enum"] == [
        "not_invoiced", "applied", "invoiced", "red_reversed",
    ]


def test_foreign_keys(engine):
    fks = {fk["name"]: (fk["referred_table"], fk["constrained_columns"]) for fk in inspect(engine).get_foreign_keys("crm_payment_plan")}
    assert fks["fk_crm_payment_plan_contract_id_crm_contract"] == ("crm_contract", ["contract_id"])
    assert fks["fk_crm_payment_plan_company_id_crm_company"] == ("crm_company", ["company_id"])
    assert fks["fk_crm_payment_plan_merged_into_id_crm_payment_plan"] == ("crm_payment_plan", ["merged_into_id"])


def _enum_names(engine):
    with engine.connect() as conn:
        return {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
                    "WHERE n.nspname = 'public' AND t.typtype = 'e'"
                )
            )
        }
