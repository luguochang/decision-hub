from __future__ import annotations

import re
from datetime import datetime

from packages.contracts_py.decision_hub_contracts import AnalystClaim, DocumentBlock
from packages.kernel.decision_hub_kernel.ports.documents import AnalystClaimExtractorPort

_DIRECTION_RE = re.compile(
    r"(看多|看空|偏多|偏空|做多|做空|买入|卖出|上涨|下跌|突破|跌破|震荡|观望|不交易)"
)
_CONDITION_RE = re.compile(r"(如果|若|除非|一旦|在.+情况下|条件)")


class HeuristicAnalystClaimExtractor(AnalystClaimExtractorPort):
    """Offline candidate extractor; all output remains candidate until review."""

    async def extract(
        self,
        *,
        version_id: str,
        corrected_blocks: tuple[DocumentBlock, ...],
        market_scopes: tuple[str, ...],
        instrument_scopes: tuple[str, ...],
        author: str | None,
        published_at: object,
    ) -> tuple[AnalystClaim, ...]:
        claims: list[AnalystClaim] = []
        for index, block in enumerate(corrected_blocks, 1):
            text = block.content.strip()
            direction = _DIRECTION_RE.search(text)
            if not direction:
                continue
            stance = _stance(direction.group(1))
            claim_type = (
                "forecast"
                if any(word in text for word in ("预计", "将", "可能", "目标"))
                else "opinion"
            )
            conditions = [text] if _CONDITION_RE.search(text) else []
            claims.append(
                AnalystClaim(
                    schema_version="analyst-claim.v1",
                    claim_id=f"claim_{version_id.replace('docver_', '')}_{index}",
                    version_id=version_id,
                    claim_type=claim_type,
                    stance=stance,
                    claim_text=text,
                    supporting_quote=text,
                    locator_refs=[block.locator],
                    market_scope=market_scopes[0] if market_scopes else "cross_asset",
                    instrument_scope=list(instrument_scopes),
                    horizon=None,
                    conditions=conditions,
                    invalidation_conditions=[],
                    status="candidate",
                    attribution_confidence="medium" if block.needs_review else "high",
                    requires_independent_evidence=True,
                    created_at=datetime.now().astimezone(),
                    reviewed_at=None,
                )
            )
        return tuple(claims)


def _stance(value: str) -> str:
    if value in {"看多", "偏多", "做多", "买入", "上涨", "突破"}:
        return "bullish"
    if value in {"看空", "偏空", "做空", "卖出", "下跌", "跌破"}:
        return "bearish"
    if value in {"震荡", "观望", "不交易"}:
        return "neutral"
    return "unknown"
