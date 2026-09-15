"""The group registry.

A group is a CONTENT-FREE, first-class record stored OUTSIDE every profile: it
carries intent (which profiles, and what each layer does), never content. Nothing
about a group lives inside a member profile, so a group can never "leak" into a
profile's own files.

Reconcile model: copy + manifest (never symlink — Hermes rejects symlinks in
profile distributions). Drift is signal, not error.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl          # POSIX advisory locks; absent on Windows
except ImportError:       # pragma: no cover
    fcntl = None

# Every layer's default is the conservative rung. The USER raises it, per group,
# in the pane — nothing here is a policy the agent imposes.
DEFAULT_POLICY: dict = {
    "skills": "manual",        # manual | sync
    "soul": "off",             # off | block | section | whole
    "anchor": None,            # WHICH member is canonical — every layer pushes FROM it
    "soul_sections": [],       # used by `section` mode
    "memory": "off",           # off | push   (memories/MEMORY.md)
    "user": "off",             # off | push   (memories/USER.md)
    "profile": "off",          # off | push   (profile.yaml description)
    "secrets": "off",          # off | push   (.env KEY VALUES, only `secret_keys`)
    "secret_keys": [],         # which .env keys move — names only, never values
    "auto": "off",             # off | on     (the reconcile pass re-runs this group)
}

SOUL_SCOPE_CONSEQUENCE = {
    "off": "SOUL is not touched in this group.",
    "block": "Chosen rule blocks are appended under ownership markers; all other SOUL text is untouched.",
    "section": "Only the sections you tick are synced; all other SOUL text is untouched.",
    "whole": "Every member of this group becomes BYTE-IDENTICAL in SOUL.md — per-bot role identity "
             "(e.g. the 12-line trader role) will be OVERWRITTEN.",
}


class GroupRegistry:
    """JSON-backed registry. One file, outside every profile."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    @contextmanager
    def _locked(self):
        """Serialize the read-modify-write.

        `save` and `delete` are both read-the-whole-file, change one entry, write-the-
        whole-file. Two of them interleaving lose one another's work outright: both read
        the same base, both write, and the second silently reverts the first. A rename
        (`/rename-group`) is TWO such cycles, which widens the window further.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.lock_path, "a+")
        try:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()

    def _read(self) -> dict:
        """Raises on an unreadable file instead of reporting "no groups".

        This used to catch everything and return {}. So a transient read failure — a
        concurrent writer mid-write, a truncated file — looked like an EMPTY registry,
        and the very next `save` then wrote a file containing only that one group.
        Every other group in it was destroyed by a read that failed.

        Absent is a fact. Unreadable is a failure, and a failure must not be persisted.
        """
        if not self.path.exists():
            return {}
        text = self.path.read_text(encoding="utf-8")
        if not text.strip():
            return {}
        try:
            return json.loads(text) or {}
        except json.JSONDecodeError as e:
            raise ValueError(
                f"group registry {self.path} is unreadable ({e}). Refusing to write: a "
                f"write based on a failed read would drop every other group in it."
            ) from e

    def _all(self) -> dict:
        return self._read()

    def _write(self, data: dict) -> None:
        """Atomic. A reader sees the old file or the new one, never a half-written one,
        and a crash mid-write cannot leave a truncated registry behind."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent),
                                   prefix=self.path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)          # atomic on POSIX and Windows
        except BaseException:
            try:
                Path(tmp).unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def save(self, group: dict) -> dict:
        g = dict(group)
        if not g.get("id"):
            raise ValueError("group needs an id")
        policy = dict(g.get("policy") or {})
        for k, v in DEFAULT_POLICY.items():
            policy.setdefault(k, v)
        g["policy"] = policy
        g.setdefault("members", [])
        with self._locked():
            data = self._read()
            data[g["id"]] = g
            self._write(data)
        return g

    def load(self, gid: str) -> dict | None:
        return self._all().get(gid)

    def all(self) -> list[dict]:
        return list(self._all().values())

    def delete(self, gid: str) -> bool:
        with self._locked():
            data = self._read()
            if gid not in data:
                return False
            data.pop(gid)
            self._write(data)
        return True

    def resolve(self, gid: str, present: list[str]) -> dict:
        """Resolve membership against the profiles that actually exist.

        Zero matches must BLOCK loudly — a silent empty selector is a known
        failure mode (Terraform "No workspaces found", k8s selector mismatch).
        """
        g = self.load(gid) or {"members": []}
        members = list(g.get("members") or [])
        found = [m for m in members if m in present]
        missing = [m for m in members if m not in present]
        return {
            "found": found,
            "missing": missing,
            "blocks": len(found) == 0,
            "reason": "" if found else "no live profile matches this group",
        }
