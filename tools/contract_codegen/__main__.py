from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "contracts" / "generated-manifest.yaml"
PYTHON_GENERATED = {
    ROOT / "contracts/schemas/research_learning.schema.yaml": ROOT
    / "packages/contracts_py/decision_hub_contracts/generated/research_learning.py",
    ROOT / "contracts/schemas/research_skill.schema.yaml": ROOT
    / "packages/contracts_py/decision_hub_contracts/generated/research_skill.py",
    ROOT / "contracts/schemas/research_signal.schema.yaml": ROOT
    / "packages/contracts_py/decision_hub_contracts/generated/research_signal.py",
    ROOT / "contracts/schemas/action_execution_v1.schema.yaml": ROOT
    / "packages/contracts_py/decision_hub_contracts/generated/action_execution_v1.py",
    ROOT / "contracts/schemas/decision_product_v1.schema.yaml": ROOT
    / "packages/contracts_py/decision_hub_contracts/generated/decision_product_v1.py",
    ROOT / "contracts/schemas/decision_product_v2.schema.yaml": ROOT
    / "packages/contracts_py/decision_hub_contracts/generated/decision_product_v2.py",
    ROOT / "contracts/schemas/history_product_status_v2.schema.yaml": ROOT
    / "packages/contracts_py/decision_hub_contracts/generated/history_product_status_v2.py",
    ROOT / "contracts" / "schemas" / "research_context_execution.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "research_context_execution.py"
    ),
    ROOT / "contracts" / "schemas" / "research_execution_binding.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "research_execution_binding.py"
    ),
    ROOT / "contracts" / "schemas" / "research_binding.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "research_binding.py"
    ),
    ROOT / "contracts" / "schemas" / "research_policy.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "research_policy.py"
    ),
    ROOT / "contracts" / "schemas" / "research_market.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "research_market.py"
    ),
    ROOT / "contracts" / "schemas" / "event_asset.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "event_asset.py"
    ),
    ROOT / "contracts" / "schemas" / "product_delivery.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "product_delivery.py"
    ),
    ROOT / "contracts" / "schemas" / "analytical_outlook.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "analytical_outlook.py"
    ),
    ROOT / "contracts" / "schemas" / "source_history.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "source_history.py"
    ),
    ROOT / "contracts" / "schemas" / "study.schema.yaml": (
        ROOT / "packages" / "contracts_py" / "decision_hub_contracts" / "generated" / "study.py"
    ),
    ROOT / "contracts" / "schemas" / "dsh_host_bridge.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "dsh_host_bridge.py"
    ),
    ROOT / "contracts" / "schemas" / "agentic_research.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "agentic_research.py"
    ),
    ROOT / "contracts" / "schemas" / "evolution_job.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "evolution_job.py"
    ),
    ROOT / "contracts" / "schemas" / "historical_data.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "historical_data.py"
    ),
    ROOT / "contracts" / "schemas" / "workbench_assets.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "workbench_assets.py"
    ),
    ROOT / "contracts" / "schemas" / "research_fact.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "research_fact.py"
    ),
    ROOT / "contracts" / "schemas" / "run_inspector.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "run_inspector.py"
    ),
    ROOT / "contracts" / "schemas" / "research_product_view.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "research_product_view.py"
    ),
    ROOT / "contracts" / "schemas" / "source_acquisition.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "source_acquisition.py"
    ),
    ROOT / "contracts" / "schemas" / "research_library.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "research_library.py"
    ),
    ROOT / "contracts" / "schemas" / "research_context_selection.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "research_context_selection.py"
    ),
    ROOT / "contracts" / "schemas" / "research_context_selection_v2.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "research_context_selection_v2.py"
    ),
    ROOT / "contracts" / "schemas" / "research_context_selection_v3.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "research_context_selection_v3.py"
    ),
}
PYTHON_GENERATED[ROOT / "contracts/schemas/harness_comparison.schema.yaml"] = (
    ROOT / "packages/contracts_py/decision_hub_contracts/generated/harness_comparison.py"
)
TS_GENERATED = ROOT / "packages" / "contracts_ts" / "src" / "generated" / "r2.ts"

PYTHON_GENERATED[ROOT / "contracts" / "schemas" / "read_command_receipt.schema.yaml"] = (
    ROOT
    / "packages"
    / "contracts_py"
    / "decision_hub_contracts"
    / "generated"
    / "read_command_receipt.py"
)

