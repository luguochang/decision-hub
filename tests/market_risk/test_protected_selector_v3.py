"""Protected immutable context IDs retain their complete excerpts within real bounds."""

import pytest

from packages.kernel.decision_hub_kernel.application.research_context_selector_v3 import (
    select_context_v3,
)
from tests.research.test_context_selector_v2 import NOW, _item, _options


def test_protected_originals_survive_real_item_budget_and_keep_exact_excerpt():
    admission = _item("admission", NOW, excerpt="authority boundary" * 120)
    path = _item("path", NOW, excerpt="closed bars path limitation" * 100)
    others = [_item(f"event-{index}", NOW, excerpt="primary event") for index in range(20)]
    options = _options()
    result = select_context_v3(
        [admission, path, *others], (), protected_refs=("admission", "path"), **options
    )
    assert len(result.projected) == 12
    originals = {item.evidence_id: item for item in result.projected}
    assert originals["admission"].excerpt == admission.excerpt
    assert originals["path"].excerpt == path.excerpt
    assert result.manifest.rules_ref.endswith("protected.v3")


@pytest.mark.parametrize("failure", ["absent", "future", "excerpt", "items"])
def test_protected_input_never_silently_disappears(failure):
    item = _item("admission", NOW)
    options = _options()
    protected = ("admission",)
    inputs = [item]
    if failure == "absent":
        protected = ("missing",)
    if failure == "future":
        options["eligibility_cutoff_at"] = NOW.replace(year=2025)
    if failure == "excerpt":
        inputs = [_item("admission", NOW, excerpt="boundary" * 200)]
        options["policy"] = options["policy"].model_copy(update={"max_excerpt_chars": 1000})
    if failure == "items":
        inputs += [_item("path", NOW)]
        protected = ("admission", "path")
        options["policy"] = options["policy"].model_copy(update={"max_context_items": 1})
    with pytest.raises(ValueError, match="protected|research_context_empty"):
        select_context_v3(inputs, (), protected_refs=protected, **options)


def test_orphan_v4_execution_link_does_not_fall_back_to_legacy_prompt(tmp_path):
    from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
    from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRoleOutputRecord

    db = Database(f"sqlite+pysqlite:///{tmp_path / 'orphan.sqlite3'}")
    db.create_all()
    with db.session() as session:
        session.add(
            RunRoleOutputRecord(
                output_id="orphan",
                run_id="missing",
                role="context_execution_link:1",
                schema_version="research-context-selection-execution-link.v3",
                payload_json="{}",
                payload_hash="0" * 64,
                created_at=NOW,
            )
        )
    try:
        with pytest.raises(ValueError):
            DshSessionLinkService(db).get_prompt("missing")
    finally:
        db.engine.dispose()
