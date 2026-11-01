"""Shared declaration corpus: canonical shape, Python relations and actual local Zod.

These controls do not authenticate SQLite origin or basis/value/self hashes.
The Node control is an explicit gate using installed Vite/esbuild; no install,
generation, server, provider or network request is part of the recipe.
"""

from __future__ import annotations

import copy
import json
import shutil
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts import (
    RevisionFeedbackEvaluation,
    RevisionFeedbackEvaluationBasis,
    RevisionFeedbackEvaluationBinding,
    RevisionFeedbackSourceWitness,
)
from packages.contracts_py.decision_hub_contracts import (
    revision_feedback_evaluation_contract_base as base,
)
from packages.contracts_py.decision_hub_contracts.models import (
    FeedbackView,
    ResearchValueEvaluation,
)

ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = ROOT / "tests/fixtures/revision_feedback_evaluation_cases.json"
CORPUS: dict[str, Any] = json.loads(CORPUS_PATH.read_text("utf-8"))
CASES: list[dict[str, Any]] = CORPUS["cases"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("offline declaration test")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def expanded(case: dict[str, Any]) -> dict[str, Any]:
    raw = copy.deepcopy(CORPUS["templates"][case["template"]])
    for patch in case["patches"]:
        target = raw
        for key in patch["path"][:-1]:
            target = target[int(key)] if isinstance(target, list) else target[key]
        last = patch["path"][-1]
        key = int(last) if isinstance(target, list) else last
        if patch["op"] == "remove":
            del target[key]
        else:
            assert patch["op"] == "set"
            target[key] = copy.deepcopy(patch["value"])
    return raw


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_canonical_shape_and_python_relations(case: dict[str, Any]) -> None:
    raw = expanded(case)
    before = copy.deepcopy(raw)
    # Standard JSON Schema checks shape; it does not compare dynamic fields.
    shape = base.revision_feedback_evaluation_validator(case["definition"]).is_valid(raw)
    assert shape is case["expected_shape"]
    if shape and case["definition"] == "revision_feedback_evaluation":
        assert base.revision_feedback_evaluation_issue_codes(raw) == case["expected_issue_codes"]
    model = (
        RevisionFeedbackEvaluationBasis
        if case["definition"] == "revision_feedback_evaluation_basis"
        else RevisionFeedbackEvaluation
    )
    if case["expected_valid"]:
        parsed = model.model_validate(raw)
        model.model_validate_json(parsed.model_dump_json())
    else:
        with pytest.raises(ValidationError):
            model.model_validate(raw)
        with pytest.raises(ValidationError):
            model.model_validate_json(json.dumps(raw, ensure_ascii=False, allow_nan=False))
    assert raw == before


def test_constructed_and_original_dependency_instances_are_revalidated() -> None:
    raw = copy.deepcopy(CORPUS["templates"]["available"])
    raw["feedback"] = FeedbackView.model_validate(raw["feedback"])
    raw["value"] = ResearchValueEvaluation.model_validate(raw["value"])
    raw["binding"]["recorded_at"] = datetime(2026, 10, 3, 4, tzinfo=UTC)
    parsed = RevisionFeedbackEvaluation.model_validate(raw)
    assert parsed.feedback.request_id == "feedback/owner-1/request-1"
    assert parsed.value is not None
    assert parsed.value.evaluation_id == "evaluation-1"
    unsafe = RevisionFeedbackEvaluation.model_construct(
        **{**CORPUS["templates"]["available"], "owner_id": "foreign-owner"}
    )
    with pytest.raises(ValidationError, match="owner_mismatch"):
        RevisionFeedbackEvaluation.model_validate(unsafe)
    bad_feedback = FeedbackView.model_construct(
        **{**CORPUS["templates"]["available"]["feedback"], "notes": True}
    )
    raw["feedback"] = bad_feedback
    with pytest.raises(ValidationError):
        RevisionFeedbackEvaluation.model_validate(raw)


def test_direct_binding_witness_and_basis_constructed_instances() -> None:
    binding = copy.deepcopy(CORPUS["templates"]["available"]["binding"])
    witness = binding["source_witnesses"][0]
    RevisionFeedbackEvaluationBinding.model_validate(binding)
    RevisionFeedbackSourceWitness.model_validate(witness)
    bad_binding = RevisionFeedbackEvaluationBinding.model_construct(
        **{**binding, "recorded_order": True}
    )
    with pytest.raises(ValidationError):
        RevisionFeedbackEvaluationBinding.model_validate(bad_binding)
    bad_basis = RevisionFeedbackEvaluationBasis.model_construct(
        **{**CORPUS["templates"]["basis"], "basis_json": {"not": "a string"}}
    )
    with pytest.raises(ValidationError):
        RevisionFeedbackEvaluationBasis.model_validate(bad_basis)


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_value_is_not_a_strict_json_declaration(number: float) -> None:
    raw = copy.deepcopy(CORPUS["templates"]["available"])
    raw["value"]["known_cost_usd"] = number
    with pytest.raises(ValidationError, match="non_finite_number"):
        RevisionFeedbackEvaluation.model_validate(raw)


def test_naive_python_datetime_is_rejected_before_json_projection() -> None:
    raw = copy.deepcopy(CORPUS["templates"]["available"])
    raw["binding"]["recorded_at"] = datetime(2026, 10, 3, 4)
    with pytest.raises(ValidationError, match="datetime_must_be_aware"):
        RevisionFeedbackEvaluation.model_validate(raw)


def test_existing_zod_executes_the_same_shared_corpus(tmp_path: Path) -> None:
    node = shutil.which("node")
    assert node is not None, "installed Node is required; no skip or download"
    runner = tmp_path / "feedback-conformance.ts"
    output = tmp_path / "feedback-conformance.cjs"
    generated = (ROOT / "packages/contracts_ts/src/generated/r2.ts").as_posix()
    runner.write_text(
        "import { readFileSync } from 'node:fs'\n"
        f"import {{ revisionFeedbackEvaluationSchema, revisionFeedbackEvaluationBasisSchema }} "
        f"from {json.dumps(generated)}\n"
        f"const corpus = JSON.parse(readFileSync({json.dumps(str(CORPUS_PATH))}, 'utf8'))\n"
        "for (const sample of corpus.cases) {\n"
        "  const raw = JSON.parse(JSON.stringify(corpus.templates[sample.template]))\n"
        "  for (const patch of sample.patches) {\n"
        "    let target = raw\n"
        "    for (const key of patch.path.slice(0, -1)) target = target[key]\n"
        "    const key = patch.path[patch.path.length - 1]\n"
        "    if (patch.op === 'remove') delete target[key]\n"
        "    else target[key] = JSON.parse(JSON.stringify(patch.value))\n"
        "  }\n"
        "  const before = JSON.stringify(raw)\n"
        "  const schema = sample.definition === 'revision_feedback_evaluation_basis' "
        "? revisionFeedbackEvaluationBasisSchema : revisionFeedbackEvaluationSchema\n"
        "  const accepted = schema.safeParse(raw).success\n"
        "  if (accepted !== sample.expected_valid) "
        "throw new Error(sample.name + ': Zod mismatch: ' + accepted)\n"
        "  if (JSON.stringify(raw) !== before) throw new Error(sample.name + ': mutated input')\n"
        "}\n"
        "for (const number of [NaN, Infinity, -Infinity]) {\n"
        "  const raw = JSON.parse(JSON.stringify(corpus.templates.available))\n"
        "  raw.value.known_cost_usd = number\n"
        "  if (revisionFeedbackEvaluationSchema.safeParse(raw).success) "
        "throw new Error('Zod accepted non-finite number')\n"
        "}\n"
        "console.log(JSON.stringify({ cases: corpus.cases.length, matched: true, "
        "scope: 'declaration shape and identity; no SQL/hash authentication' }))\n",
        encoding="utf-8",
    )
    # Resolve the already installed Vite -> esbuild dependency. This executes
    # local contract validators only and never invokes package installation.
    bootstrap = (
        "const path = require('node:path'); "
        "const [desk, entry, output] = process.argv.slice(1); "
        "const vite = require.resolve('vite/package.json', { paths: [desk] }); "
        "const esbuild = require(require.resolve('esbuild', { paths: [path.dirname(vite)] })); "
        "esbuild.buildSync({ entryPoints: [entry], outfile: output, bundle: true, "
        "platform: 'node', format: 'cjs', target: 'node18', logLevel: 'error' }); "
        "require(output);"
    )
    completed = subprocess.run(
        [node, "-e", bootstrap, str(ROOT / "apps/decision-desk"), str(runner), str(output)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["cases"] == len(CASES)
    assert result["matched"] is True
