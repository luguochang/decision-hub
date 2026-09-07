from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime

from packages.contracts_py.decision_hub_contracts import (
    ProviderAttempt,
    ProviderRoute,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
)
from packages.kernel.decision_hub_kernel.ports.research import ResearchCapabilityAdapter
from packages.provider_adapters.http_errors import classify_provider_exception


class ProviderCapabilityRouter:
    """Execute an audited provider route list behind one stable capability ID."""

    supported_modes = frozenset({"live", "replay"})

    def __init__(
        self,
        *,
        capability_id: str,
        routes: Sequence[ProviderRoute],
        adapters: Mapping[str, ResearchCapabilityAdapter],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.capability_id = capability_id
        self.routes = tuple(sorted(routes, key=lambda item: (item.priority, item.provider_id)))
        self.adapters = dict(adapters)
        self.clock = clock

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        if query.capability_id != self.capability_id:
            raise ResearchCapabilityError(
                "provider_capability_mismatch",
                "provider router received a query for another capability",
                retryable=False,
                origin="gateway",
            )
        routes = tuple(
            route
            for route in self.routes
            if route.audit_status == "approved" and route.license_status == "approved"
        )
        if not routes:
            raise ResearchCapabilityError(
                "provider_unconfigured",
                "capability has no approved provider route",
                retryable=False,
                origin="gateway",
            )

        routes = _routes_compatible_with_query(routes, query)
        routes = _routes_capable_of_fields(routes, query, self.adapters)
        if not routes:
            raise ResearchCapabilityError(
                "provider_route_unavailable",
                "no approved provider route supports the requested query",
                retryable=False,
                origin="gateway",
            )

        attempts: list[ProviderAttempt] = []
        for index, route in enumerate(routes):
            adapter = self.adapters.get(route.provider_id)
            if adapter is None:
                raise ResearchCapabilityError(
                    "provider_adapter_missing",
                    "approved provider route has no registered adapter",
                    retryable=False,
                    origin="gateway",
                    provider_attempts=attempts,
                )
            if getattr(adapter, "capability_id", None) != self.capability_id:
                raise ResearchCapabilityError(
                    "provider_adapter_capability_mismatch",
                    "provider adapter is not registered for the stable capability",
                    retryable=False,
                    origin="gateway",
                    provider_attempts=attempts,
                )
            if query.mode not in adapter.supported_modes:
                raise ResearchCapabilityError(
                    "provider_mode_denied",
                    "provider route does not support the requested mode",
                    retryable=False,
                    origin="gateway",
                    provider_attempts=attempts,
                )

            started_at = _aware(self.clock())
            try:
                async with asyncio.timeout(route.timeout_seconds):
                    result = await adapter.execute(query)
                finished_at = _aware(self.clock())
                if result.provider != route.provider_id:
                    raise ResearchCapabilityError(
                        "provider_identity_mismatch",
                        "provider result does not match the selected route",
                        retryable=False,
                        origin="provider",
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                finished_at = _aware(self.clock())
                error = classify_provider_exception(exc)
                attempts.append(
                    _attempt(
                        route=route,
                        status="failed",
                        started_at=started_at,
                        finished_at=finished_at,
                        cost_usd=None,
                        error_code=error.error_code,
                        retryable=error.retryable,
                    )
                )
                has_fallback = index + 1 < len(routes)
                if error.retryable and has_fallback:
                    continue
                raise error.with_provider_attempts(attempts) from exc

            attempts.append(
                _attempt(
                    route=route,
                    status="succeeded",
                    started_at=started_at,
                    finished_at=finished_at,
                    cost_usd=result.cost_usd,
                    error_code=None,
                    retryable=None,
                )
            )
            return result.model_copy(
                update={
                    "provider_attempts": [*(result.provider_attempts or []), *attempts],
                }
            )

        raise ResearchCapabilityError(  # pragma: no cover - loop is exhaustive
            "provider_unconfigured",
            "capability has no executable provider route",
            provider_attempts=attempts,
        )


def _attempt(
    *,
    route: ProviderRoute,
    status: str,
    started_at: datetime,
    finished_at: datetime,
    cost_usd: float | None,
    error_code: str | None,
    retryable: bool | None,
) -> ProviderAttempt:
    latency_ms = max(0, round((finished_at - started_at).total_seconds() * 1000))
    return ProviderAttempt.model_validate(
        {
            "provider_id": route.provider_id,
            "route_role": route.route_role,
            "service_tier": route.service_tier,
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
            "latency_ms": latency_ms,
            "cost_usd": cost_usd,
            "error_code": error_code,
            "retryable": retryable,
        }
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("provider_router_clock_must_be_aware")
    return value.astimezone(UTC)


def _routes_compatible_with_query(
    routes: Sequence[ProviderRoute], query: ResearchCapabilityQuery
) -> tuple[ProviderRoute, ...]:
    """Restrict route selection without expanding the Gateway domain allowlist."""

    # Event-relative facts must come exclusively from an event-window route.
    # Conversely, ordinary current-snapshot queries must never see the archive
    # route, even when the caller leaves ``allowed_domains`` empty. This keeps
    # an archive miss from silently degrading into a current quote.
    event_window_requested = query.event_id is not None and bool(query.requested_event_offsets)
    routes = tuple(
        route
        for route in routes
        if bool(getattr(route, "requires_event_window", False)) == event_window_requested
    )
    requested = tuple(_normalize_domain(item) for item in query.allowed_domains)
    if not requested:
        return routes
    return tuple(
        route
        for route in routes
        if any(
            _domain_matches(requested_domain, _normalize_domain(route_domain))
            or _domain_matches(_normalize_domain(route_domain), requested_domain)
            for requested_domain in requested
            for route_domain in route.allowed_domains
        )
    )


def _routes_capable_of_fields(
    routes: Sequence[ProviderRoute],
    query: ResearchCapabilityQuery,
    adapters: Mapping[str, ResearchCapabilityAdapter],
) -> tuple[ProviderRoute, ...]:
    """Skip approved routes that advertise no support for the requested fields.

    The optional adapter metadata keeps a stable capability composable while a
    provider has a narrower surface. Providers without the metadata remain
    eligible and are validated by their canonical result at execution time.
    """

    requested = frozenset(query.fields)
    if not requested:
        return tuple(routes)
    eligible: list[ProviderRoute] = []
    for route in routes:
        supported = getattr(adapters.get(route.provider_id), "supported_fields", None)
        if supported is None or requested <= frozenset(supported):
            eligible.append(route)
    return tuple(eligible)


def _normalize_domain(value: str) -> str:
    return value.strip().lower().rstrip(".").removeprefix(".")


def _domain_matches(host: str, allowed: str) -> bool:
    return host == allowed or host.endswith(f".{allowed}")
