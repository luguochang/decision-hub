"""Authenticated research direction for the user's existing explicit PAPER flow.

No unit-plan comparison grants quantity, account feasibility or native execution
authority. The existing manual service owns current-account risk and execution.
"""

from datetime import datetime, timedelta

from ..persistence.action_repository import ActionRepository
from ..ports.market_path import aware_utc
from ..ports.perpetual_manual_execution import EventManualBinding, manual_horizon
from .event_scenario_publication import read_latest_published_event_scenario_plan_in_session
from .event_scenario_watch import (
    _retained_origin_progress,
    event_scenario_advice_view_in_session,
)
from .perpetual_condition_monitor import condition_new_exposure_blocked_in_session
from .perpetual_plan_watch_continuous import _check_origin, _latest, _latest_progress


def _at(value):
    return aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def read_event_manual_in_session(session, *, owner_id, command, at):
    """Read the current published/selected originals, never a client-provided side."""
    at = aware_utc(at)
    bundle = read_latest_published_event_scenario_plan_in_session(
        session,
        owner_id=owner_id,
        settings_ref=command.settings_revision,
        partition=command.partition,
        horizon=manual_horizon(command),
        at=at,
    )
    view = event_scenario_advice_view_in_session(session, bundle=bundle, at=at)
    if view is None or view["current_state"] != "active":
        raise ValueError("perp_manual_event_not_active")
    declared = command.model_dump(mode="json")
    if any(
        declared[name] != value
        for name, value in (
            ("event_plan_ref", view["plan_ref"]),
            ("selection_ref", view["selection_ref"]),
            ("event_review_ref", view["review_authorization_ref"]),
            ("event_publication_ref", view["publication_ref"]),
        )
    ):
        raise ValueError("perp_manual_event_selection_changed")
    selection, record = view["selection"], view["selection_original"]
    if (
        selection is None
        or record is None
        or selection["status"] != "selected_preapproved_branch"
        or selection["plan_ref"] != view["plan_ref"]
        or selection["review_authorization_ref"] != view["review_authorization_ref"]
        or selection["branch_ref"] != declared["branch_ref"]
        or record["publication_ref"] != view["publication_ref"]
        or record["owner_id"] != owner_id
        or record["plan_revision_ref"] != declared["revision"]
        or record["plan_id"] not in bundle["plan_ids"]
    ):
        raise ValueError("perp_manual_event_entry_not_selected")
    plan, scope = bundle["plan"], bundle["review_scope"]
    branch = next(
        (row for row in plan["branches"] if row["branch_ref"] == declared["branch_ref"]), None
    )
    if (
        branch is None
        or scope["verdict"] != "approved"
        or branch["target"]["horizon"] != manual_horizon(command)
    ):
        raise ValueError("perp_manual_event_branch_not_reviewed")
    flat = branch["comparisons_by_position"]["flat"]["comparison"]
    rows = [
        row
        for row, reference in zip(
            flat["candidate_results"],
            branch["reviewed_candidate_refs_by_position"]["flat"],
            strict=True,
        )
        if reference == selection["selected_candidate_ref"]
    ]
    action = selection["selected_action_id"]
    if (
        len(rows) != 1
        or rows[0]["candidate_id"] != action
        or action
        not in {
            "open_long",
            "open_short",
        }
    ):
        raise ValueError("perp_manual_event_flat_entry_required")
    domain = branch["applicability_domain"]
    latest = min(
        _at(branch["valid_until"]),
        _at(domain["valid_until"]),
        _at(scope["valid_until"]),
        _at(branch["target"]["window_end"]),
    )
    latest = min(
        latest,
        _at(branch["target"]["window_start"])
        + timedelta(
            seconds=domain["maximum_elapsed_seconds"],
        ),
    )
    if not _at(branch["applicable_from"]) <= at < latest:
        raise ValueError("perp_manual_event_entry_permission_elapsed")
    consent = _latest(session, record["watch_id"])
    progress = _latest_progress(session, record["watch_id"])
    origin = _retained_origin_progress(session, consent, bundle) if consent else None
    if (
        consent is None
        or consent.state != "active"
        or consent.revision.model_dump(mode="json") != record["watch_ref"]
        or progress is None
        or progress["status"] == "invalidated"
        or origin is None
        or origin["plan_revision_ref"] != declared["revision"]
        or consent.owner_id != owner_id
        or consent.command.partition != command.partition
    ):
        raise ValueError("perp_manual_event_watch_changed")
    _check_origin(session, consent, origin, at, require_current=True)
    repo = ActionRepository(session)
    revision, head = repo.get_revision(command.revision.ref), repo.get_head(record["plan_id"])
    if (
        revision is None
        or revision.payload_hash != command.revision.hash
        or head is None
        or head.current_revision_id != command.revision.ref
        or head.state in {"invalidated", "superseded"}
        or condition_new_exposure_blocked_in_session(
            session,
            owner_id=owner_id,
            plan_id=record["plan_id"],
            revision_ref=declared["revision"],
        )
    ):
        raise ValueError("perp_manual_event_origin_invalidated")
    return dict(
        plan_id=record["plan_id"],
        revision_ref=declared["revision"],
        candidate_id=action,
        target_side=action.removeprefix("open_"),
        target=branch["target"],
        latest_entry_at=latest.isoformat(),
        published_at=revision.published_at.isoformat(),
        head_state_version=head.state_version,
        event_binding={name: declared[name] for name in EventManualBinding.model_fields},
    )