PYTHON_GENERATED[ROOT / "contracts" / "schemas" / "revision_feedback_evaluation.schema.yaml"] = (
    ROOT
    / "packages"
    / "contracts_py"
    / "decision_hub_contracts"
    / "generated"
    / "revision_feedback_evaluation.py"
)


PYTHON_GENERATED[ROOT / "contracts/schemas/research_context_selection_v4.schema.yaml"] = (
    ROOT / "packages/contracts_py/decision_hub_contracts/generated/research_context_selection_v4.py"
)
PYTHON_GENERATED[ROOT / "contracts/schemas/research_run_spot_admission.schema.yaml"] = (
    ROOT / "packages/contracts_py/decision_hub_contracts/generated/research_run_spot_admission.py"
)

PYTHON_GENERATED[ROOT / "contracts/schemas/study_effect_tracking.schema.yaml"] = (
    ROOT / "packages/contracts_py/decision_hub_contracts/generated/study_effect_tracking.py"
)

PYTHON_GENERATED[ROOT / "contracts/schemas/notification_envelope.schema.yaml"] = (
    ROOT / "packages/contracts_py/decision_hub_contracts/generated/notification_envelope.py"
)

PYTHON_GENERATED[ROOT / "contracts/schemas/market_risk_view.schema.yaml"] = (
    ROOT / "packages/contracts_py/decision_hub_contracts/generated/market_risk_view.py"
)
PYTHON_GENERATED[ROOT / "contracts/schemas/risk_research_request.schema.yaml"] = (
    ROOT / "packages/contracts_py/decision_hub_contracts/generated/risk_research_request.py"
)
PYTHON_GENERATED[ROOT / "contracts/schemas/independent_review.schema.yaml"] = (
    ROOT / "packages/contracts_py/decision_hub_contracts/generated/independent_review.py"
)
PYTHON_GENERATED[ROOT / "contracts/schemas/provider_input.schema.yaml"] = (
    ROOT / "packages/contracts_py/decision_hub_contracts/generated/provider_input.py"
)


def _schema_digests() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "contracts").rglob("*.schema.yaml"))
    }


