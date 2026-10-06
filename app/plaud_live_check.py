"""
Manual live check against the real Plaud account. Run by the owner, never by tests.

When to run: once before setting PLAUD_DELETE_ENABLED=true, and after Plaud
changes its web app. Read-only by default: shows token expiry and, per
recording, the raw processing status. Mutations need an explicit flag and
one recording id, so use a throwaway test recording:

    docker compose run --rm app python plaud_live_check.py
    docker compose run --rm app python plaud_live_check.py --trash <id>
    docker compose run --rm app python plaud_live_check.py --delete <id>

It also exercises the automatic token renewal (REQ-004) and stores the
rotated tokens in the database, exactly as the app does.

The check answers REQ-001 open questions: does DELETE /file/ work on a
trashed recording only, and which task_status values occur.
"""
import argparse
import sys

from sqlalchemy.orm import Session

from config import load_config
from database import engine
from plaud_client import PlaudError, build_client
from utils import now_utc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--trash", metavar="ID", help="move this recording to the Plaud trash")
    parser.add_argument("--delete", metavar="ID", help="permanently delete this recording")
    args = parser.parse_args(argv)

    cfg = load_config()
    client = build_client(cfg, lambda: Session(engine))
    try:
        client.ensure_fresh_token(now_utc().timestamp())
        left = client.credential_seconds_left(now_utc().timestamp())
        print("credential days left:", "unknown" if left is None else f"{left / 86400:.1f}")
        if args.trash:
            client.trash([args.trash])
            print("trashed", args.trash)
        if args.delete:
            client.delete_permanently([args.delete])
            print("deleted permanently", args.delete)
        for item in client.list_recordings():
            detail = client.get_detail(item.plaud_id)
            statuses = {
                c.get("data_type"): c.get("task_status") for c in detail.raw.get("content_list") or []
            }
            print(item.plaud_id, repr(detail.title), "processed" if detail.is_processed else "not processed", statuses)
    except PlaudError as exc:
        print("Plaud error:", exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
