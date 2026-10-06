"""
voice-vault entrypoint: migrate the schema, then run the import/delete cycle
on a schedule (REQ-001).
"""
import os
import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy.orm import Session

from config import load_config
from database import engine, migrate_schema
from plaud_client import PlaudClient
from sync_job import SyncJob
from utils import LOCAL_TZ, now_utc, setup_logging

setup_logging()
log = logging.getLogger(__name__)


def build_scheduler(job: SyncJob, interval_minutes: int) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=LOCAL_TZ)
    # max_instances=1: a cycle still running when the next one is due is skipped.
    scheduler.add_job(
        job.run, "interval", minutes=interval_minutes, id="plaud_sync",
        max_instances=1, coalesce=True, next_run_time=now_utc(),
    )
    return scheduler


def main():
    migrate_schema()
    cfg = load_config()
    log.info(
        "voice-vault v%s (%s) started interval_min=%d delete_enabled=%s",
        os.getenv("APP_VERSION", "0.0"), os.getenv("GIT_COMMIT", "unknown"),
        cfg.import_interval_minutes, cfg.delete_enabled,
    )
    if not cfg.delete_enabled:
        log.warning("Shadow mode: nothing is deleted at Plaud (PLAUD_DELETE_ENABLED=false)")
    client = PlaudClient(cfg.plaud_token, cfg.plaud_api_base)
    job = SyncJob(cfg, client, lambda: Session(engine))
    build_scheduler(job, cfg.import_interval_minutes).start()


if __name__ == "__main__":
    main()
