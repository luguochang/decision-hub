"""Deterministic public article extraction; quoted views never become facts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from bs4 import BeautifulSoup

from packages.contracts_py.decision_hub_contracts import AnalystClaim, DocumentBlock
from packages.kernel.decision_hub_kernel.ports.research_signal import PublicationTime
from packages.provider_adapters.research.documents import extract_html_text
from packages.provider_adapters.research.publication_time import html_publication_time

PARSER_REF = "public-opinion-html.verbatim.v1"
_VIEW = re.compile(
    r"\b(?:I|we)\s+(?:anticipate|expect|believe|think|see|forecast)|"
    r"\b(?:could|may|likely|unlikely|expect(?:ed|s)?|forecasts|forecasted|if|unless)\b|"
    r"预计|可能|如果|若|看多|看空|偏多|偏空|上涨|下跌",
    re.IGNORECASE,
)
_FINANCE = re.compile(
    r"\b(?:inflation|monetary|policy|rates?|hikes?|econom\w*|market\w*|"
    r"bitcoin|btc|ethereum|eth|gold|stocks?|yields?|oil|price\w*|dollar|"
    r"liquidity|employment|tariffs?|fund\w*)\b|黄金|比特币|利率|通胀|股市|资金",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PublicOpinionText:
    title: str
    author: str | None
    language: str | None
    publication: PublicationTime
    text: str


def parse_public_opinion(data: bytes, url: str) -> PublicOpinionText:
    html = data.decode("utf-8", errors="strict")
    soup = BeautifulSoup(html, "html.parser")
    text = extract_html_text(html)
    if not text or "\x00" in text or "\ufffd" in text:
        raise ValueError("public_opinion_readable_original_required")
    authors = {
        str(node.get("content") or node.get_text(" ", strip=True)).strip()
        for node in soup.select('meta[name="author"], [itemprop="author"]')
        if node.get("content") or node.get_text(" ", strip=True)
    }
    if not authors:
        authors = {
            node.get_text(" ", strip=True)
            for node in soup.select(".speaker, .article__speaker, .author, .byline")
            if node.get_text(" ", strip=True)
        }
    author = next(iter(authors)) if len(authors) == 1 else None
    title_node = soup.select_one('meta[property="og:title"]') or soup.select_one("title, h1")
    title = (
        str(title_node.get("content") or title_node.get_text(" ", strip=True))
        if title_node
        else url
    )
    published = html_publication_time(html, url)
    display = soup.select_one(".article__time, time, [itemprop=datePublished]")
    raw_date = (
        str(display.get("datetime") or display.get_text(" ", strip=True)) if display else None
    )
    publication = PublicationTime(
        precision="exact" if published is not None else "unknown",
        published_at=published,
        window_start=None,
        window_end=None,
        display_text=raw_date[:256] if raw_date else None,
        source_timezone="explicit_source_offset" if published is not None else None,
    )
    language = str(soup.html.get("lang")) if soup.html and soup.html.get("lang") else None
    return PublicOpinionText(
        title[:512], author[:256] if author else None, language, publication, text
    )


def exact_opinion_claims(
    *,
    version_id: str,
    blocks: tuple[DocumentBlock, ...],
    research_family: str,
    instrument_scopes: tuple[str, ...],
    created_at: datetime,
) -> tuple[AnalystClaim, ...]:
    """Preserve the whole paragraph and its conditions; do not invent direction.

    Oversize paragraphs remain in the document and are not silently truncated
    into a claim. The import receipt reports the full parsed and selected counts.
    """
    output = []
    for index, block in enumerate(blocks, 1):
        text = block.content
        if not _VIEW.search(text) or not _FINANCE.search(text) or len(text) > 3200:
            continue
        conditional = bool(re.search(r"\bif\b|\bunless\b|如果|若", text, re.IGNORECASE))
        output.append(
            AnalystClaim(
                schema_version="analyst-claim.v1",
                claim_id=f"claim_{version_id.removeprefix('docver_')}_{index}",
                version_id=version_id,
                claim_type="scenario" if conditional else "opinion",
                stance="unknown",
                claim_text=text,
                supporting_quote=text,
                locator_refs=[block.locator],
                market_scope=cast(
                    Literal["crypto_macro", "cn_equity", "cross_asset"], research_family
                ),
                instrument_scope=list(instrument_scopes),
                horizon=None,
                conditions=[text] if conditional and len(text) <= 1000 else [],
                invalidation_conditions=[],
                status="candidate",
                attribution_confidence="medium",
                requires_independent_evidence=True,
                created_at=created_at,
                reviewed_at=None,
            )
        )
    return tuple(output)
