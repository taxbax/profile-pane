"""Tests for panecore.trial — apply for real, then undo it.

The claim under test: a revert restores every byte the trial touched, and a trial is
single-use so a stale Revert button cannot scramble a tree that has moved on.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))

from panecore import trial as T   # noqa: E402


def test_record_and_revert_a_modified_file(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "TRIALS", tmp_path / "trials")
    f = tmp_path / "SOUL.md"
    f.write_text("ORIGINAL")

    tid = T.begin("test")
    snap = T.backup_file(tid, f, 0)
    T.record(tid, {"kind": "file", "path": str(f), "existed": True, "snapshot": snap})

    f.write_text("CHANGED")                       # the trial's write
    assert f.read_text() == "CHANGED"

    r = T.revert(tid)
    assert r["ok"] and r["count"] == 1
    assert f.read_text() == "ORIGINAL"            # byte-identical restore


def test_revert_deletes_a_file_the_trial_created(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "TRIALS", tmp_path / "trials")
    f = tmp_path / "USER.md"

    tid = T.begin("test")
    T.record(tid, {"kind": "file", "path": str(f), "existed": False, "snapshot": None})
    f.write_text("created by the trial")

    r = T.revert(tid)
    assert r["ok"] and not f.exists()


def test_revert_restores_a_directory_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "TRIALS", tmp_path / "trials")
    d = tmp_path / "skills" / "thing"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("v1")

    tid = T.begin("test")
    snap = T.backup_dir(tid, d, 0)
    T.record(tid, {"kind": "dir", "path": str(d), "existed": True, "snapshot": snap})

    (d / "SKILL.md").write_text("v2")             # the copy lands
    (d / "extra.md").write_text("new file")       # and adds one

    r = T.revert(tid)
    assert r["ok"]
    assert (d / "SKILL.md").read_text() == "v1"
    assert not (d / "extra.md").exists()          # the whole tree came back


def test_revert_removes_a_directory_the_trial_created(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "TRIALS", tmp_path / "trials")
    d = tmp_path / "skills" / "brand-new"

    tid = T.begin("test")
    T.record(tid, {"kind": "dir", "path": str(d), "existed": False, "snapshot": None})
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("x")

    r = T.revert(tid)
    assert r["ok"] and not d.exists()


def test_revert_runs_in_reverse_order(tmp_path, monkeypatch):
    """Overlapping writes unwind correctly only if the undo is LIFO."""
    monkeypatch.setattr(T, "TRIALS", tmp_path / "trials")
    f = tmp_path / "n.md"
    f.write_text("gen0")

    tid = T.begin("test")
    for i, gen in enumerate(("gen1", "gen2")):
        snap = T.backup_file(tid, f, i)
        T.record(tid, {"kind": "file", "path": str(f), "existed": True, "snapshot": snap})
        f.write_text(gen)

    T.revert(tid)
    assert f.read_text() == "gen0"


def test_backups_cannot_collide_even_on_the_same_index(tmp_path, monkeypatch):
    """Two ops that pass the SAME index must not share a blob — otherwise the second
    restore returns the wrong generation of the file."""
    monkeypatch.setattr(T, "TRIALS", tmp_path / "trials")
    f = tmp_path / "c.md"
    f.write_text("one")
    tid = T.begin()
    a = T.backup_file(tid, f, 0)
    f.write_text("two")
    b = T.backup_file(tid, f, 0)
    assert a != b
    assert Path(a).read_text() == "one" and Path(b).read_text() == "two"


def test_a_trial_is_single_use(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "TRIALS", tmp_path / "trials")
    f = tmp_path / "x.md"
    f.write_text("a")
    tid = T.begin()
    snap = T.backup_file(tid, f, 0)
    T.record(tid, {"kind": "file", "path": str(f), "existed": True, "snapshot": snap})
    f.write_text("b")

    assert T.revert(tid)["ok"] is True
    f.write_text("c")                              # the tree moved on
    second = T.revert(tid)
    assert second["ok"] is False and "already reverted" in second["error"]
    assert f.read_text() == "c"                    # untouched by the stale revert


def test_a_failed_reverse_is_reported_not_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "TRIALS", tmp_path / "trials")
    tid = T.begin()
    T.record(tid, {"kind": "file", "path": str(tmp_path / "nope" / "deep" / "x.md"),
                   "existed": True, "snapshot": str(tmp_path / "missing-snapshot")})
    r = T.revert(tid)
    assert r["ok"] is False
    assert r["results"][0]["action"] == "FAILED"


def test_latest_live_ignores_reverted_trials(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "TRIALS", tmp_path / "trials")
    a = T.begin("a")
    assert T.latest_live()["id"] == a
    T.revert(a)
    assert T.latest_live() is None


def test_backups_live_outside_the_target(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "TRIALS", tmp_path / "trials")
    d = tmp_path / "profile" / "skills" / "s"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("v1")
    tid = T.begin()
    snap = T.backup_dir(tid, d, 0)
    assert str(tmp_path / "trials") in snap      # never inside the profile it tested


# ── the swallow class: a failed read must never be persisted as an empty state ────

def test_a_corrupt_trial_record_is_NOT_replaced(tmp_path):
    """The bug this pins: `record()` did `load(tid) or {fresh}`, and `load` returned None
    on a parse error — so a corrupt record read as ABSENT and was overwritten with a
    record holding one op. Every reverse already in it was destroyed, and `revert` would
    then undo only the last thing."""
    from panecore import trial as TR
    tid = TR.begin("thing")
    TR.record(tid, {"kind": "file", "path": "/x", "existed": True, "snapshot": "/s", "what": "op1"})
    f = TR._file(tid)
    good = f.read_text()
    TR._file(tid).write_text(good[:20])              # truncated, as a crash leaves it

    try:
        TR.record(tid, {"kind": "file", "path": "/y", "existed": True,
                        "snapshot": "/s2", "what": "op2"})
    except TR.TrialCorrupt as e:
        assert "unreadable" in str(e)
    else:
        raise AssertionError("record must refuse rather than replace a corrupt record")

    assert f.read_text() == good[:20], "the corrupt record must be left alone"


def test_a_genuinely_absent_trial_is_still_None(tmp_path):
    """Absent IS a fact — only unreadable is a failure."""
    from panecore import trial as TR
    assert TR.load("20260101-000000-nope") is None


def test_revert_reports_a_corrupt_record_instead_of_claiming_absence(tmp_path):
    from panecore import trial as TR
    tid = TR.begin("thing")
    good = TR._file(tid).read_text()
    TR._file(tid).write_text(good[:15])
    r = TR.revert(tid)
    assert r["ok"] is False and "unreadable" in r["error"], r


def test_the_trial_save_is_atomic(tmp_path):
    from panecore import trial as TR
    tid = TR.begin("thing")
    TR.record(tid, {"kind": "file", "path": "/x", "existed": True, "snapshot": "/s", "what": "op"})
    assert TR._file(tid).read_text().strip().endswith("}")
    assert not list(TR._dir(tid).glob("*.tmp")), "no temp file may be left behind"


def test_latest_live_skips_a_corrupt_record_without_failing(tmp_path):
    """One bad file must not break the pane's poll — but it cannot be called 'live'
    either, since its reverses are unknown.

    Uses a PRIVATE trials dir: `latest_live` scans the module-global `TRIALS`, and the
    sandbox is shared for the whole pytest process, so a trial left by an earlier test
    outranks this one and the assertion becomes order-dependent.
    """
    from panecore import trial as TR
    saved, TR.TRIALS = TR.TRIALS, tmp_path / "trials"
    try:
        bad = TR.begin("bad")
        TR._file(bad).write_text("{ truncated")
        good = TR.begin("good")

        live = TR.latest_live()
        assert live is not None and live["id"] == good, live
        assert live["id"] != bad
    finally:
        TR.TRIALS = saved


def test_latest_live_returns_none_when_the_only_trial_is_corrupt(tmp_path):
    from panecore import trial as TR
    saved, TR.TRIALS = TR.TRIALS, tmp_path / "trials"
    try:
        bad = TR.begin("bad")
        TR._file(bad).write_text("{ truncated")
        assert TR.latest_live() is None, "a corrupt record is not a live trial"
    finally:
        TR.TRIALS = saved
