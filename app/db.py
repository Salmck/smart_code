"""数据库连接与会话。

SQLite 开启 WAL 模式：读写并发更好，且写事务串行化——这正是「防重复核销」
所依赖的底层保证（并发核销请求会被数据库串行处理，条件 UPDATE 只有一个能命中）。
换 PostgreSQL 时只需改 config.DATABASE_URL，本文件的 SQLAlchemy 写法完全通用。
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

_is_sqlite = settings.DATABASE_URL.startswith("sqlite")

engine = create_engine(
    settings.DATABASE_URL,
    # SQLite 在多线程（FastAPI 线程池）下需要关闭同线程检查
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    echo=False,
)


if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")     # 写不阻塞读
        cur.execute("PRAGMA foreign_keys=ON")      # 启用外键约束
        cur.execute("PRAGMA busy_timeout=5000")    # 并发写等待而非直接报错
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 依赖：每请求一个会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _schema_outdated() -> bool:
    """检测现有库的表结构是否落后于模型（缺表或缺列）。"""
    from sqlalchemy import inspect
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    if not existing_tables:
        return False  # 全新库，直接建表即可
    for table in Base.metadata.tables.values():
        if table.name not in existing_tables:
            return True
        have = {c["name"] for c in insp.get_columns(table.name)}
        need = {c.name for c in table.columns}
        if need - have:
            return True
    return False


def init_db():
    """建表（幂等）。结构过期时自动重建（当前为演示阶段，允许清空重灌）。"""
    import logging
    from . import models  # noqa: F401  确保模型已注册

    if _schema_outdated():
        logging.getLogger("db").warning("检测到数据库结构过期，自动重建并清空旧数据 …")
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
