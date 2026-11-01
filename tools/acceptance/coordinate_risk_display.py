"""Finite isolated UI probe coordinator; never writes display/ack receipts."""
from __future__ import annotations

import argparse
import json
import socket
import time

from tools.acceptance import probe_risk_display as probe


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=probe.Path, required=True)
    parser.add_argument('--output', type=probe.Path, required=True)
    parser.add_argument('--source-plan', type=probe.Path, required=True)
    args = parser.parse_args()
    output, _ = probe.guard(args)
    if list(output.glob('trigger-*.json')):
        raise ValueError('new_finite_probe_namespace_required')
    class NoNetworkSocket(socket.socket):
        def connect(self, *_args, **_kwargs):
            raise RuntimeError('probe_network_forbidden_browser_only')
        connect_ex = connect
    socket.socket = NoNetworkSocket
    for index in range(1, 31):
        probe.guard(args)
        trigger = probe.inject(args, output)
        print(json.dumps({'index': index, 'alert_id': trigger['alert_id']}), flush=True)
        args.index = index
        args.witness = output / f'witness-{index:02d}.json'
        deadline = time.monotonic() + 90
        while not args.witness.is_file() and time.monotonic() < deadline:
            time.sleep(.05)
        if not args.witness.is_file():
            args.witness = None
            probe.observe(args, output)
            break
        observed = probe.observe(args, output)
        print(json.dumps({'observed': index, 'qualified': observed['qualified']}), flush=True)
        probe.guard(args)
    receipt = probe.summary(output)
    print(json.dumps({k: v for k, v in receipt.items() if k != 'rows'}), flush=True)


if __name__ == '__main__':
    main()
