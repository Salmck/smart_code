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


def _migrate_additive() -> list[str]:
    """非破坏式迁移：新增缺失的表与列，绝不删除数据。返回已执行的变更说明。

    仅能处理「加表 / 加列」这类向后兼容的结构演进。改列类型、删列、加唯一约束等
    需要重建表的变更不在此列——生产环境应改用正式迁移工具（如 Alembic）。
    """
    from sqlalchemy import inspect, text

    changes: list[str] = []
    # create_all 只创建缺失的表，已存在的表不动，天然幂等且安全
    Base.metadata.create_all(bind=engine)

    insp = inspect(engine)
    dialect = engine.dialect
    with engine.begin() as conn:
        for table in Base.metadata.tables.values():
            if table.name not in set(insp.get_table_names()):
                changes.append(f"新建表 {table.name}")
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                col_type = col.type.compile(dialect=dialect)
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}'
                # 已有数据行需要填充默认值：优先用 server_default，其次对可空列留空。
                if col.server_default is not None:
                    default_sql = col.server_default.arg
                    default_sql = getattr(default_sql, "text", default_sql)
                    ddl += f" DEFAULT {default_sql}"
                elif not col.nullable:
                    # SQLite 给已有行加非空列必须带默认值，退化为按类型给零值。
                    fallback = "0" if "INT" in col_type.upper() or "NUM" in col_type.upper() else "''"
                    ddl += f" NOT NULL DEFAULT {fallback}"
                conn.execute(text(ddl))
                changes.append(f"{table.name}.{col.name} (+列)")
    return changes


def init_db():
    """建表并按需迁移。

    生产（DEBUG=False）：只做非破坏式加表/加列，绝不清空数据。
    开发（DEBUG=True）：结构过期时自动重建并清空，便于快速迭代演示数据。
    """
    import logging
    from . import models  # noqa: F401  确保模型已注册
    log = logging.getLogger("db")

    if not _schema_outdated():
        Base.metadata.create_all(bind=engine)
        return

    if settings.DEBUG:
        log.warning("检测到数据库结构过期（DEBUG 模式），自动重建并清空旧数据 …")
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        return

    log.warning("检测到数据库结构过期（生产模式），执行非破坏式迁移，保留现有数据 …")
    changes = _migrate_additive()
    if changes:
        log.warning("已应用结构变更：%s", "，".join(changes))
