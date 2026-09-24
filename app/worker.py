import logging
import time
from datetime import timedelta

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Job, Peer, Subscription, utc_now
from app.services import deliver_webhooks, expire_due, poll_usage, process_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wg-worker")


def run_once() -> None:
    with SessionLocal() as db:
        for stale in db.scalars(select(Job).where(Job.status == "running", Job.run_after <= utc_now())).all():
            stale.status = "retry"
            stale.run_after = utc_now()
        db.commit()
        job = db.scalar(select(Job).where(Job.status.in_(["queued", "retry"]),
                                           Job.run_after <= utc_now()).order_by(Job.created_at)
                        .with_for_update(skip_locked=True).limit(1))
        if job:
            job.status = "running"
            job.attempts += 1
            job.run_after = utc_now() + timedelta(minutes=3)
            db.commit()
            try:
                process_job(db, job)
            except Exception as exc:
                db.rollback()
                job = db.get(Job, job.id)
                if job:
                    job.last_error = str(exc)[:2000]
                    if job.attempts >= 8:
                        job.status = "failed"
                        peer = db.get(Peer, job.resource_id) if job.kind == "provision_peer" else None
                        if peer:
                            peer.status = "failed"
                            sub = db.get(Subscription, peer.subscription_id)
                            if sub:
                                sub.status = "failed"
                                from app.services import emit
                                emit(db, sub.tenant_id, "subscription.failed", {"subscription_id": sub.id})
                                for active_peer in db.scalars(select(Peer).where(
                                        Peer.subscription_id == sub.id,
                                        Peer.status.in_(["active", "provisioning", "recreating", "failed"]))).all():
                                    active_peer.status = "revoking"
                                    db.add(Job(tenant_id=sub.tenant_id, kind="revoke_peer",
                                               resource_id=active_peer.id))
                    else:
                        job.status = "retry"
                        job.run_after = utc_now() + timedelta(seconds=min(1800, 2 ** job.attempts * 3))
                    db.commit()
                log.exception("Job execution failed")
        expire_due(db)
        deliver_webhooks(db)


def main() -> None:
    last_usage_poll = 0.0
    while True:
        try:
            run_once()
            now = time.monotonic()
            if now - last_usage_poll >= 30:
                with SessionLocal() as db:
                    poll_usage(db)
                last_usage_poll = now
        except Exception:
            log.exception("Worker loop failed")
        time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
