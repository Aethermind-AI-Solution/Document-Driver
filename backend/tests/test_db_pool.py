def test_engine_has_pre_ping_enabled():
    """Stale Neon/Postgres connections must be validated on checkout, else a
    dropped idle connection surfaces as 'SSL connection has been closed
    unexpectedly' on the next query."""
    from app.database import engine
    assert engine.pool._pre_ping is True
