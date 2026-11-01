"""Independent bounded callback lane; never waits behind the research tick."""

from __future__ import annotations

import asyncio
import json
import os
import re
from threading import Event, Lock
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError


class CallbackLeaseWatch:
    """Continue reversible RO only under the last actually confirmed boundary."""

    def __init__(self, jobs, lease):
        self.jobs, self.lease = jobs, lease
        self.lock = Lock()
        self.last = lease.initial_observation
        self.lost_reason = None
        if self.last is None:
            # Older internal tokens are usable only after a fresh authenticated RO.
            self.last = jobs.observe(lease)

    def _log(self, status, code, error=None):
        print(
            json.dumps(
                dict(
                    role="research_skill_callbacks",
                    status=status,
                    job_id=self.lease.job_id,
                    error_code=code,
                    error_type=type(error).__name__ if error else None,
                    last_confirmed_until=self.last.valid_until.isoformat(),
                )
            )
        )

    def _lose(self, code, status="confirmed_lost"):
        with self.lock:
            if self.lost_reason is None:
                self.lost_reason = code
                self._log(status, code)

    def check(self):
        with self.lock:
            if self.lost_reason is None and self.jobs.clock() >= self.last.valid_until:
                self.lost_reason = "skill_callback_last_confirmed_expired"
                self._log("last_confirmed_expired", self.lost_reason)
            if self.lost_reason is not None:
                raise ValueError("skill_callback_lease_lost")

    def remaining(self):
        self.check()
        with self.lock:
            return max(0, (self.last.valid_until - self.jobs.clock()).total_seconds())

    def _confirm(self, observation):
        with self.lock:
            # A confirmed loss never resurrects this attempt, even if time changes.
            if self.lost_reason is None:
                self.last = observation
        self.check()

    def renew_once(self):
        self.check()
        try:
            self._confirm(self.jobs.renew(self.lease))
            return True
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            self._log("renew_unconfirmed", "skill_callback_renew_unconfirmed", error)
            # A short write lock can leave RO available. This only authenticates
            # existing rights, never writes/extends the old lease or Run deadline.
            try:
                observation = self.jobs.observe(self.lease)
            except (SQLAlchemyError, OSError, TimeoutError) as observation_error:
                self._log(
                    "lease_read_unconfirmed", "skill_callback_renew_unconfirmed", observation_error
                )
                self.check()
            except (ValueError, PermissionError) as fence_error:
                self._lose(str(fence_error))
                self.check()
            else:
                self._confirm(observation)
            return False
        except (ValueError, PermissionError) as fence_error:
            self._lose(str(fence_error))
            self.check()


class ResearchSkillCallbackWorker:
    def __init__(self, service, admission):
        self.service, self.admission = service, admission
        self.owner = f"skill-callback:{os.getpid()}:{uuid4().hex}"
        self.stop = Event()

    def request_stop(self):
        self.stop.set()

    async def process_one(self):
        if not await asyncio.to_thread(self.service.jobs.has_pending):
            return False

        def claim_after_source_verification():
            # Evaluate real CPU/file work in the admitted native thread,
            # before the repository enters BEGIN; never on the event loop.
            producer = self.service.producer_identity()
            return self.service.jobs.claim(self.owner, producer)

        lease = await self.admission.run_thread(claim_after_source_verification)
        if lease is None:
            return False
        watch = CallbackLeaseWatch(self.service.jobs, lease)
        finished = asyncio.Event()

        def check():
            if self.stop.is_set():
                raise ValueError("skill_callback_worker_stopping")
            watch.check()

        async def renew():
            retry_unconfirmed = False
            while not finished.is_set():
                try:
                    interval = 1 if retry_unconfirmed else 20
                    remaining = watch.remaining()
                    delay = min(interval, remaining, max(0.05, remaining / 2))
                    await asyncio.wait_for(finished.wait(), timeout=delay)
                except TimeoutError:
                    try:
                        retry_unconfirmed = not await asyncio.to_thread(watch.renew_once)
                    except ValueError:
                        return
                except ValueError:
                    return

        renew_task = asyncio.create_task(renew(), name="skill-callback-lease")
        try:
            await self.admission.run_thread(
                self.service.process_claimed,
                lease,
                execution_check=check,
            )
        except (ValueError, PermissionError, KeyError, TypeError) as error:
            # No second attempt runs here. Saved requests are freshly authenticated
            # by a later claim. A lost writer cannot alter another owner's job.
            code = str(error)
            if code != "legacy_skill_unversioned" and not re.fullmatch(r"skill_[a-z_]{1,100}(?::[a-zA-Z0-9_.:]*)?", code):
                code = "skill_original_verification_failed"
            try:
                if code == "skill_callback_worker_stopping":
                    await asyncio.to_thread(self.service.jobs.requeue, lease, code)
                elif code != "skill_callback_lease_lost":
                    await asyncio.to_thread(self.service.jobs.reject, lease, code[:128])
            except ValueError as fence_error:
                if str(fence_error) != "skill_callback_lease_lost":
                    raise
        finally:
            finished.set()
            await renew_task
        return True

    async def run_forever(self, interval):
        while not self.stop.is_set():
            try:
                await self.process_one()
            except (SQLAlchemyError, OSError, ValueError) as error:
                # Storage/identity observation failure is not job failure or
                # permission to submit. Preserve uncertain state and the lane;
                # expired attempts are freshly claimed/re-authenticated later.
                print(
                    json.dumps(
                        {
                            "role": "research_skill_callbacks",
                            "status": "observation_unconfirmed",
                            "error_code": "skill_callback_lane_unconfirmed",
                            "error_type": type(error).__name__,
                        }
                    )
                )
            if not self.stop.is_set():
                await asyncio.sleep(interval)
