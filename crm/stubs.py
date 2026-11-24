"""依赖工单的接口桩：合同表与企业表（仅含本单外键所需列）。

合同与企业模型属于其他工单，尚未交付；为了不阻塞本单的迁移与约束测试，
这里只建最小列，并与业务表共用同一个 Base.metadata（外键才能解析）。
合同/企业工单落地后：删除本文件的模型，用 `CRM_WITH_STUBS=0` 运行 Alembic
即可跳过桩表，回款计划的外键自动指向真实表。
"""
import os
import uuid

from sqlalchemy import ForeignKey, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from crm.base import Base

STUB_COMMENT = "接口桩：合同/企业模型工单落地后移除"


class StubCompany(Base):
    __tablename__ = "crm_company"
    __table_args__ = {"comment": STUB_COMMENT}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)


class StubContract(Base):
    __tablename__ = "crm_contract"
    __table_args__ = {"comment": STUB_COMMENT}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_company.id"), nullable=False)


def stubs_enabled() -> bool:
    return os.environ.get("CRM_WITH_STUBS", "1") not in ("0", "false", "no")
