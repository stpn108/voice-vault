"""
Manual live check against the real Plaud account. Run by the owner, never by tests.

When to run: once before setting PLAUD_DELETE_ENABLED=true, and after Plaud
changes its web app. Read-only by default: shows token expiry and, per
recording, the raw processing status. Mutations need an explicit flag and
one recording id, so use a throwaway test recording:

    docker compose run --rm app python plaud_live_check.py
    docker compose run --rm app python plaud_live_check.py --trash <id>
    docker compose run --rm app python plaud_live_check.py --delete <id>
    docker compose run --rm app python plaud_live_check.py --speakers <id>

`--speakers` changes nothing. It shows where Plaud keeps the speaker names of one recording: the
labels in the cleaned and in the original transcript, and every field of the detail whose name
mentions a speaker. It prints names and keys, never transcript text.

It also exercises the automatic token renewal (REQ-004) and stores the
rotated tokens in the database, exactly as the app does.

The check answers REQ-001 open questions: does the permanent delete work on a
trashed recording only, and which task_status values occur. Since Plaud 4.0
(D-080) it is also the first check of the new endpoints.
"""
import argparse
import json
import re
import sys
from collections import Counter

from sqlalchemy.orm import Session

from config import load_config
from database import engine
from plaud_client import POLISHED_TYPE, TRANSCRIPT_TYPE, PlaudError, _object, _segments, build_client
from utils import now_utc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--trash", metavar="ID", help="move this recording to the Plaud trash")
    parser.add_argument("--delete", metavar="ID", help="permanently delete this recording")
    parser.add_argument("--speakers", metavar="ID", help="show where the speaker names of this recording live")
    args = parser.parse_args(argv)

    cfg = load_config()
    client = build_client(cfg, lambda: Session(engine))
    try:
        client.ensure_fresh_token(now_utc().timestamp())
        left = client.credential_seconds_left(now_utc().timestamp())
        print("credential days left:", "unknown" if left is None else f"{left / 86400:.1f}")
        if args.speakers:
            show_speakers(client, args.speakers)
            return 0
        if args.trash:
            client.trash([args.trash])
            print("trashed", args.trash)
        if args.delete:
            client.delete_permanently([args.delete])
            print("deleted permanently", args.delete)
        for item in client.list_recordings():
            detail = client.get_detail(item.plaud_id)
            statuses = {
                o.get("object_type"): o.get("task_status") for o in detail.raw.get("objects") or []
            }
            print(item.plaud_id, repr(detail.title), "processed" if detail.is_processed else "not processed", statuses)
    except PlaudError as exc:
        print("Plaud error:", exc, file=sys.stderr)
        return 1
    return 0


SPEAKERISH = re.compile(r"speaker|voice|embedding|participant", re.IGNORECASE)


def _speaker_fields(obj, path="", depth=0, out=None):
    """(path, short value) for every key that mentions a speaker, anywhere in a JSON structure."""
    out = [] if out is None else out
    if depth > 6 or len(out) > 40:
        return out
    if isinstance(obj, dict):
        for key, value in obj.items():
            here = f"{path}.{key}" if path else str(key)
            if SPEAKERISH.search(str(key)) and key not in ("speaker", "original_speaker"):
                out.append((here, json.dumps(value, ensure_ascii=False)[:200]))
            if key not in ("trans_result", "content", "data_content"):
                _speaker_fields(value, here, depth + 1, out)
    elif isinstance(obj, list):
        for i, value in enumerate(obj[:5]):
            _speaker_fields(value, f"{path}[{i}]", depth + 1, out)
    return out


def _labels(segments) -> str:
    pairs = Counter((s.get("speaker"), s.get("original_speaker")) for s in segments if isinstance(s, dict))
    return "; ".join(f"speaker={sp!r} original={orig!r} x{n}" for (sp, orig), n in pairs.most_common()) or "(none)"


def show_speakers(client, plaud_id: str) -> None:
    detail = client.get_detail(plaud_id)
    raw = detail.raw
    print("detail keys:", sorted(raw))
    print("objects:", [(o.get("object_type"), o.get("task_status"), bool(o.get("content_url")))
                       for o in raw.get("objects") or [] if isinstance(o, dict)])
    for where, value in _speaker_fields(raw):
        print("detail field:", where, "=", value)
    for object_type in (POLISHED_TYPE, TRANSCRIPT_TYPE):
        item = _object(raw, object_type)
        if not item or not item.get("content_url"):
            print(object_type, "labels: (no download link)")
            continue
        print(object_type, "labels:", _labels(_segments(client._download(str(item["content_url"])))))
    print("used by the import:", _labels(client.fetch_segments(detail)))


if __name__ == "__main__":
    sys.exit(main())
