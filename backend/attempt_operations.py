"""Serialize file removal and dispatch against the same controlled sources."""
from contextlib import contextmanager, ExitStack
from functools import wraps
from contextvars import ContextVar
import hashlib
from .platform_runtime import exclusive_lock
HELD = ContextVar('attempt_operation_locks', default=frozenset())


class AttemptBusy(ValueError):
    pass


def source_ids(value):
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ('source_attempt_id', 'upstream_attempt_id', 'parent_attempt_id', 'attempt_id', 'plot_source_attempt_id', 'restart_source_attempt_id') and isinstance(item, str):
                found.add(item)
            else:
                found.update(source_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(source_ids(item))
    return found


@contextmanager
def locked(keys):
    from . import execution
    base = execution.ROOT / 'attempt-operation-locks'
    base.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        owned = HELD.get()
        for key in sorted(set(keys) - owned):
            name = hashlib.sha256(key.encode()).hexdigest() + '.lock'
            if not stack.enter_context(exclusive_lock(base / name)):
                raise AttemptBusy('该 attempt 正在派发、复制来源或核查删除，请稍后重试')
        token = HELD.set(owned | set(keys))
        try:
            yield
        finally:
            HELD.reset(token)


def serialized_submit(function):
    @wraps(function)
    def run(key, *args, **kwargs):
        from . import execution
        record = execution.get(key)
        try:
            from .project_sync import dispatch_guard
            with dispatch_guard(record):
                with locked({key} | source_ids(record)):
                    return function(key, *args, **kwargs)
        except AttemptBusy:
            # Keep an authorized PREPARED task waiting; a brief read-only preview
            # must not turn an unrelated chain into BLOCKED or dispatch twice.
            return execution.get(key)
    return run
