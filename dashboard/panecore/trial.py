"""Trial apply — apply the queue for REAL, then put every byte back.

Preview answers "what WOULD happen". A trial answers "what DOES happen": it is the same
apply, with the reverse recorded for EVERY write, so one call restores the tree. That is
what lets you watch a profile actually behave under a change without keeping it.

The reverse is always BYTES, never documentation:

  existing file  → its bytes are copied aside before the write
  new file       → the reverse is DELETION
  existing dir   → the tree is copied aside before the copy lands
  new dir        → the reverse is removal of the tree

Revert runs in REVERSE ORDER, so overlapping writes unwind correctly. A trial is
single-use: once reverted it refuses to run again, so a stale revert button cannot
scramble a tree that has since moved on.

Backups live under `~/.hermes/profile-pane/trials/<id>/`, never inside a profile, so a
trial leaves no residue in the thing it is testing.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

TRIALS = Path.home() / ".hermes" / "profile-pane" / "trials"


class TrialCorrupt(Exception):
    """A trial record exists but cannot be read.

    Deliberately NOT the same as "no such trial". A failed read is not an empty trial,
    and treating it as one is what lets a writer overwrite the record with a single op —
    dropping the reverses already recorded in it.
    """


def _dir(tid: str) -> Path:
    return TRIALS / tid


def _file(tid: str) -> Path:
    return _dir(tid) / "trial.json"


def _save(tid: str, rec: dict) -> None:
    """Atomic. A reader sees the old record or the new one, never a truncated file —
    a half-written record is indistinguishable from a corrupt one, and corruption is
    what makes the reverses unreachable."""
    d = _dir(tid)
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(d), prefix="trial.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, _file(tid))
    except BaseException:
        try:
            Path(tmp).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def load(tid: str) -> dict | None:
    """The record, or None when there is genuinely no such trial.

    Raises TrialCorrupt on a record that exists but does not parse. It used to catch
    everything and return None, so a corrupt record read as ABSENT — and `record()` then
    took its `or {fresh}` branch and wrote a record holding one op, destroying every
    reverse already in it. A failed read must never be persisted as an empty state.
    """
    f = _file(tid)
    if not f.exists():
        return None
    text = f.read_text(encoding="utf-8")
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise TrialCorrupt(
            f"trial {tid} is unreadable ({e}). Refusing to treat a failed read as an "
            f"absent trial: doing so would overwrite it and lose its recorded reverses."
        ) from e


def begin(label: str = "") -> str:
    tid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    _save(tid, {"id": tid, "at": time.time(), "label": label, "ops": [], "reverted": None})
    return tid


def record(tid: str, op: dict) -> None:
    """Append one reverse. A corrupt record RAISES rather than being replaced."""
    rec = load(tid)
    if rec is None:
        rec = {"id": tid, "at": time.time(), "ops": [], "reverted": None}
    rec.setdefault("ops", []).append(op)
    _save(tid, rec)


def backup_dir(tid: str, src: Path, n: int) -> str:
    # The path carries a random suffix as well as the index. Keying on the caller's `n`
    # alone means two ops that pass the same index overwrite each other's backup — the
    # second restore then silently returns the wrong generation of the file.
    dest = _dir(tid) / "blobs" / f"{n:03d}-{uuid.uuid4().hex[:6]}-dir"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return str(dest)


def backup_file(tid: str, src: Path, n: int) -> str:
    dest = _dir(tid) / "blobs" / f"{n:03d}-{uuid.uuid4().hex[:6]}-file"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return str(dest)


def revert(tid: str) -> dict:
    """Undo the trial, in reverse order. Single-use."""
    try:
        rec = load(tid)
    except TrialCorrupt as e:
        return {"ok": False, "error": str(e)}
    if not rec:
        return {"ok": False, "error": "no such trial"}
    if rec.get("reverted"):
        return {"ok": False, "error": "this trial was already reverted"}

    results = []
    for op in reversed(rec.get("ops", [])):
        p = Path(op["path"])
        try:
            if op["kind"] == "file":
                if op.get("existed") and op.get("snapshot"):
                    p.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(op["snapshot"], p)
                    results.append({"path": str(p), "action": "restored"})
                else:
                    if p.exists():
                        p.unlink()
                    results.append({"path": str(p), "action": "deleted (was new)"})
            elif op["kind"] == "dir":
                if op.get("existed") and op.get("snapshot"):
                    if p.exists():
                        shutil.rmtree(p)
                    shutil.copytree(op["snapshot"], p)
                    results.append({"path": str(p), "action": "restored tree"})
                else:
                    if p.exists():
                        shutil.rmtree(p)
                    results.append({"path": str(p), "action": "removed tree (was new)"})
        except Exception as e:                                        # noqa: BLE001
            results.append({"path": str(p), "action": "FAILED", "error": f"{type(e).__name__}: {e}"})

    rec["reverted"] = time.time()
    rec["revert_results"] = results
    _save(tid, rec)
    ok = all(r["action"] != "FAILED" for r in results)
    return {"ok": ok, "trial": tid, "results": results, "count": len(results)}


def latest_live() -> dict | None:
    """The most recent trial that has NOT been reverted — what the Revert button undoes."""
    if not TRIALS.is_dir():
        return None
    for d in sorted((x for x in TRIALS.iterdir() if x.is_dir()), reverse=True):
        try:
            rec = load(d.name)
        except TrialCorrupt:
            # A record we cannot read cannot be reverted either — we do not know what its
            # reverses are. Skipped KNOWINGLY: one corrupt file must not break the poll.
            continue
        if rec and not rec.get("reverted"):
            return rec
    return None


def prune(older_than_hours: float = 24.0) -> int:
    """Drop old trial backups. A reverted trial is dead weight the moment it is undone."""
    if not TRIALS.is_dir():
        return 0
    cutoff = time.time() - older_than_hours * 3600
    n = 0
    for d in TRIALS.iterdir():
        if not d.is_dir():
            continue
        try:
            rec = load(d.name)
        except TrialCorrupt:
            continue          # never delete a record we cannot read
        if rec and rec.get("reverted") and rec.get("reverted", 0) < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    return n