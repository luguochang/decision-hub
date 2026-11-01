"""Bind settings to real contract originals, server policy and explicit account sources."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    AccountSnapshotV2,
    DecisionSettingsCommandV1,
    DecisionSettingsPartitionV1,
    PositionContextV2,
)

from ..ports.market_path import aware_utc
from .decision_execution_profile import read_execution_profile
from .decision_settings import DecisionSettingsService
from .okx_perpetual_source import OKXPerpetualSource


class PaperAccountReader(Protocol):
    """Resolve an owner-scoped perpetual paper account and selected position from originals.

    The adapter must authenticate the registered account/head/refs in this session;
    its projections are compared with, rather than supplied by, the client command.
    The legacy spot paper service is not an implementation of this boundary.
    """

    def __call__(
        self,
        session: Session,
        owner: str,
        partition: DecisionSettingsPartitionV1,
    ) -> tuple[AccountSnapshotV2, PositionContextV2]: ...


class DecisionSettingsBindingReader:
    def __init__(
        self,
        source: OKXPerpetualSource,
        *,
        now: Callable[[], datetime],
        paper_account_reader: PaperAccountReader | None = None,
    ) -> None:
        self.source, self._now, self._paper_account_reader = source, now, paper_account_reader

    def _instrument(self, session: Session, position: PositionContextV2) -> None:
        contract = self.source.read_contract_in_session(session, position.instrument_ref)
        if f"{contract.venue}:{contract.inst_id}" != position.instrument_id:
            raise ValueError("decision_settings_instrument_binding_mismatch")

    @staticmethod
    def _declared_position(position: PositionContextV2) -> None:
        if position.source not in {"user_declared", "unknown"}:
            raise ValueError("decision_settings_declared_source_invalid")
        if any(value is not None for value in (position.ref, position.hash, position.account_ref)):
            raise ValueError("decision_settings_declared_external_original_forbidden")

    def __call__(self, session: Session, owner: str, command: DecisionSettingsCommandV1) -> None:
        command = DecisionSettingsCommandV1.model_validate(command)
        if command.operation == "remove":
            current = DecisionSettingsService.current_in_session(session, owner, command.partition)
            if current is None:
                raise ValueError("decision_settings_remove_missing")
            if current.state_version != command.expected_state_version:
                raise ValueError("decision_settings_state_conflict")
            return

        values, partition = command.values, command.partition
        account, position = values.account_snapshot, values.position_context
        if (account.owner_id, account.account_kind, account.account_id) != (
            owner,
            partition.account_kind,
            partition.account_id,
        ):
            raise PermissionError("decision_settings_account_owner_or_partition_mismatch")
        if aware_utc(account.as_of) > aware_utc(self._now()):
            raise ValueError("decision_settings_account_from_future")
        self._instrument(session, position)
        profile = read_execution_profile(partition.instrument_id, values.execution_profile)
        if profile.profile_ref != partition.profile_ref:
            raise ValueError("decision_settings_profile_binding_mismatch")

        if partition.account_kind == "user_declared":
            if account.source not in {"user_declared", "unknown"}:
                raise ValueError("decision_settings_declared_source_invalid")
            if account.original is not None or values.utility_policy.account_ref is not None:
                raise ValueError("decision_settings_declared_external_original_forbidden")
            self._declared_position(position)
            for declared in account.positions or ():
                self._declared_position(declared)
                self._instrument(session, declared)
            return

        if self._paper_account_reader is None:
            raise ValueError("decision_settings_paper_account_reader_unavailable")
        original_account, original_position = self._paper_account_reader(session, owner, partition)
        original_account = AccountSnapshotV2.model_validate(original_account)
        original_position = PositionContextV2.model_validate(original_position)
        if (
            original_account.source != "paper_policy"
            or original_position.source != "paper_policy"
            or (
                original_account.owner_id,
                original_account.account_kind,
                original_account.account_id,
            )
            != (owner, "paper", partition.account_id)
            or original_position.instrument_id != partition.instrument_id
            or original_account.model_dump(mode="json") != account.model_dump(mode="json")
            or original_position.model_dump(mode="json") != position.model_dump(mode="json")
        ):
            raise ValueError("decision_settings_paper_original_mismatch")
        if values.utility_policy.account_ref != original_account.original:
            raise ValueError("decision_settings_paper_policy_account_mismatch")
