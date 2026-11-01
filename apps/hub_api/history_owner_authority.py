"""History-specific private launch authentication, separate from harness grants."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from .local_owner_session import (
    LocalOwnerPrincipal,
    LocalOwnerSessionAuthority,
)

HISTORY_COOKIE = "decision_hub_history_owner"
HISTORY_COOKIE_PATH = "/v1/history"
HISTORY_DOMAIN = "market-history"


@dataclass(frozen=True, repr=False)
class HistoryOwnerSession:
    cookie_value: str = field(repr=False)
    csrf_token: str = field(repr=False)
    expires_at: datetime


class HistoryOwnerAuthority:
    """Only independently provisioned history pairing/signing material is accepted."""

    def __init__(
        self,
        *,
        signing_key: bytes,
        pairing_sha256: bytes,
        owner_identity: str,
        origin: str,
        lifetime_seconds: int = 600,
    ):
        self._authority = LocalOwnerSessionAuthority(
            signing_key=signing_key,
            pairing_sha256=pairing_sha256,
            owner_identity=owner_identity,
            domain_pack_ref=HISTORY_DOMAIN,
            origin=origin,
            lifetime_seconds=lifetime_seconds,
        )
        self.owner_identity = owner_identity
        self.origin = origin

    def pair(self, credential: str, *, origin: str) -> HistoryOwnerSession:
        value = self._authority.pair(credential, origin=origin)
        return HistoryOwnerSession(
            value.cookie_value,
            value.csrf_token,
            datetime.fromtimestamp(value.expires_at, UTC),
        )

    def resolve(
        self, cookie: str, *, origin: str | None, mutation: bool, csrf_token: str | None = None
    ) -> LocalOwnerPrincipal:
        principal = self._authority.resolve(
            cookie,
            origin=origin,
            mutation=mutation,
            csrf_token=csrf_token,
        )
        if mutation:
            self._authority.require_owner(
                principal,
                owner_identity=self.owner_identity,
                domain_pack_ref=HISTORY_DOMAIN,
                operation="evaluate",
            )
        return principal
