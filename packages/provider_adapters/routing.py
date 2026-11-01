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
    # The Gateway recognizes this seam and replaces the Run-wide deadline with
    # a capability-local deadline that leaves bounded result-finalization time.
    uses_capability_deadline = True

    def __init__(
        self,
        *,
        capability_id: str,
        routes: Sequence[ProviderRoute],
        adapters: Mapping[str, ResearchCapabilityAdapter],
        minimum_successes_by_requirement: Mapping[str, int] | None = None,
        max_attempts_per_provider: int = 1,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.capability_id = capability_id
        for route in routes:
            # Configuration is trusted input, but malformed allowlists must
            # fail at startup instead of widening a route at runtime.
            for domain in route.allowed_domains:
                _normalize_domain(domain)
        self.routes = tuple(sorted(routes, key=lambda item: (item.priority, item.provider_id)))
        self.adapters = dict(adapters)
        self.minimum_successes_by_requirement = dict(minimum_successes_by_requirement or {})
        if max_attempts_per_provider not in {1, 2}:
            raise ValueError("provider_max_attempts_invalid")
        self.max_attempts_per_provider = max_attempts_per_provider
        if any(value < 1 for value in self.minimum_successes_by_requirement.values()):
            raise ValueError("provider_minimum_successes_invalid")
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

        try:
            routes = _routes_compatible_with_query(routes, query)
        except ValueError as exc:
            raise ResearchCapabilityError(
                "provider_domain_invalid",
                "provider route domains must be plain DNS names",
                retryable=False,
                origin="gateway",
                cause_code="invalid_domain",
            ) from exc
        routes = _routes_capable_of_fields(routes, query, self.adapters)
        if not routes:
            raise ResearchCapabilityError(
                "provider_route_unavailable",
                "no approved provider route supports the requested query",
                retryable=False,
                origin="gateway",
            )

        target_successes = min(
            self.minimum_successes_by_requirement.get(query.requirement_id, 1),
            len(routes),
        )

        attempts: list[ProviderAttempt] = []
        successes: list[ResearchCapabilityResult] = []
        last_error: ResearchCapabilityError | None = None
        deadline_at = _aware(query.deadline_at) if query.deadline_at is not None else None
        loop = asyncio.get_running_loop()
        deadline_anchor = (
            _aware(query.observed_at)
            if query.requested_observed_at is not None
            else _aware(self.clock())
        )
        deadline_mono = (
            loop.time() + max(0.0, (deadline_at - deadline_anchor).total_seconds())
            if deadline_at is not None
            else None
        )
        # Try other independent venues before retrying transient failures.
        # Successful providers never count twice toward source independence.
        attempt_routes = routes * self.max_attempts_per_provider
        succeeded_providers: set[str] = set()
        permanent_failures: set[str] = set()
        attempts_started_by_provider: dict[str, int] = {}
        for index, route in enumerate(attempt_routes):
            if route.provider_id in succeeded_providers | permanent_failures:
                continue
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

            remaining = deadline_mono - loop.time() if deadline_mono is not None else None
            if remaining is not None and remaining <= 0:
                if successes:
                    return _merge_results(successes, attempts)
                raise ResearchCapabilityError(
                    "provider_deadline_exhausted",
                    "provider fallback budget was exhausted before this route",
                    retryable=True,
                    origin="transport",
                    cause_code="capability_deadline",
                    provider_attempts=attempts,
                )
            # A retry has already consumed this route's declared attempt
            # window. Do not start another retry unless its full audited
            # window still fits. First attempts remain eligible for a clipped
            # window because an independent public venue can complete quickly.
            if (
                remaining is not None
                and attempts_started_by_provider.get(route.provider_id, 0) > 0
                and remaining < float(route.timeout_seconds)
            ):
                continue
            attempts_started_by_provider[route.provider_id] = (
                attempts_started_by_provider.get(route.provider_id, 0) + 1
            )
            started_at = _aware(self.clock())
            try:
                timeout_seconds = (
                    min(float(route.timeout_seconds), remaining)
                    if remaining is not None
                    else float(route.timeout_seconds)
                )
                async with asyncio.timeout(timeout_seconds):
                    result = await adapter.execute(query)
                finished_at = _aware(self.clock())
                if result.provider != route.provider_id:
                    raise ResearchCapabilityError(
                        "provider_identity_mismatch",
                        "provider result does not match the selected route",
                        retryable=False,
                        origin="provider",
                    )
                if deadline_at is not None and _aware(result.completed_at) > deadline_at:
                    raise ResearchCapabilityError(
                        "provider_deadline_exceeded",
                        "provider result completed after the capability deadline",
                        retryable=True,
                        origin="transport",
                        cause_code="capability_deadline",
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
                last_error = error
                if not error.retryable:
                    permanent_failures.add(route.provider_id)
                has_fallback = index + 1 < len(attempt_routes)
                if has_fallback and (error.retryable or target_successes > 1):
                    # Windows event-loop timers may fire slightly early. Once
                    # the deadline-bounded timeout fires, that budget is spent;
                    # do not start another route in the clock's residual tick.
                    deadline_timeout = (
                        isinstance(exc, TimeoutError)
                        and remaining is not None
                        and remaining <= float(route.timeout_seconds)
                    )
                    if deadline_timeout or (
                        deadline_mono is not None and deadline_mono <= loop.time()
                    ):
                        if successes:
                            return _merge_results(successes, attempts)
                        raise ResearchCapabilityError(
                            "provider_deadline_exhausted",
                            "provider fallback budget was exhausted after a retryable failure",
                            retryable=True,
                            origin="transport",
                            cause_code="capability_deadline",
                            provider_attempts=attempts,
                        ) from exc
                    continue
                if successes:
                    return _merge_results(successes, attempts)
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
            successes.append(result)
            succeeded_providers.add(route.provider_id)
            if len(successes) >= target_successes:
                return _merge_results(successes, attempts)

        if successes:
            return _merge_results(successes, attempts)
        if last_error is not None:
            raise last_error.with_provider_attempts(attempts)

        raise ResearchCapabilityError(  # pragma: no cover - loop is exhaustive
            "provider_unconfigured",
            "capability has no executable provider route",
            provider_attempts=attempts,
        )


def _merge_results(
    results: Sequence[ResearchCapabilityResult],
    attempts: Sequence[ProviderAttempt],
) -> ResearchCapabilityResult:
    """Combine independent venue results behind one auditable capability call."""

    if len(results) == 1:
        result = results[0]
        return result.model_copy(
            update={
                "provider_attempts": [*(result.provider_attempts or []), *attempts],
            }
        )
    costs = [result.cost_usd for result in results]
    return ResearchCapabilityResult(
        schema_version="research-capability-result.v1",
        request_id=results[0].request_id,
        capability_id=results[0].capability_id,
        provider="+".join(result.provider for result in results),
        evidence_candidates=[
            candidate for result in results for candidate in result.evidence_candidates
        ],
        facts=[fact for result in results for fact in (result.facts or [])],
        cost_usd=(
            sum(cost for cost in costs if cost is not None)
            if all(cost is not None for cost in costs)
            else None
        ),
        completed_at=max(result.completed_at for result in results),
        provider_attempts=[
            *(attempt for result in results for attempt in (result.provider_attempts or [])),
            *attempts,
        ],
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
    normalized = value.strip().lower().rstrip(".").removeprefix(".")
    if (
        not normalized
        or "://" in normalized
        or "/" in normalized
        or ":" in normalized
        or "*" in normalized
    ):
        raise ValueError("provider_domain_invalid")
    return normalized


def _domain_matches(host: str, allowed: str) -> bool:
    return host == allowed or host.endswith(f".{allowed}")
