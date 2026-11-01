import hashlib
import json

import pytest

from tools.ops.audit_dsh_catalog import verify_tools


def fixture():
    method = {
        "run_id": "run_test",
        "request_hash": "request",
        "method_hash": "method",
        "definition": {"execution_mode": "live", "allowed_capabilities": ["market.spot"]},
    }
    catalog = {
        "schema_version": "research-query-catalog.v1",
        "capabilities": [{"capability_id": "market.spot", "modes": ["live"]}],
        "scope": {
            "run_id": "run_test",
            "dsh_session_id": "root",
            "request_hash": "request",
            "method_hash": "method",
            "status": "bound",
            "execution_mode": "live",
        },
    }
    return method, catalog


def header(catalog, enum=None):
    catalog["catalog_hash"] = hashlib.sha256(
        json.dumps(
            {k: v for k, v in catalog.items() if k != "catalog_hash"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "tools": [
            {
                "name": "decision_hub_research",
                "description": "Catalog: " + json.dumps(catalog, separators=(",", ":")),
                "parameters": {"properties": {"capability_id": {"enum": enum or ["market.spot"]}}},
            }
        ],
        "messages": [{"content": "private prompt must never enter the audit"}],
    }


def test_actual_scoped_tool_and_no_prompt_output():
    method, catalog = fixture()
    result = verify_tools([header(catalog)], method, "root", "request")
    assert result["tool_definitions"] == 1
    assert all(result["checks"].values())
    assert "private prompt" not in json.dumps(result)


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "other"),
        ("dsh_session_id", "child"),
        ("request_hash", "other"),
        ("method_hash", "other"),
        ("status", "legacy_unbound"),
        ("execution_mode", "replay"),
    ],
)
def test_wrong_identity_fails(field, value):
    method, catalog = fixture()
    catalog["scope"][field] = value
    result = verify_tools([header(catalog)], method, "root", "request")
    assert not result["checks"]["scope_identity"]


def test_global_or_placeholder_definition_is_not_accepted():
    method, catalog = fixture()
    del catalog["scope"]
    result = verify_tools([header(catalog)], method, "root", "request")
    assert not result["checks"]["scope_identity"]
    placeholder = {"tools": [{"name": "decision_hub_research", "description": "generic"}]}
    result = verify_tools([placeholder], method, "root", "request")
    assert not any(result["checks"].values())


def test_catalog_tamper_enum_and_permission_are_independently_checked():
    method, catalog = fixture()
    record = header(catalog, ["official.macro"])
    record["tools"][0]["description"] = record["tools"][0]["description"].replace(
        '"market.spot"',
        '"official.macro"',
    )
    checks = verify_tools([record], method, "root", "request")["checks"]
    assert checks["scope_identity"]
    assert not checks["catalog_hash"]
    assert not checks["allowed_capabilities"]
    assert checks["parameter_enum"]
    result = verify_tools([header(catalog, ["web.fetch"])], method, "root", "request")
    assert not result["checks"]["parameter_enum"]


def test_one_bad_round_cannot_be_hidden_by_later_valid_round():
    method, catalog = fixture()
    result = verify_tools(
        [header(catalog, ["web.fetch"]), header(catalog)],
        method,
        "root",
        "request",
    )
    assert result["tool_definitions"] == 2
    assert not result["checks"]["parameter_enum"]


def test_reviewer_without_research_tool_has_no_fabricated_catalog():
    method, _ = fixture()
    result = verify_tools([{"tools": [{"name": "review"}]}], method, "root", "request")
    assert result["tool_definitions"] == 0
    assert result["catalogs"] == []


@pytest.mark.parametrize("bad_child", [False, True])
def test_archive_descendants_and_inherited_context(tmp_path, monkeypatch, bad_child):
    from contextlib import nullcontext
    from types import SimpleNamespace

    import zstandard

    from tools.ops import audit_dsh_catalog as module

    method, catalog = fixture()
    monkeypatch.setattr(
        module,
        "ResearchMethodService",
        lambda db: SimpleNamespace(
            get=lambda run: method,
        ),
    )
    db = SimpleNamespace(
        session=lambda: nullcontext(
            SimpleNamespace(
                get=lambda model, run: SimpleNamespace(
                    dsh_session_id="root", request_hash="request"
                ),
            )
        )
    )
    for sid, parent, group in [
        ("root", None, "one"),
        ("child", "root", "two"),
        ("reviewer", "child", "two"),
    ]:
        path = tmp_path / group / sid / "session.jsonl.zstd"
        path.parent.mkdir(parents=True)
        meta = {"type": "session", "id": sid, "createdAt": 100}
        if parent:
            meta["parentSession"] = parent
        good = header(catalog)
        invalid = {"tools": [{"name": "decision_hub_research", "description": "generic"}]}
        active = invalid if sid == "child" and bad_child else good
        if sid == "reviewer":
            active = {"tools": []}
        events = [meta]
        if parent:
            # Copied pre-birth context is not a request made by this child.
            events.append(
                {"type": "request/header", "seq": 1, "time": 99, "data": {"header": invalid}}
            )
        events.append({"type": "request/header", "seq": 2, "time": 101, "data": {"header": active}})
        path.write_bytes(zstandard.compress("\n".join(json.dumps(e) for e in events).encode()))
    result = module.audit(db, tmp_path, "run_test")
    assert result["passed"] is (not bad_child)
    assert len(result["sessions"]) == 3
    assert all(row["request_headers"] == 1 for row in result["sessions"])
    assert result["directory_header_errors"] == 0
    assert "private prompt" not in json.dumps(result)


def test_query_input_gate_rejects_legacy_or_partial_metadata_in_any_round():
    from copy import deepcopy

    method, catalog = fixture()
    old = header(deepcopy(catalog))
    assert not verify_tools([old], method, "root", "request", require_query_inputs=True)["checks"][
        "query_input_metadata"
    ]
    catalog["capabilities"][0]["routes"] = [
        {
            key: None
            for key in (
                "symbols",
                "symbols_required",
                "max_symbols",
                "symbol_fields",
                "sources",
                "target_url_required",
                "evidence_ref_required",
                "usage_notes",
            )
        }
    ]
    good = header(catalog)
    good["tools"][0]["parameters"]["properties"].update({"evidence_ref": {}, "content_ref": {}})
    assert verify_tools([good], method, "root", "request", require_query_inputs=True)["checks"][
        "query_input_metadata"
    ]
    assert not verify_tools([old, good], method, "root", "request", require_query_inputs=True)[
        "checks"
    ]["query_input_metadata"]


def test_query_input_source_scope_cannot_advertise_parent_or_unrelated_hosts():
    from tools.ops.audit_dsh_catalog import _sources_in_scope

    c = {
        "allowed_domains": ["example.com"],
        "routes": [
            {
                "allowed_domains": ["api.example.com"],
                "sources": [{"domains": ["api.example.com"]}],
            }
        ],
    }
    assert _sources_in_scope(c)
    c["routes"][0]["sources"][0]["domains"] = ["example.com"]
    assert not _sources_in_scope(c)
    c["routes"][0]["sources"][0]["domains"] = ["evil-example.com"]
    assert not _sources_in_scope(c)
