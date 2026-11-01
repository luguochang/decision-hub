"""Injected local-owner authentication; no header label is a principal.

The launcher must supply independently generated, private pairing/signing
material to every API process. This module does not read Host keys, create an
account, enable approval, or expose an authentication route. Signed sessions
work across processes sharing that trusted launch configuration.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass, field
from time import time
from typing import Callable, Literal
from urllib.parse import urlsplit

Operation = Literal["register", "evaluate", "deploy", "rollback"]
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class LocalOwnerAuthenticationError(PermissionError):
    """Messages are fixed codes, never a supplied credential or cookie."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise LocalOwnerAuthenticationError("local_owner_session_invalid")
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


@dataclass(frozen=True, repr=False)
class LocalOwnerPrincipal:
    owner_identity: str
    domain_pack_ref: str
    expires_at: int
    _mutation_authorized: bool = field(repr=False)
    _claims: bytes = field(repr=False)
    _proof: bytes = field(repr=False)


@dataclass(frozen=True, repr=False)
class LocalOwnerSession:
    """Transport-only secrets; never persist this as a command receipt."""

    cookie_value: str = field(repr=False)
    csrf_token: str = field(repr=False)
    expires_at: int
    http_only: Literal[True] = True
    same_site: Literal["strict"] = "strict"
    cookie_path: Literal["/v1/harness"] = "/v1/harness"


class LocalOwnerSessionAuthority:
    def __init__(
        self, *, signing_key: bytes, pairing_sha256: bytes,
        owner_identity: str, domain_pack_ref: str, origin: str,
        lifetime_seconds: int = 600, clock: Callable[[], float] = time,
        maximum_lifetime_seconds: int = 900,
    ) -> None:
        parsed = urlsplit(origin)
        if (
            not isinstance(signing_key, bytes) or len(signing_key) < 32
            or not isinstance(pairing_sha256, bytes) or len(pairing_sha256) != 32
            or not _ID.fullmatch(owner_identity) or not _ID.fullmatch(domain_pack_ref)
            or parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment or parsed.port is None
            or type(maximum_lifetime_seconds) is not int
            or not 1 <= maximum_lifetime_seconds <= 28_800
            or type(lifetime_seconds) is not int
            or not 1 <= lifetime_seconds <= maximum_lifetime_seconds
        ):
            raise ValueError("local_owner_private_configuration_invalid")
        self._key, self._pairing = bytes(signing_key), bytes(pairing_sha256)
        self.owner_identity, self.domain_pack_ref = owner_identity, domain_pack_ref
        self.origin, self.lifetime_seconds, self.clock = origin, lifetime_seconds, clock

    def _mac(self, domain: bytes, raw: bytes) -> bytes:
        return hmac.digest(self._key, domain + b"\0" + raw, "sha256")

    def pair(self, credential: str, *, origin: str) -> LocalOwnerSession:
        if (
            origin != self.origin or not isinstance(credential, str)
            or not 32 <= len(credential) <= 2048
            or not hmac.compare_digest(hashlib.sha256(credential.encode()).digest(), self._pairing)
        ):
            raise LocalOwnerAuthenticationError("local_owner_pairing_rejected")
        now = int(self.clock())
        claims = json.dumps({
            "v": 1, "owner": self.owner_identity, "pack": self.domain_pack_ref,
            "origin": self.origin, "issued": now, "expires": now + self.lifetime_seconds,
            "nonce": secrets.token_hex(32),
        }, sort_keys=True, separators=(",", ":")).encode()
        token = _b64(claims) + "." + _b64(self._mac(b"session", claims))
        return LocalOwnerSession(token, _b64(self._mac(b"csrf", claims)), now + self.lifetime_seconds)

    def resolve(
        self, cookie: str, *, origin: str | None,
        mutation: bool, csrf_token: str | None = None,
    ) -> LocalOwnerPrincipal:
        try:
            if not isinstance(cookie, str) or len(cookie) > 4096 or type(mutation) is not bool:
                raise ValueError
            encoded, signature = cookie.split(".")
            claims, proof = _decode(encoded), _decode(signature)
            if not hmac.compare_digest(proof, self._mac(b"session", claims)):
                raise ValueError
            value = json.loads(claims)
            if not isinstance(value, dict) or set(value) != {"v", "owner", "pack", "origin", "issued", "expires", "nonce"}:
                raise ValueError
            now = int(self.clock())
            if (
                type(value["v"]) is not int or value["v"] != 1 or value["owner"] != self.owner_identity
                or value["pack"] != self.domain_pack_ref or value["origin"] != self.origin
                or type(value["issued"]) is not int or type(value["expires"]) is not int
                or not value["issued"] <= now < value["expires"]
                or value["expires"] - value["issued"] != self.lifetime_seconds
                or not isinstance(value["nonce"], str) or not re.fullmatch(r"[a-f0-9]{64}", value["nonce"])
                or origin not in {None, self.origin}
            ):
                raise ValueError
            if mutation and (
                origin != self.origin or not isinstance(csrf_token, str)
                or not hmac.compare_digest(csrf_token, _b64(self._mac(b"csrf", claims)))
            ):
                raise ValueError
            permission = b"\0write" if mutation else b"\0read"
            return LocalOwnerPrincipal(self.owner_identity, self.domain_pack_ref,
                                       value["expires"], mutation, claims,
                                       self._mac(b"principal", claims + permission))
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise LocalOwnerAuthenticationError("local_owner_session_rejected") from exc

    def require_owner(
        self, principal: object, *, owner_identity: str,
        domain_pack_ref: str, operation: Operation,
    ) -> None:
        try:
            if (
                not isinstance(principal, LocalOwnerPrincipal)
                or owner_identity != self.owner_identity or domain_pack_ref != self.domain_pack_ref
                or operation not in {"register", "evaluate", "deploy", "rollback"}
                or principal.owner_identity != owner_identity or principal.domain_pack_ref != domain_pack_ref
                or principal._mutation_authorized is not True
                or not hmac.compare_digest(principal._proof, self._mac(b"principal", principal._claims + b"\0write"))
            ):
                raise ValueError
            # Recheck current scope/expiry from authenticated server-owned bytes.
            value = json.loads(principal._claims)
            if not isinstance(value, dict) or set(value) != {"v", "owner", "pack", "origin", "issued", "expires", "nonce"}:
                raise ValueError
            now = int(self.clock())
            if (
                type(value["v"]) is not int or value["v"] != 1
                or value["owner"] != owner_identity or value["pack"] != domain_pack_ref
                or value["origin"] != self.origin
                or type(value["issued"]) is not int or type(value["expires"]) is not int
                or not value["issued"] <= now < value["expires"]
                or value["expires"] - value["issued"] != self.lifetime_seconds
                or value["expires"] != principal.expires_at
                or not isinstance(value["nonce"], str) or not re.fullmatch(r"[a-f0-9]{64}", value["nonce"])
            ):
                raise ValueError
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise LocalOwnerAuthenticationError("local_owner_principal_rejected") from exc