@contextmanager
def _python_input(source: Path) -> Generator[Path, None, None]:
    if source.name not in {
        "research_skill.schema.yaml",
        "action_execution_v1.schema.yaml",
        "decision_product_v2.schema.yaml",
        "research_context_execution.schema.yaml",
        "research_policy.schema.yaml",
        "research_binding.schema.yaml",
        "research_execution_binding.schema.yaml",
        "research_context_selection.schema.yaml",
        "research_context_selection_v2.schema.yaml",
        "research_context_selection_v3.schema.yaml",
        "research_context_selection_v4.schema.yaml",
        "revision_feedback_evaluation.schema.yaml",
        "provider_input.schema.yaml",
        "independent_review.schema.yaml",
    }:
        yield source
        return
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    if source.name == "revision_feedback_evaluation.schema.yaml":
        # Keep the public DTOs ordinary Pydantic models with their standard
        # construction/validation APIs. Only this temporary input omits the
        # closed alternatives; the new base validates untouched canonical
        # oneOf branches before conversion, including constructed instances.
        for definition, count in (
            ("revision_feedback_evaluation", 3),
            ("revision_feedback_source_witness", 2),
        ):
            alternatives = document["$defs"][definition].pop("oneOf")
            if not isinstance(alternatives, list) or len(alternatives) != count:
                raise ValueError("revision_feedback_evaluation_alternatives_changed")

    def specialize(value: object) -> None:
        if isinstance(value, dict):
            # Pydantic applies Field(pattern) after date parsing, where it is
            # invalid for AwareDatetime. The new base verifies this original
            # canonical string pattern before conversion; keep AwareDatetime.
            if value.get("format") == "date-time":
                value.pop("pattern", None)
                if source.name == "decision_product_v2.schema.yaml":
                    # The canonical base checks UTC text before datetime parsing.
                    value.pop("minLength", None)
                    value.pop("maxLength", None)
            if source.name in {
                "research_context_execution.schema.yaml",
                "research_execution_binding.schema.yaml",
                "research_context_selection.schema.yaml",
                "research_context_selection_v2.schema.yaml",
                "research_context_selection_v3.schema.yaml",
                "research_context_selection_v4.schema.yaml",
            }:
                # This generator emits constr(...) as a mapping key type for
                # propertyNames, which is not a valid static type expression.
                # The new execution base checks the untouched canonical keys
                # before model parsing; specialize only this temporary input.
                value.pop("propertyNames", None)
            for item in value.values():
                specialize(item)
        elif isinstance(value, list):
            for item in value:
                specialize(item)

    specialize(document)
    with tempfile.TemporaryDirectory(dir=ROOT) as raw_temp:
        candidate = Path(raw_temp) / source.name
        candidate.write_text(json.dumps(document), encoding="utf-8")
        if source.name == "research_skill.schema.yaml":
            # Keep the reference closure local in this temporary specialization.
            # No canonical bytes are edited and remote retrieval stays disabled.
            for dependency_path in source.parent.glob("*.yaml"):
                if dependency_path == source:
                    continue
                dependency = yaml.safe_load(dependency_path.read_text(encoding="utf-8"))
                specialize(dependency)
                (candidate.parent / dependency_path.name).write_text(
                    json.dumps(dependency), encoding="utf-8"
                )
        if source.name in {
            "research_context_execution.schema.yaml",
            "research_binding.schema.yaml",
            "research_execution_binding.schema.yaml",
            "research_context_selection.schema.yaml",
            "research_context_selection_v2.schema.yaml",
            "research_context_selection_v3.schema.yaml",
            "research_context_selection_v4.schema.yaml",
            "revision_feedback_evaluation.schema.yaml",
        }:
            # External canonical DTOs are reused unchanged. Specialize only
            # datamodel-codegen's temporary input so patterns remain checked
            # before conversion by the new canonical strict base.
            dependencies = (
                (
                    "workbench_assets.schema.yaml",
                    "research_product_view.schema.yaml",
                    "agentic_research.schema.yaml",
                    "research_fact.schema.yaml",
                )
                if source.name == "revision_feedback_evaluation.schema.yaml"
                else (
                    "research_execution_binding.schema.yaml",
                    "research_context_selection.schema.yaml",
                    "agentic_research.schema.yaml",
                    "research_policy.schema.yaml",
                    "research_fact.schema.yaml",
                    "dsh_host_bridge.schema.yaml",
                )
                if source.name == "research_context_execution.schema.yaml"
                else (
                    "research_context_selection_v2.schema.yaml",
                    "research_context_selection.schema.yaml",
                    "research_execution_binding.schema.yaml",
                    "agentic_research.schema.yaml",
                    "research_policy.schema.yaml",
                    "research_fact.schema.yaml",
                    "dsh_host_bridge.schema.yaml",
                )
                if source.name
                in {
                    "research_context_selection_v3.schema.yaml",
                    "research_context_selection_v4.schema.yaml",
                }
                else (
                    "research_context_selection.schema.yaml",
                    "research_execution_binding.schema.yaml",
                    "agentic_research.schema.yaml",
                    "research_policy.schema.yaml",
                    "research_fact.schema.yaml",
                    "dsh_host_bridge.schema.yaml",
                )
                if source.name == "research_context_selection_v2.schema.yaml"
                else (
                    "agentic_research.schema.yaml",
                    "research_policy.schema.yaml",
                    "research_fact.schema.yaml",
                    "research_library.schema.yaml",
                )
                if source.name == "research_binding.schema.yaml"
                else (
                    "research_execution_binding.schema.yaml",
                    "agentic_research.schema.yaml",
                    "research_policy.schema.yaml",
                    "research_fact.schema.yaml",
                    "dsh_host_bridge.schema.yaml",
                )
                if source.name == "research_context_selection.schema.yaml"
                else (
                    "agentic_research.schema.yaml",
                    "research_policy.schema.yaml",
                    "research_fact.schema.yaml",
                    "dsh_host_bridge.schema.yaml",
                )
            )
            for name in dependencies:
                dependency = yaml.safe_load((source.parent / name).read_text(encoding="utf-8"))
                specialize(dependency)
                (candidate.parent / name).write_text(json.dumps(dependency), encoding="utf-8")
        yield candidate


