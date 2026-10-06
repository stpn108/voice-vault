"""
Manual live check against the real Plaud account. Run by the owner, never by tests.

When to run: once before setting PLAUD_DELETE_ENABLED=true, and after Plaud
changes its web app. Read-only by default: shows token expiry and, per
recording, the raw processing status. Mutations need an explicit flag and
one recording id, so use a throwaway test recording:

    docker compose run --rm app python plaud_live_check.py
    docker compose run --rm app python plaud_live_check.py --trash <id>
    docker compose run --rm app python plaud_live_check.py --delete <id>

The check answers REQ-001 open questions: does DELETE /file/ work on a
trashed recording only, and which task_status values occur.
"""
import argparse
import sys

from config import load_config
from plaud_client import PlaudClient, PlaudError
from utils import now_utc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--trash", metavar="ID", help="move this recording to the Plaud trash")
    parser.add_argument("--delete", metavar="ID", help="permanently delete this recording")
    args = parser.parse_args(argv)

    cfg = load_config()
    client = PlaudClient(cfg.plaud_token, cfg.plaud_api_base)
    left = client.token_seconds_left(now_utc().timestamp())
    print("token days left:", "unknown" if left is None else f"{left / 86400:.1f}")
    try:
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
