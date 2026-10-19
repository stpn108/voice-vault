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
labels in the current transcript (/file/list), in the file behind the download link, and every
field whose name mentions a speaker. It prints names and keys, never transcript text.

It also exercises the automatic token renewal (REQ-004) and stores the
rotated tokens in the database, exactly as the app does.

The check answers REQ-001 open questions: does DELETE /file/ work on a
trashed recording only, and which task_status values occur.
"""
import argparse
import json
import re
import sys
from collections import Counter

from sqlalchemy.orm import Session

from config import load_config
from database import engine
from plaud_client import PlaudError, _segments, build_client
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
                c.get("data_type"): c.get("task_status") for c in detail.raw.get("content_list") or []
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
    print("content_list:", [(c.get("data_type"), c.get("task_status")) for c in raw.get("content_list") or []])
    print("pre_download_content_list:", [(p.get("data_type"), "speaker" in str(p.get("data_content", "")))
                                         for p in raw.get("pre_download_content_list") or []])
    for where, value in _speaker_fields(raw):
        print("detail field:", where, "=", value)
    try:
        payload = client._request("POST", "/file/list", body=[plaud_id])
        files = payload.get("data_file_list") or []
        print("/file/list entries:", len(files))
        for entry in files:
            if isinstance(entry, dict):
                print("  keys:", sorted(entry))
                print("  trans_result labels:", _labels(entry.get("trans_result") or []))
                print("  transaction_polish labels:", _labels(_segments(entry.get("transaction_polish"))))
                for where, value in _speaker_fields(entry):
                    print("  field:", where, "=", value)
    except PlaudError as exc:
        print("/file/list failed:", exc)
    link = next((c.get("data_link") for c in raw.get("content_list") or [] if c.get("data_type") == "transaction"), None)
    if link:
        body = client._download(str(link))
        segments = body.get("trans_result") if isinstance(body, dict) else body
        print("download link labels:", _labels(segments if isinstance(segments, list) else []))
    print("used by the import:", _labels(client.fetch_segments(detail)))


if __name__ == "__main__":
    sys.exit(main())
