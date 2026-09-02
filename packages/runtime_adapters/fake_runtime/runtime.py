from __future__ import annotations

import hashlib
import time

from packages.contracts_py.decision_hub_contracts.models import CandidateProposal
from packages.kernel.decision_hub_kernel.ports.runtime import AgentRequest, AgentResult, AgentUsage


class FakeAgentRuntime:
    runtime_id = "fake"
    runtime_version = "fake.v1"
    max_attempts = 1
    cost_budget: float | None = None

    async def execute(self, request: AgentRequest) -> AgentResult:
        started = time.perf_counter()
        text = request.text.lower()
        hawkish = any(
            word in text
            for word in ("higher for longer", "higher", "restrictive", "加息", "更高", "紧缩")
        )
        dovish = any(
            word in text for word in ("cut", "cuts", "降息", "宽松", "support growth", "支持增长")
        )
        direction = (
            "short" if hawkish and not dovish else "long" if dovish and not hawkish else "no_trade"
        )
        probability = 0.58 if direction != "no_trade" else 0.5
        payload: dict[str, object]
        if request.role == "policy_delta":
            payload = {
                "delta": "偏鹰" if hawkish else "偏鸽" if dovish else "未识别明确政策变化",
                "facts": [request.text[:500]],
                "citations": list(request.evidence),
            }
        elif request.role == "counter_thesis":
            payload = {
                "counter_thesis": "市场可能已提前定价，或跨资产确认不足，方向性反应可能反转。",
                "uncertainty": ["缺少完整市场快照", "讲话措辞可能被后续问答修正"],
            }
        else:
            configured = list(request.instructions)
            payload = {
                "direction": direction,
                "probability": probability,
                "headline": "政策信息对 BTC 的即时影响候选",
                "summary": (
                    "根据输入文本抽取政策变化，并给出需要人工确认的条件化判断。"
                    + (f" 候选策略约束：{'；'.join(configured)}" if configured else "")
                ),
                "facts": [request.text[:500]],
                "inferences": ["政策预期变化可能先通过美元、实际利率和风险偏好传导至 BTC。"],
                "counter_thesis": "市场可能已经定价，且单一讲话片段不足以确认持续方向。",
                "uncertainty": [
                    "当前使用文本快照，尚未接入跨资产实时确认",
                    *(["候选策略已注入，但仍由确定性 Gate 裁决"] if configured else []),
                ],
                "transmission_chain": ["政策措辞", "利率预期", "美元/实际利率", "风险偏好", "BTC"],
                "citations": list(request.evidence),
                "trigger": "BTC 在事件后 15 秒可执行价格确认同向突破",
                "invalidation": "实际利率/DXY 与 BTC 反向，或后续讲话修正核心措辞",
            }
        return AgentResult(
            role=request.role,
            payload=payload,
            runtime_id=self.runtime_id,
            runtime_version=self.runtime_version,
            latency_ms=round((time.perf_counter() - started) * 1000),
            cost_usd=None,
            usage=AgentUsage(cost_status="unknown"),
            provider_id="fake",
            model="deterministic-fixture",
            api_mode="offline",
        )


class FakeEvolutionAgentRuntime(FakeAgentRuntime):
    """Deterministic CandidateProposal fallback for offline evolution runs."""

    runtime_id = "fake-evolution"
    runtime_version = "fake-evolution.v1"

    async def execute(self, request: AgentRequest) -> AgentResult:
        started = time.perf_counter()
        identity = hashlib.sha256(
            (request.text + "\n" + "\n".join(request.evidence)).encode()
        ).hexdigest()[:16]
        proposal = CandidateProposal(
            proposal_id=f"proposal:{identity}",
            candidate_type="strategy",
            version=f"candidate.fake.{identity}",
            parent_version=None,
            summary=(
                "Require explicit counter-thesis and cross-asset confirmation before action."
            ),
            changes=[
                "Add a deterministic cross-asset confirmation requirement to the strategy."
            ],
            rationale=(
                "Frozen feedback and evaluation context indicates missing transmission checks."
            ),
            evidence_refs=list(request.evidence),
        )
        return AgentResult(
            role=request.role,
            payload=proposal.model_dump(),
            runtime_id=self.runtime_id,
            runtime_version=self.runtime_version,
            latency_ms=round((time.perf_counter() - started) * 1000),
            usage=AgentUsage(cost_status="unknown"),
            provider_id="fake",
            model="deterministic-evolution-fixture",
            api_mode="offline",
            schema_version="candidate-proposal.v1",
        )