def _specialize_provider_attempts(source: Path, generated: bytes) -> bytes:
    # Optional presence does not permit a null array. Restrict this correction
    # to the two original v1 canonical fields; do not narrow other defaults.
    if source.name != "agentic_research.schema.yaml":
        return generated
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    expected = {
        "type": "array",
        "items": {"$ref": "#/$defs/provider_attempt"},
        "maxItems": 20,
        "default": [],
    }
    targets = {
        "ErrorProvenance": "error_provenance",
        "ResearchCapabilityResult": "research_capability_result",
    }
    for definition in targets.values():
        canonical = document["$defs"][definition]
        if canonical["properties"].get(
            "provider_attempts"
        ) != expected or "provider_attempts" in canonical.get("required", []):
            raise ValueError(f"provider_attempts canonical shape changed: {definition}")

    tree = ast.parse(generated)
    lines = generated.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    changes: list[tuple[int, int, bytes]] = []
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    expected_item = ast.dump(ast.parse("list[ProviderAttempt]", mode="eval").body)
    expected_field = ast.dump(
        ast.parse("Field(max_length=20, validate_default=True)", mode="eval").body
    )
    for class_name in targets:
        matched = [node for node in classes if node.name == class_name]
        if len(matched) != 1:
            raise ValueError(f"provider_attempts generated class changed: {class_name}")
        fields = [
            node
            for node in matched[0].body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "provider_attempts"
        ]
        if len(fields) != 1:
            raise ValueError(f"provider_attempts generated field changed: {class_name}")
        field = fields[0]
        annotation = field.annotation
        if not (
            isinstance(field.value, ast.List)
            and not field.value.elts
            and isinstance(annotation, ast.Subscript)
            and isinstance(annotation.value, ast.Name)
            and annotation.value.id == "Annotated"
            and isinstance(annotation.slice, ast.Tuple)
            and len(annotation.slice.elts) == 2
            and ast.dump(annotation.slice.elts[1]) == expected_field
        ):
            raise ValueError(f"provider_attempts generated metadata changed: {class_name}")
        union = annotation.slice.elts[0]
        if not (
            isinstance(union, ast.BinOp)
            and isinstance(union.op, ast.BitOr)
            and ast.dump(union.left) == expected_item
            and isinstance(union.right, ast.Constant)
            and union.right.value is None
            and union.end_lineno is not None
            and union.end_col_offset is not None
            and union.left.end_lineno is not None
            and union.left.end_col_offset is not None
        ):
            raise ValueError(f"provider_attempts generated union changed: {class_name}")
        start = starts[union.lineno - 1] + union.col_offset
        stop = starts[union.end_lineno - 1] + union.end_col_offset
        item_stop = starts[union.left.end_lineno - 1] + union.left.end_col_offset
        changes.append((start, stop, generated[start:item_stop]))
    for start, stop, replacement in sorted(changes, reverse=True):
        generated = generated[:start] + replacement + generated[stop:]
    return generated


