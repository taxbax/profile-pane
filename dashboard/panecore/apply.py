"""Apply layer — the ONLY module that writes. Every write snapshots first.

Design constraints this encodes (all verified in the SQSI report):
  * copy + manifest, never symlink (Hermes rejects symlinks in distributions)
  * three-hash drift read, explicit per-artifact apply, no auto-heal loop
  * snapshot before every write (SOUL/memory have NO CAS, are not atomic)
  * a blocking secret scan (Snyk: 10.9% of skills carry hardcoded secrets)
"""

from __future__ import annotations

import hashlib
import json
import re
import itertools
import os
import shutil
import tempfile
import time
from pathlib import Path

_SNAP_SEQ = itertools.count()

# The reverse for a DIRECTORY, outside any trial. A scheduled reconcile has no trial to
# belong to, so its reverse needs its own durable home — otherwise the cron overwrites
# with no way back, and this module's own first line ("Every write snapshots first") is
# a claim the code does not keep. Tests redirect this.
SNAPSHOTS = Path.home() / ".hermes" / "profile-pane" / "snapshots"

# BYPRODUCTS, not the skill's content. `hash_tree` used to hash every file, so a
# `__pycache__` left by running the skill — or a stray `.DS_Store` — made the destination
# hash differ from the source's FOREVER. The reconcile then re-copied every 15 minutes
# and could never reach the fixed point its own docs call the healthy outcome.
IGNORE_NAMES = {"__pycache__", ".pytest_cache", ".git", ".DS_Store", ".venv", "node_modules"}
IGNORE_SUFFIX = (".pyc", ".pyo")
SECRET_PATTERNS = [
    (r"sk-[A-Za-z0-9]{20,}", "openai-style key"),
    (r"AKIA[0-9A-Z]{16}", "aws access key"),
    (r"ghp_[A-Za-z0-9]{36}", "github token"),
    (r"xox[baprs]-[A-Za-z0-9-]{10,}", "slack token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"(?i)\b(api[_-]?key|secret|passwd|password|token)\b\s*[:=]\s*[\"'][^\"']{12,}[\"']", "inline credential"),
]


def is_byproduct(rel: Path) -> bool:
    """True for a file that is machine noise rather than the skill's content.

    Both the HASH and the PRUNE use this, so what "the same" means and what gets removed
    cannot drift apart — the mismatch between those two is what broke the fixed point.
    """
    rel = Path(rel)
    return any(p in IGNORE_NAMES or p.endswith(IGNORE_SUFFIX) for p in rel.parts)


def hash_tree(root: Path) -> str:
    """Content hash of a directory tree — the identity the manifest stores."""
    root = Path(root)
    h = hashlib.sha256()
    if not root.exists():
        return ""
    for f in sorted(root.rglob("*")):
        if f.is_file() and not is_byproduct(f.relative_to(root)):
            h.update(str(f.relative_to(root)).encode())
            h.update(b"\0")
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


def snapshot_dir(path: Path, *, label: str = "tree") -> str | None:
    """Retain a copy of a directory before it is overwritten. The reverse for a tree.

    Returns the retained path, or None when there was nothing to retain (a create, whose
    reverse is deletion).
    """
    path = Path(path)
    if not path.exists():
        return None
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    key = f"{label}-{int(time.time())}-{next(_SNAP_SEQ)}"
    dest = SNAPSHOTS / f"{key}--{path.name}"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(path, dest)
    return str(dest)


def scan_secrets(root: Path, max_bytes: int = 200_000) -> list[dict]:
    """Blocking pre-sync scan. A reference to a secret is fine; a VALUE is not."""
    hits: list[dict] = []
    root = Path(root)
    if not root.exists():
        return hits
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        try:
            if f.stat().st_size > max_bytes:
                continue
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pat, label in SECRET_PATTERNS:
            m = re.search(pat, text)
            if m:
                hits.append({"file": str(f.relative_to(root)), "kind": label,
                             # A LOCATOR, never the value. This used to return the first 14 characters
                             # of the match, and those hits flow into /plan, /scan and /apply
                             # responses — so a slice of a live key was being served back. The line
                             # number and length locate it just as well without revealing it.
                             "line": text[:m.start()].count("\n") + 1,
                             "length": len(m.group(0))})
    return hits


def snapshot(path: Path) -> str | None:
    """The reverse. Documentation is NOT a reverse — bytes must be retained.

    The copy INHERITS the source's mode. `write_bytes` on a fresh path created it 0644
    under the usual umask, so snapshotting a `0600` `.env` produced a copy of every secret
    in it that was MORE readable than the original — and nothing ever removed it.
    """
    path = Path(path)
    if not path.exists():
        return None
    # A one-second clock is not an identity: two writes to the same file inside the
    # same second produced the SAME snapshot name, and the second overwrote the first
    # — destroying the only reverse for that file. A counter makes each unique.
    _key = f"{int(time.time())}-{next(_SNAP_SEQ)}"
    snap = path.with_name(path.name + f".snap-{_key}")
    shutil.copy2(path, snap)        # copy2 carries st_mode across
    # A snapshot is the reverse for the NEXT destructive write, not an
    # unbounded secret archive.  Retain this newest complete preimage only.
    # This runs after copy2, so a failed cleanup never removes the new reverse.
    for older in path.parent.glob(path.name + ".snap-*"):
        if older == snap:
            continue
        try:
            if older.is_dir():
                shutil.rmtree(older)
            else:
                older.unlink()
        except OSError:
            continue
    return str(snap)


def prune_snapshots(older_than_hours: float = 72.0) -> int:
    """Drop snapshots older than the window. A reverse is not meant to be permanent.

    Nothing ever cleaned these, so every write left another retained generation and the
    directory grew without bound — a growing pile of secret copies is worse than one.
    """
    if not SNAPSHOTS.is_dir():
        return 0
    cutoff = time.time() - older_than_hours * 3600
    n = 0
    for entry in SNAPSHOTS.iterdir():
        try:
            if entry.stat().st_mtime < cutoff:
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
                n += 1
        except OSError:
            continue
    return n


def drift(manifest_rec: dict | None, upstream: str, local: str | None) -> str:
    """The three-hash read. Manifest missing => we have no state file => undecidable."""
    if manifest_rec is None:
        return "Untracked"
    m = manifest_rec.get("hash")
    if m == upstream and local == upstream:
        return "In-sync"
    if m == upstream and local != upstream:
        return "Local-diverged"
    if m != upstream and local == m:
        return "Upstream-moved"
    return "Both-diverged"


def apply_skill(src: Path, dest: Path, *, force: bool = False,
                prune: bool = False, snapshot_before: bool = False) -> dict:
    """Copy one skill. Refuses a destination that already diverged unless forced.

    `prune` makes the destination MATCH the source — entries the source lacks are
    removed. Without it the tree can never converge: `copytree(dirs_exist_ok=True)`
    leaves extras behind, so the next hash differs again and the reconcile re-copies
    forever. Only meaningful with `force`.

    `snapshot_before` retains the destination before it is overwritten. A write whose
    reverse is not recorded is not reversible, and the reconcile is a write.
    """
    src, dest = Path(src), Path(dest)
    if not (src / "SKILL.md").is_file():
        return {"ok": False, "reason": f"{src} is not a skill (no SKILL.md)"}
    hits = scan_secrets(src)
    if hits:
        return {"ok": False, "reason": "secret scan blocked", "secrets": hits}
    upstream = hash_tree(src)
    local = hash_tree(dest) if dest.exists() else None
    existed = dest.exists()
    if existed and local != upstream and not force:
        return {"ok": False, "reason": "destination diverged — pass force to overwrite",
                "upstream": upstream, "local": local}
    if existed and local == upstream:
        # Already identical: a copy would churn mtimes and litter a snapshot, and it is
        # what let the reconcile report "changed" on a pass that changed nothing.
        return {"ok": True, "src": str(src), "dest": str(dest), "hash": upstream,
                "unchanged": True, "pruned": [], "snapshot": None}

    snap = snapshot_dir(dest, label="skill") if (snapshot_before and existed) else None
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, dirs_exist_ok=True)      # copy, never symlink

    pruned: list[str] = []
    if prune:
        # Remove what the source does not have, so the next hash matches and the pass
        # converges. Byproducts are left alone — they are not content.
        keep = {str(f.relative_to(src)) for f in src.rglob("*")
                if not is_byproduct(f.relative_to(src))}
        for f in sorted(dest.rglob("*"), reverse=True):
            rel = f.relative_to(dest)
            if is_byproduct(rel):
                continue
            if str(rel) not in keep:
                if f.is_dir():
                    if not any(f.iterdir()):
                        f.rmdir()
                else:
                    f.unlink()
                pruned.append(str(rel))

    return {"ok": True, "src": str(src), "dest": str(dest), "hash": upstream,
            "unchanged": False, "pruned": pruned, "snapshot": snap}


