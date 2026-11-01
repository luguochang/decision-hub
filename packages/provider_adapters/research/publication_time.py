"""Conservative document publication instants; fetch/modify dates are not publication."""

import re
from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import urlsplit

from bs4 import BeautifulSoup


def html_publication_time(html: str, url: str) -> datetime | None:
    soup = BeautifulSoup(html, "html.parser")
    values = []
    for node in soup.select('meta[property="article:published_time"], meta[name="datePublished"], '
                            'meta[itemprop="datePublished"], time[itemprop="datePublished"]'):
        value = node.get("content") or node.get("datetime")
        if value:
            values.append(str(value))
    # Only explicit publication metadata with an offset is an instant.
    # Date-only and naive times remain unknown; never invent midnight/UTC.
    instants = set()
    for value in values:
        try:
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if moment.tzinfo is None:
            return None
        instants.add(moment.astimezone(UTC))
    if len(instants) > 1:
        return None
    if instants:
        return next(iter(instants))
    parsed = urlsplit(url)
    if (parsed.hostname not in {"www.federalreserve.gov", "federalreserve.gov"}
            or not parsed.path.startswith("/newsevents/pressreleases/")):
        return None
    dates = soup.select(".article__time")
    releases = re.findall(
        r"For release at\s+(\d{1,2}):(\d{2})\s*([ap])\.?m\.?\s+(EDT|EST)\b",
        soup.get_text(" ", strip=True), re.IGNORECASE,
    )
    if len(dates) != 1 or len(releases) != 1:
        return None
    try:
        day = datetime.strptime(dates[0].get_text(strip=True), "%B %d, %Y")
        hour, minute, period, zone = releases[0]
        if not 1 <= int(hour) <= 12 or not 0 <= int(minute) <= 59:
            return None
        return day.replace(
            hour=int(hour) % 12 + (12 if period.lower() == "p" else 0), minute=int(minute),
            tzinfo=timezone(timedelta(hours=-4 if zone.upper() == "EDT" else -5)),
        ).astimezone(UTC)
    except ValueError:
        return None
