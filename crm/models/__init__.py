"""CRM 核心对象。"""
from crm import stubs
from crm.base import Base
from crm.models.payment_plan import CrmPaymentPlan

if stubs.stubs_enabled():
    from crm.stubs import StubCompany, StubContract  # noqa: F401  注册桩表到 Base.metadata

__all__ = ["Base", "CrmPaymentPlan"]
