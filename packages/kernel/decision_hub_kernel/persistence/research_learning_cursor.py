"""Bounded fair intake progress. Cursor state never grants learning permission."""
from dataclasses import dataclass

from sqlalchemy import select, update, exists, func

from .research_learning_records import LearningRegistrationRecord, LearningScanCursorRecord
from ..ports.research_learning import aware


@dataclass(frozen=True)
class ScanBatch:
    scanner_id: str
    expected_version: int
    previous_id: str | None
    registration_ids: tuple[str, ...]
    source_kind: str = 'registration'


class LearningScanCursor:
    def __init__(self, session):
        self.session = session

    def prepare(self, scanner_id: str, *, limit: int = 2, source_kind: str = 'registration') -> ScanBatch:
        if not isinstance(scanner_id, str) or not 1 <= len(scanner_id) <= 128:
            raise ValueError("learning_scanner_identity_invalid")
        if type(limit) is not int or not 1 <= limit <= 2:
            raise ValueError("learning_scanner_batch_budget_invalid")
        row = self.session.get(LearningScanCursorRecord, scanner_id, populate_existing=True)
        previous = row.last_registration_id if row else None
        if source_kind == 'registration':
            key = LearningRegistrationRecord.registration_id
            query = select(key)
        elif source_kind == 'portfolio':
            from .action_paper_records import AccountHead
            key = AccountHead.account_id
            query = select(key).where(
                func.json_extract(AccountHead.identity_json, '$.horizon') == '24h',
                func.json_extract(AccountHead.identity_json, '$.scope_id').in_((
                    'crypto:spot:okx:BTC-USDT:trade:1m', 'crypto:spot:okx:ETH-USDT:trade:1m')),
                ~exists(select(LearningRegistrationRecord.registration_id).where(
                    LearningRegistrationRecord.portfolio_id == AccountHead.account_id)))
        else:
            raise ValueError('learning_cursor_source_kind_invalid')
        query = query.order_by(key)
        tail = query.where(key > previous) if previous else query
        values = list(self.session.scalars(tail.limit(limit)))
        if previous and len(values) < limit:
            values.extend(self.session.scalars(query.where(
                key <= previous
            ).limit(limit - len(values))))
        return ScanBatch(scanner_id, row.version if row else 0, previous, tuple(values), source_kind)

    def advance(self, batch: ScanBatch, *, now):
        connection = self.session.connection()
        if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
            raise ValueError("learning_cursor_explicit_writer_required")
        now = aware(now)
        # Recompute both the CAS version and the bounded set in the writer snapshot.
        if self.prepare(batch.scanner_id, limit=max(1, len(batch.registration_ids)), source_kind=batch.source_kind) != batch:
            raise ValueError("learning_cursor_compare_and_swap_failed")
        if not batch.registration_ids:
            return
        if batch.expected_version == 0:
            self.session.add(LearningScanCursorRecord(
                scanner_id=batch.scanner_id, version=1,
                last_registration_id=batch.registration_ids[-1], updated_at=now,
            ))
            self.session.flush()
            return
        statement = update(LearningScanCursorRecord).where(
            LearningScanCursorRecord.scanner_id == batch.scanner_id,
            LearningScanCursorRecord.version == batch.expected_version,
            LearningScanCursorRecord.last_registration_id == batch.previous_id,
        ).values(version=batch.expected_version + 1,
                 last_registration_id=batch.registration_ids[-1], updated_at=now)
        if self.session.execute(statement).rowcount != 1:
            raise ValueError("learning_cursor_compare_and_swap_failed")
