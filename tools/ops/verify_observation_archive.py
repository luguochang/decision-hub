"""Read-only verification of a completed OKX close_1m observation against its archive."""

import argparse
import hashlib
import json
import math
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx


def verify(database_path: Path, api_url: str, outlook_id: str):
    def require(condition, reason):
        if not condition:
            raise ValueError(reason)

    def instant(value):
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result

    db = sqlite3.connect(database_path.resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only = ON')
        with httpx.Client(trust_env=False, timeout=30) as client:
            response = client.get(f'{api_url.rstrip("/")}/v1/analytical-outlooks/{outlook_id}')
            response.raise_for_status()
            detail = response.json()
            outlook = detail['outlook']
            observation = outlook['observation']
            require(observation['status'] == 'completed', 'observation_not_completed')
            require(observation['benchmark'] == 'close_1m', 'unsupported_benchmark')
            stored = db.execute(
                'SELECT * FROM analytical_observations WHERE outlook_id = ?', (outlook_id,)
            ).fetchone()
            require(stored is not None and stored['status'] == 'completed'
                    and stored['attempt'] == observation['attempt'],
                    'stored_observation_missing_or_changed')
            window = json.loads(stored['window_json'])
            attempt = next(row for row in detail['attempts']
                           if row['attempt'] == observation['attempt'])
            require(attempt['status'] == 'completed', 'attempt_not_completed')
            captures = []
            for identity in attempt['raw_capture_ids']:
                capture = db.execute(
                    'SELECT * FROM raw_captures WHERE capture_id = ?', (identity,)
                ).fetchone()
                require(capture is not None and capture['status_code'] == 200,
                        'capture_missing_or_failed')
                body = client.get(f'{api_url.rstrip("/")}/v1/raw-captures/{identity}/body')
                body.raise_for_status()
                require(hashlib.sha256(body.content).hexdigest() == capture['body_hash'],
                        'archive_hash_mismatch')
                url = urlparse(capture['url'])
                require(url.hostname == 'www.okx.com'
                        and url.path == '/api/v5/market/history-candles',
                        'unsupported_archive_provider')
                captures.append((identity, parse_qs(url.query), body.json()))
            prices, verified = [], []
            for side, boundary_key in [('entry', 'window_start_at'), ('exit', 'window_end_at')]:
                quote = window[side]
                boundary = instant(outlook[boundary_key])
                matches = [(identity, query, payload) for identity, query, payload in captures
                           if query.get('after') == [str(int(boundary.timestamp() * 1000))]]
                require(len(matches) == 1, 'candle_capture_ambiguous')
                identity, query, payload = matches[0]
                require(query.get('instId') == [outlook['instrument']]
                        and query.get('bar') == ['1m'], 'candle_instrument_or_interval_mismatch')
                require(str(payload.get('code')) == '0', 'provider_response_failed')
                candle = payload['data'][0]
                require(len(candle) == 9 and str(candle[8]) == '1', 'candle_not_closed')
                close_at = datetime.fromtimestamp(int(candle[0]) / 1000, UTC) + timedelta(minutes=1)
                price = float(candle[4])
                require(math.isfinite(price) and price > 0, 'invalid_price')
                require(boundary <= close_at < boundary + timedelta(minutes=1),
                        'candle_time_mismatch')
                require(close_at == instant(quote['observed_at'])
                        and close_at <= instant(quote['received_at'])
                        and price == quote['last'] and quote['instrument'] == outlook['instrument'],
                        'saved_window_mismatch')
                prices.append(price)
                verified.append({'side': side, 'capture_id': identity, 'price': price,
                                 'observed_at': close_at.isoformat()})
            market_return = (prices[1] / prices[0] - 1) * 100
            require(math.isclose(market_return, observation['market_return_pct'], abs_tol=1e-10)
                    and math.isclose(market_return, stored['market_return_pct'], abs_tol=1e-10),
                    'market_return_mismatch')
            directional = market_return if outlook['action'] == 'long' else (
                -market_return if outlook['action'] == 'short' else None)
            correct = directional > 0 if directional is not None else None
            require(observation['directional_return_pct'] == directional
                    and observation['direction_correct'] == correct
                    and stored['directional_return_pct'] == directional
                    and stored['direction_correct'] == correct,
                    'directional_classification_mismatch')
            require(observation['quality_status'] == 'estimated', 'candle_quality_mismatch')
            return {'status': 'passed', 'outlook_id': outlook_id, 'run_id': outlook['run_id'],
                    'checked_at': datetime.now(UTC).isoformat(), 'action': outlook['action'],
                    'market_return_pct': market_return, 'direction_correct': correct,
                    'quality_status': 'estimated', 'benchmark': 'close_1m',
                    'attempt': observation['attempt'], 'verified_candles': verified,
                    'scope': 'Archived minute-close research observation; not execution P&L.'}
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--api', default='http://127.0.0.1:8047')
    parser.add_argument('--outlook-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.database, args.api, args.outlook_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
