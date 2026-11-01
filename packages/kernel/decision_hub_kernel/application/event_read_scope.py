"""Reuse native event reads only inside one physical SQLite read transaction.

The scope owns its results; callers cannot supply a verified DTO or cache. It
is discarded before the session is returned, including on verification error.
Outside this explicit scope every reader retains its full native behavior.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class _ReadScope:
    session: object
    archive: object
    transaction: object
    connection: object
    native: object
    archive_identity: object
    nested: object
    changes: int
    original_read: object = None
    values: dict = field(default_factory=dict)


_CURRENT = ContextVar('native_event_read_scope', default=None)


def _archive_identity(archive):
    # The default factory creates a fresh instance for every native reader.
    # Only the exact file reader may share by location; custom readers retain
    # object identity. Include the callable so an altered reader cannot hit.
    from packages.provider_adapters.research.raw_capture import RawBodyArchive

    if type(archive) is RawBodyArchive and 'read' not in vars(archive):
        return RawBodyArchive, RawBodyArchive.read, Path(archive.root).resolve()
    return type(archive), id(archive)


def _current(session, archive):
    scope = _CURRENT.get()
    if scope is None or scope.session is not session:
        return None
    if _archive_identity(scope.archive) != scope.archive_identity:
        raise ValueError('event_read_scope_archive_changed')
    if _archive_identity(archive) != scope.archive_identity:
        return None
    if (session.get_transaction() is not scope.transaction
            or not scope.transaction.is_active
            or session.get_nested_transaction() is not scope.nested
            or session.connection() is not scope.connection
            or scope.connection.connection.driver_connection is not scope.native
            or not scope.native.in_transaction
            or scope.native.total_changes != scope.changes
            or (scope.original_read is not None and not scope.original_read.unchanged())):
        raise ValueError('event_read_scope_physical_snapshot_changed')
    if session.new or session.dirty or session.deleted:
        raise ValueError('event_read_scope_requires_read_only_session')
    return scope


def _native_read_once(session, archive, key, read, *, validate=None):
    """Private native-reader hook; only successful complete reads are retained."""
    scope = _current(session, archive)
    if scope is None:
        return read()
    if key in scope.values:
        # DB originals remain in the physical snapshot. Archives are external
        # files, so native readers recheck their bytes on a dependency hit.
        if validate is not None:
            validate(scope.values[key])
        if _current(session, archive) is not scope:
            raise ValueError('event_read_scope_archive_changed')
    else:
        value = read()
        if _current(session, archive) is not scope:
            raise ValueError('event_read_scope_archive_changed')
        scope.values[key] = value
    return scope.values[key]


@contextmanager
def native_event_read_scope(session, *, archive, original_read=None):
    """Bind native reader reuse to a physically read-only request snapshot.

    SQLite SELECT in legacy mode does not begin a physical transaction. Begin
    before the first metadata SELECT, and prohibit SQL/ORM writes for the scope.
    An enclosing original preparation can supply its exact local read boundary;
    it already owns physical BEGIN and original-read protection, so this path
    issues no PRAGMA or replaces the caller's SELECT-only authorizer. The local
    read boundary remains checked on use and successful exit.
    Other backends retain ordinary native reads without reusing results.
    """
    existing = _current(session, archive)
    if existing is not None:
        yield
        return
    if session.new or session.dirty or session.deleted:
        raise ValueError('event_read_scope_requires_read_only_session')
    connection = session.connection()
    if connection.dialect.name != 'sqlite':
        yield
        return
    native = connection.connection.driver_connection
    if native is None:
        raise ValueError('event_read_scope_physical_snapshot_required')
    query_only = None
    if original_read is not None:
        from .research_graph_binding import _LocalOriginalRead

        if (type(original_read) is not _LocalOriginalRead
                or original_read.session is not session
                or original_read.connection is not connection
                or original_read.driver is not native
                or not original_read.unchanged()):
            raise ValueError('event_read_scope_physical_snapshot_required')
    else:
        query_only = connection.exec_driver_sql('PRAGMA query_only').scalar_one()
        connection.exec_driver_sql('PRAGMA query_only=ON')
        if not native.in_transaction:
            connection.exec_driver_sql('BEGIN')
    scope = _ReadScope(
        session, archive, session.get_transaction(), connection, native,
        _archive_identity(archive), session.get_nested_transaction(), native.total_changes,
        original_read,
    )
    token = _CURRENT.set(scope)
    try:
        yield
        _current(session, archive)
    finally:
        scope.values.clear()
        _CURRENT.reset(token)
        if (query_only is not None and not connection.closed
                and connection.connection.driver_connection is native):
            connection.exec_driver_sql('PRAGMA query_only=' + ('ON' if query_only else 'OFF'))
