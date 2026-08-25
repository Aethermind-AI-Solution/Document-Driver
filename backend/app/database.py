from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker, with_loader_criteria
from sqlalchemy.orm import Session as _Session
from .config import DATABASE_URL

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)


def enable_sqlite_pragmas(target_engine):
    """Turn on WAL + a busy timeout for SQLite so concurrent writers wait
    instead of failing with 'database is locked'. No-op for other backends."""
    if not target_engine.url.get_backend_name().startswith("sqlite"):
        return

    @event.listens_for(target_engine, "connect")
    def _set_pragmas(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


enable_sqlite_pragmas(engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    pass


@event.listens_for(_Session, "do_orm_execute")
def _apply_org_filter(state):
    """Fail-closed tenant scoping: every SELECT against a _TenantMixin model
    is auto-filtered to the current org context, unless explicitly opted out
    via .execution_options(skip_org_filter=True) for the few sanctioned
    global queries (login, email dup-check, reset_stuck_processing).

    _TenantMixin and current_org_id are imported lazily inside the handler
    (rather than at module load time) because models.py imports Base from
    this module — importing models.py here at top level would be circular.
    """
    if not state.is_select:
        return
    if state.execution_options.get("skip_org_filter"):
        return
    from .models import _TenantMixin
    from .context import current_org_id
    # Resolve org_id *outside* the lambda and close over the plain value.
    # SQLAlchemy's with_loader_criteria lambda is run through its "lambda SQL"
    # caching system, which forbids invoking functions (like current_org_id())
    # from within the lambda body to produce a literal — it raises
    # InvalidRequestError ("Can't invoke Python callable ... inside of lambda
    # expression"). Calling current_org_id() here also means the fail-closed
    # RuntimeError (org context unset) still surfaces synchronously, before
    # the query runs.
    org_id = current_org_id()
    state.statement = state.statement.options(
        with_loader_criteria(_TenantMixin, lambda cls: cls.org_id == org_id,
                             include_aliases=True))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
