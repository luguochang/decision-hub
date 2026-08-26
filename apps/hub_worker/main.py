from __future__ import annotations

import argparse
import time

from packages.kernel.decision_hub_kernel.application.outbox import OutboxService
from packages.kernel.decision_hub_kernel.persistence.db import Database


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="drain the local outbox once")
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()
    database = Database()
    database.initialize()
    outbox = OutboxService(database)
    while True:
        drained = outbox.drain_local()
        if args.once:
            print(f"hub-worker drained {drained} local notifications")
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    run()