def _generate_python(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    policy_options = (
        [
            "--base-class",
            (
                "packages.contracts_py.decision_hub_contracts."
                "context_execution_contract_base.ResearchContextExecutionContract"
                if source.name == "research_context_execution.schema.yaml"
                else "packages.contracts_py.decision_hub_contracts."
                "context_selection_v4_contract_base.ResearchContextSelectionV4Contract"
                if source.name == "research_context_selection_v4.schema.yaml"
                else "packages.contracts_py.decision_hub_contracts."
                "context_selection_v3_contract_base.ResearchContextSelectionV3Contract"
                if source.name == "research_context_selection_v3.schema.yaml"
                else "packages.contracts_py.decision_hub_contracts."
                "context_selection_v2_contract_base.ResearchContextSelectionV2Contract"
                if source.name == "research_context_selection_v2.schema.yaml"
                else "packages.contracts_py.decision_hub_contracts."
                "context_selection_contract_base.ResearchContextSelectionContract"
                if source.name == "research_context_selection.schema.yaml"
                else "packages.contracts_py.decision_hub_contracts."
                "binding_contract_base.ResearchBindingContract"
                if source.name == "research_binding.schema.yaml"
                else "packages.contracts_py.decision_hub_contracts."
                "execution_contract_base.ResearchExecutionContract"
                if source.name == "research_execution_binding.schema.yaml"
                else "packages.contracts_py.decision_hub_contracts."
                "policy_contract_base.ResearchPolicyContract"
            ),
            "--strict-types",
            "str",
            "int",
            "float",
            "bool",
            "--output-datetime-class",
            "AwareDatetime",
        ]
        if source.name
        in {
            "research_context_execution.schema.yaml",
            "research_policy.schema.yaml",
            "research_binding.schema.yaml",
            "research_execution_binding.schema.yaml",
            "research_context_selection.schema.yaml",
            "research_context_selection_v2.schema.yaml",
            "research_context_selection_v3.schema.yaml",
            "research_context_selection_v4.schema.yaml",
        }
        else []
    )
    if source.name == "study_effect_tracking.schema.yaml":
        policy_options = [
            "--base-class",
            "packages.contracts_py.decision_hub_contracts.study_effect_tracking_contract_base.StudyEffectTrackingContract",
            "--strict-types",
            "str",
            "int",
            "float",
            "bool",
            "--output-datetime-class",
            "AwareDatetime",
        ]
    if source.name in {
        "market_risk_view.schema.yaml",
        "risk_research_request.schema.yaml",
        "provider_input.schema.yaml",
        "independent_review.schema.yaml",
    }:
        policy_options = [
            "--strict-types",
            "str",
            "int",
            "float",
            "bool",
            "--output-datetime-class",
            "AwareDatetime",
        ]
    if source.name == "provider_input.schema.yaml":
        policy_options += [
            "--base-class",
            "packages.contracts_py.decision_hub_contracts."
            "provider_input_contract_base.ProviderInputContract",
        ]
    if source.name == "independent_review.schema.yaml":
        policy_options += [
            "--base-class",
            "packages.contracts_py.decision_hub_contracts."
            "independent_review_contract_base.IndependentReviewContract",
        ]
    if source.name == "read_command_receipt.schema.yaml":
        policy_options = [
            "--base-class",
            "packages.contracts_py.decision_hub_contracts."
            "read_command_receipt_contract_base.ReadCommandReceiptContract",
            "--strict-types",
            "str",
            "int",
            "float",
            "bool",
            "--output-datetime-class",
            "AwareDatetime",
        ]
    if source.name == "revision_feedback_evaluation.schema.yaml":
        policy_options = [
            "--base-class",
            "packages.contracts_py.decision_hub_contracts."
            "revision_feedback_evaluation_contract_base.RevisionFeedbackEvaluationContract",
            "--strict-types",
            "str",
            "int",
            "float",
            "bool",
            "--output-datetime-class",
            "AwareDatetime",
        ]
    if source.name == "harness_comparison.schema.yaml":
        policy_options = [
            "--base-class",
            "packages.contracts_py.decision_hub_contracts.harness_comparison_contract_base.HarnessComparisonContract",
            "--strict-types",
            "str",
            "int",
            "float",
            "bool",
            "--output-datetime-class",
            "AwareDatetime",
        ]
    if source.name == "history_product_status_v2.schema.yaml":
        policy_options = [
            "--base-class",
            "packages.contracts_py.decision_hub_contracts.history_product_status_v2_contract_base.HistoryProductContractV2",
            "--strict-types",
            "str",
            "int",
            "float",
            "bool",
            "--output-datetime-class",
            "AwareDatetime",
        ]
    if source.name == "action_execution_v1.schema.yaml":
        policy_options = [
            "--base-class",
            "packages.contracts_py.decision_hub_contracts.action_execution_contract_base.ActionExecutionContract",
            "--strict-types",
            "str",
            "int",
            "float",
            "bool",
            "--output-datetime-class",
            "AwareDatetime",
        ]
    if source.name == "decision_product_v1.schema.yaml":
        policy_options = [
            "--base-class",
            "packages.contracts_py.decision_hub_contracts.decision_product_contract_base.DecisionProductContract",
            "--strict-types",
            "str",
            "int",
            "float",
            "bool",
            "--output-datetime-class",
            "AwareDatetime",
        ]
    if source.name == "decision_product_v2.schema.yaml":
        policy_options = [
            "--base-class",
            "packages.contracts_py.decision_hub_contracts."
            "decision_product_v2_contract_base.DecisionProductV2Contract",
            "--strict-types",
            "str",
            "int",
            "float",
            "bool",
            "--output-datetime-class",
            "AwareDatetime",
        ]
    if source.name == "research_learning.schema.yaml":
        policy_options = [
            "--base-class",
            "packages.contracts_py.decision_hub_contracts.research_learning_contract_base.ResearchLearningContract",
            "--strict-types",
            "str",
            "int",
            "float",
            "bool",
            "--output-datetime-class",
            "AwareDatetime",
        ]
    if source.name == "research_skill.schema.yaml":
        policy_options = [
            "--base-class",
            "packages.contracts_py.decision_hub_contracts.research_skill_contract_base.ResearchSkillContract",
            "--strict-types",
            "str",
            "int",
            "float",
            "bool",
            "--output-datetime-class",
            "AwareDatetime",
        ]
    if source.name == "research_signal.schema.yaml":
        policy_options = [
            "--base-class",
            "packages.contracts_py.decision_hub_contracts.research_signal_contract_base.ResearchSignalContract",
            "--strict-types",
            "str",
            "int",
            "float",
            "bool",
            "--output-datetime-class",
            "AwareDatetime",
        ]
    with _python_input(source) as input_source:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "datamodel_code_generator",
                "--input",
                str(input_source),
                "--input-file-type",
                "jsonschema",
                "--output",
                str(output),
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--target-python-version",
                "3.12",
                "--use-standard-collections",
                "--use-union-operator",
                "--enum-field-as-literal",
                "all",
                "--collapse-root-models",
                "--use-annotated",
                "--use-type-alias",
                "--disable-timestamp",
                "--formatters",
                "ruff-format",
                *policy_options,
            ],
            cwd=ROOT,
            check=True,
        )
    generated = output.read_text(encoding="utf-8")
    if "class ExecutionBudgetV2(" in generated:
        import re

        pattern = (
            r"class ExecutionBudgetV2\("
            r"(?:BaseModel|Research\w+Contract|RevisionFeedbackEvaluationContract)\):"
        )
        generated, count = re.subn(
            pattern, "class ExecutionBudgetV2(ExecutionBudgetV2Contract):", generated
        )
        if count != 1:
            raise ValueError("execution_budget_v2_generated_class_missing")
        generated = generated.replace(
            "from __future__ import annotations\n",
            "from __future__ import annotations\n\n"
            "from packages.contracts_py.decision_hub_contracts."
            "execution_budget_v2_contract_base import ExecutionBudgetV2Contract\n",
            1,
        )
        output.write_text(generated, encoding="utf-8", newline="\n")
    # datamodel-codegen/ruff inherit Windows newlines; normalize generated
    # artifacts so check is deterministic across hosts.
    output.write_text(
        output.read_text(encoding="utf-8").replace("\r\n", "\n"),
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--fix",
            "--ignore",
            "E501",
            str(output),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )

    output.write_bytes(_specialize_provider_attempts(source, output.read_bytes()))


