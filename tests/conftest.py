"""测试夹具：每个会话在一个干净的库上跑一遍 `alembic upgrade head`。

需要 PostgreSQL 16，连接串取自 TEST_DATABASE_URL。库不存在时自动创建。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5581/payment_plan_test"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", DEFAULT_URL)


def _ensure_database(url) -> None:
    server = url.set(database="postgres")
    tmp = create_engine(server, isolation_level="AUTOCOMMIT")
    with tmp.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": url.database}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    tmp.dispose()


@pytest.fixture(scope="session")
def engine():
    url = make_url(TEST_DATABASE_URL)
    # DROP SCHEMA 只允许打在名字含 test 的库上，防止误指到开发/生产库
    if "test" not in (url.database or ""):
        pytest.exit(f"拒绝在库名不含 test 的数据库上跑测试：{url.database}")
    try:
        _ensure_database(url)
    except Exception as exc:  # pragma: no cover
        pytest.exit(
            f"无法连接 PostgreSQL（{url.render_as_string(hide_password=True)}）：{exc}\n"
            "请启动 PostgreSQL 16 并设置 TEST_DATABASE_URL。"
        )
    eng = create_engine(TEST_DATABASE_URL)
    with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        # 每次从零开始，保证迁移在空库上可执行
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    alembic("upgrade", "head")
    yield eng
    eng.dispose()


def alembic(*args: str):
    """在任务目录内以当前环境变量运行 Alembic 命令，返回 CompletedProcess。"""
    env = dict(os.environ, DATABASE_URL=TEST_DATABASE_URL)
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def db(engine):
    """每个用例一张干净的回款计划表。"""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("TRUNCATE crm_payment_plan, crm_contract, crm_company CASCADE"))
    return engine
