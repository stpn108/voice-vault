"""Import (REQ-001 criteria 1, 2, 5): store, verify by read-back, re-import on change."""
import datetime as dt

import pytest
from sqlalchemy import select

import import_service
from database import Recording, Segment
from import_service import (
    ImportStats, compute_content_hash, normalize_segments, run_import,
)
from plaud_client import PlaudAuthError, PlaudDetail, PlaudError, PlaudRecording

NOW = dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.timezone.utc)
START_MS = 1_760_000_000_000  # 2025-10-09T08:53:20Z


def raw_seg(speaker, text, start, end):
    return {"speaker": speaker, "content": text, "start_time": start, "end_time": end}


class FakeClient:
    def __init__(self):
        self.recordings = {}  # id -> dict(processed, segments, summary, title)
        self.fail_on = {}
        self.duration_ms = 60_000

    def add(self, plaud_id="r1", processed=True, segments=None, summary="## Summary", title="Team Sync"):
        if segments is None:
            segments = [raw_seg(f"Speaker {i % 2}", f"text {i}", i * 1000, i * 1000 + 900) for i in range(42)]
        self.recordings[plaud_id] = dict(processed=processed, segments=segments, summary=summary, title=title)

    def list_recordings(self):
        return [PlaudRecording(i, r["title"], START_MS, self.duration_ms) for i, r in self.recordings.items()]

    def get_detail(self, plaud_id):
        if plaud_id in self.fail_on:
            raise self.fail_on[plaud_id]
        r = self.recordings[plaud_id]
        return PlaudDetail(plaud_id, r["title"], START_MS, self.duration_ms, r["processed"])

    def fetch_segments(self, detail):
        return self.recordings[detail.plaud_id]["segments"]

    def fetch_summary(self, detail):
        return self.recordings[detail.plaud_id]["summary"]


def test_req_001_import_stores_recording_and_all_segments(session_factory):
    client = FakeClient()
    client.add()
    stats = run_import(session_factory, client, lambda: NOW)

    with session_factory() as s:
        rec = s.scalar(select(Recording))
        segs = s.scalars(select(Segment).order_by(Segment.idx)).all()
        assert (rec.plaud_id, rec.title, rec.duration_ms) == ("r1", "Team Sync", 60_000)
        assert rec.summary == "## Summary" and rec.segment_count == 42 and len(segs) == 42
        assert (segs[3].speaker, segs[3].start_ms, segs[3].end_ms, segs[3].text) == ("Speaker 1", 3000, 3900, "text 3")
        assert rec.ended_at.replace(tzinfo=dt.timezone.utc) - rec.started_at.replace(tzinfo=dt.timezone.utc) == dt.timedelta(minutes=1)
    assert (stats.imported, stats.verified) == (1, 1)


def test_import_marks_row_verified_with_matching_hash(session_factory):
    client = FakeClient()
    client.add()
    run_import(session_factory, client, lambda: NOW)
    with session_factory() as s:
        rec = s.scalar(select(Recording))
        assert rec.verified_hash == rec.content_hash and rec.verified_at is not None


def test_unprocessed_recording_is_not_imported(session_factory):
    client = FakeClient()
    client.add(processed=False)
    stats = run_import(session_factory, client, lambda: NOW)
    with session_factory() as s:
        assert s.scalar(select(Recording)) is None
    assert stats.skipped == 1


@pytest.mark.parametrize("segments,summary", [([], "## S"), (None, "")])
def test_empty_transcript_or_summary_is_not_imported(session_factory, segments, summary):
    # Doubt means no import and thus no deletion (D-003).
    client = FakeClient()
    client.add(segments=segments, summary=summary)
    run_import(session_factory, client, lambda: NOW)
    with session_factory() as s:
        assert s.scalar(select(Recording)) is None


def test_second_run_without_change_keeps_state(session_factory):
    client = FakeClient()
    client.add()
    run_import(session_factory, client, lambda: NOW)
    later = NOW + dt.timedelta(minutes=10)
    stats = run_import(session_factory, client, lambda: later)
    with session_factory() as s:
        rec = s.scalar(select(Recording))
        assert rec.last_changed_at.replace(tzinfo=dt.timezone.utc) == NOW
        assert s.query(Segment).count() == 42
    assert (stats.unchanged, stats.imported, stats.updated) == (1, 0, 0)


def test_req_001_changed_summary_is_reimported_and_unverified_until_checked(session_factory):
    client = FakeClient()
    client.add()
    run_import(session_factory, client, lambda: NOW)
    with session_factory() as s:
        old_hash = s.scalar(select(Recording)).content_hash

    client.recordings["r1"]["summary"] = "## Summary\n- new action item"
    later = NOW + dt.timedelta(minutes=10)
    stats = run_import(session_factory, client, lambda: later)

    with session_factory() as s:
        rec = s.scalar(select(Recording))
        assert rec.summary.endswith("new action item")
        assert rec.content_hash != old_hash
        assert rec.last_changed_at.replace(tzinfo=dt.timezone.utc) == later
        assert rec.verified_hash == rec.content_hash  # re-verified against the new content
        assert s.query(Segment).count() == 42  # replaced, not duplicated
    assert stats.updated == 1