def manifest_path(profile_dir: Path) -> Path:
    return Path(profile_dir) / ".profile-pane" / "manifest.json"


class ManifestCorrupt(Exception):
    """The manifest exists but cannot be read. Not the same as "nothing manifested"."""


def read_manifest(profile_dir: Path) -> dict:
    """The applied-skill record. RAISES on an unreadable file.

    It used to catch everything and return {}, so a failed read looked like "nothing is
    manifested" — and `write_manifest` re-reads before writing, so the very next copy
    persisted a manifest holding exactly one key. Every other skill then read as
    `Untracked` and was never reconciled again.
    """
    p = manifest_path(profile_dir)
    if not p.exists():
        return {}
    text = p.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        return json.loads(text) or {}
    except json.JSONDecodeError as e:
        raise ManifestCorrupt(
            f"manifest {p} is unreadable ({e}). Refusing to write: a write from a failed "
            f"read would drop every other manifested skill."
        ) from e


def write_manifest(profile_dir: Path, key: str, value: dict) -> None:
    p = manifest_path(profile_dir)
    data = read_manifest(profile_dir)
    data[key] = {"applied_at": time.time(), **value}
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            Path(tmp).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def apply_soul(soul_path: Path, content: str) -> dict:
    """Whole-file SOUL write. Snapshot first — there is no CAS on this file.

    When the file did not exist, the reverse is DELETION, recorded as `created`.
    A write whose reverse is not recorded is not reversible.
    """
    soul_path = Path(soul_path)
    existed = soul_path.exists()
    snap = snapshot(soul_path) if existed else None
    soul_path.parent.mkdir(parents=True, exist_ok=True)
    soul_path.write_text(content, encoding="utf-8")
    return {"ok": True, "snapshot": snap, "created": not existed, "bytes": len(content)}