from contextvars import ContextVar

_current_org: ContextVar[int | None] = ContextVar("current_org_id", default=None)


def set_current_org(org_id: int | None):
    return _current_org.set(org_id)


def reset_org(token) -> None:
    _current_org.reset(token)


def current_org_id() -> int | None:
    # NOTE: hardened to raise-when-unset in Task 4 once the loader-criteria is live.
    return _current_org.get()