def test_changed_transcript_replaces_segments(session_factory):
    client = FakeClient()
    client.add()
    run_import(session_factory, client, lambda: NOW)
    client.recordings["r1"]["segments"] = [raw_seg("A", "only one", 0, 10)]
    run_import(session_factory, client, lambda: NOW + dt.timedelta(minutes=10))
    with session_factory() as s:
        assert s.scalar(select(Recording)).segment_count == 1
        assert s.query(Segment).count() == 1


def test_title_change_alone_does_not_reset_stability(session_factory):
    client = FakeClient()
    client.add()
    run_import(session_factory, client, lambda: NOW)
    client.recordings["r1"]["title"] = "Renamed"
    run_import(session_factory, client, lambda: NOW + dt.timedelta(minutes=10))
    with session_factory() as s:
        rec = s.scalar(select(Recording))
        assert rec.title == "Renamed"
        assert rec.last_changed_at.replace(tzinfo=dt.timezone.utc) == NOW


def test_recording_that_becomes_unprocessed_is_flagged(session_factory):
    client = FakeClient()
    client.add()
    run_import(session_factory, client, lambda: NOW)
    client.recordings["r1"]["processed"] = False
    run_import(session_factory, client, lambda: NOW + dt.timedelta(minutes=10))
    with session_factory() as s:
        assert s.scalar(select(Recording)).is_plaud_processed is False


def test_verification_failure_leaves_row_unverified(session_factory, monkeypatch):
    client = FakeClient()
    client.add()
    # Simulate a read-back that does not match what the API sent.
    monkeypatch.setattr(import_service, "compute_content_hash",
                        _hash_that_differs_on_second_call(import_service.compute_content_hash))
    stats = run_import(session_factory, client, lambda: NOW)
    with session_factory() as s:
        rec = s.scalar(select(Recording))
        assert rec.verified_at is None and rec.verified_hash is None
    assert stats.verified == 0


def _hash_that_differs_on_second_call(real):
    calls = []

    def fake(summary, segments):
        calls.append(1)
        value = real(summary, segments)
        return value if len(calls) == 1 else "0" * 64
    return fake


def test_one_failing_recording_does_not_stop_the_others(session_factory):
    client = FakeClient()
    client.add("bad")
    client.add("good")
    client.fail_on["bad"] = PlaudError("boom")
    stats = run_import(session_factory, client, lambda: NOW)
    with session_factory() as s:
        assert [r.plaud_id for r in s.scalars(select(Recording))] == ["good"]
    assert (stats.failed, stats.imported) == (1, 1)


def test_auth_error_aborts_the_run(session_factory):
    client = FakeClient()
    client.add("a")
    client.fail_on["a"] = PlaudAuthError("401")
    with pytest.raises(PlaudAuthError):
        run_import(session_factory, client, lambda: NOW)


def test_hash_depends_on_summary_and_segments_only():
    segs = normalize_segments([raw_seg("A", "x", 0, 1)])
    assert compute_content_hash("s", segs) == compute_content_hash("s", list(segs))
    assert compute_content_hash("s", segs) != compute_content_hash("s2", segs)
    assert compute_content_hash("s", segs) != compute_content_hash("s", normalize_segments([raw_seg("B", "x", 0, 1)]))


def test_normalize_segments_falls_back_to_original_speaker_and_tolerates_missing_times():
    out = normalize_segments([{"original_speaker": "Speaker 1", "content": "hi"}])
    assert out == [{"speaker": "Speaker 1", "start_ms": None, "end_ms": None, "text": "hi"}]


def test_corrected_duration_is_picked_up_without_restarting_the_stability_window(session_factory):
    # D-006: a recording listed with duration 0 gets its real length later, content unchanged.
    client = FakeClient()
    client.add()
    client.duration_ms = 0
    run_import(session_factory, client, lambda: NOW)
    client.duration_ms = 60_000
    run_import(session_factory, client, lambda: NOW + dt.timedelta(minutes=10))
    with session_factory() as s:
        rec = s.scalar(select(Recording))
        assert rec.duration_ms == 60_000
        assert rec.ended_at.replace(tzinfo=dt.timezone.utc) - rec.started_at.replace(tzinfo=dt.timezone.utc) == dt.timedelta(minutes=1)
        assert rec.last_changed_at.replace(tzinfo=dt.timezone.utc) == NOW


def test_req_002_discarded_recording_is_never_fetched_or_stored_again(session_factory):
    from database import Recording as Rec
    client = FakeClient()
    client.add("p1")
    run_import(session_factory, client, lambda: NOW)
    with session_factory() as s:
        row = s.scalar(select(Rec))
        row.title, row.summary, row.segment_count, row.discarded_at = "", "", 0, NOW
        s.query(Segment).delete()
        s.commit()
    client.fail_on["p1"] = AssertionError("a discarded recording must not be fetched")
    client.recordings["p1"]["summary"] = "changed at Plaud afterwards"
    stats = run_import(session_factory, client, lambda: NOW + dt.timedelta(minutes=10))
    with session_factory() as s:
        row = s.scalar(select(Rec))
        assert row.summary == "" and s.query(Segment).count() == 0
    assert stats.skipped == 1 and stats.imported == 0