def _generate_typescript(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["node", "tools/contract_codegen/generate_ts.mjs", str(output)],
        cwd=ROOT,
        check=True,
    )


def generate() -> None:
    for source, output in PYTHON_GENERATED.items():
        _generate_python(source, output)
    _generate_typescript(TS_GENERATED)
    MANIFEST.write_text(
        yaml.safe_dump(
            {"manifest_version": "contracts.v1", "schemas": _schema_digests()},
            sort_keys=True,
        ),
        encoding="utf-8",
        newline="\n",
    )


def _check_generated() -> list[str]:
    stale: list[str] = []
    # Keep candidates below the repository so formatters resolve the same
    # project configuration used by the committed generated files.
    with tempfile.TemporaryDirectory(dir=ROOT) as raw_temp:
        temp = Path(raw_temp)
        for source, recorded in PYTHON_GENERATED.items():
            candidate = temp / recorded.name
            _generate_python(source, candidate)
            if not recorded.exists() or recorded.read_bytes() != candidate.read_bytes():
                stale.append(str(recorded.relative_to(ROOT)))
        ts_candidate = temp / "r2.ts"
        _generate_typescript(ts_candidate)
        if not TS_GENERATED.exists() or TS_GENERATED.read_bytes() != ts_candidate.read_bytes():
            stale.append(str(TS_GENERATED.relative_to(ROOT)))
    return stale


def check() -> int:
    failures: list[str] = []
    for path in sorted((ROOT / "contracts").rglob("*.yaml")):
        if path.name in {"versions.yaml"}:
            continue
        try:
            document = yaml.safe_load(path.read_text())
            Draft202012Validator.check_schema(document)
        except Exception as exc:  # pragma: no cover - error output path
            failures.append(f"{path}: {exc}")
    if failures:
        print("\n".join(failures))
        return 1
    current = _schema_digests()
    if MANIFEST.exists():
        recorded = yaml.safe_load(MANIFEST.read_text()).get("schemas", {})
        stale = [path for path, digest in current.items() if recorded.get(path) != digest]
        missing = [path for path in recorded if path not in current]
        if stale or missing:
            print(
                f"generated contract manifest stale; regenerate: stale={stale}, missing={missing}"
            )
            return 1
    stale_generated = _check_generated()
    if stale_generated:
        print(f"generated contract code stale; regenerate: {stale_generated}")
        return 1
    print("canonical schemas: ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["check", "generate", "compatibility"])
    args = parser.parse_args()
    if args.command == "check":
        return check()
    if args.command == "generate":
        generate()
        print(MANIFEST)
        return 0
    print(
        "compatibility: additive schema compatibility is enforced by fixtures in the next R0 slice"
    )
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
