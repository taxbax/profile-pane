"""Secrets sync — the `.env` KEY layer.

A profile's `.env` holds the keys it can use. Syncing them across profiles is a real
operational need (one bot's key another one also needs) and a real risk (every member
then holds every key). So this module is deliberately narrow:

  * **Key NAMES are readable; VALUES are not.** `read_keys` returns names and whether a
    value is present — never the value. Nothing here returns a secret to a caller, so no
    secret can be rendered by the pane, logged, or journalled.
  * **Only the keys you tick are copied**, and they are MERGED, not replaced: a member's
    own keys survive a push.
  * **A key that is absent at the source is reported, not invented.**

`auth.json` is deliberately NOT part of this. It is a token STORE, not config: a copy
forks token state, and the first refresh in either copy strands the other
(`tui_gateway/methods_profiles.py:313`). It stays profile-local on purpose.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from . import apply as apply_mod


def env_file(profile_dir: Path) -> Path:
    return Path(profile_dir) / ".env"


def parse_env(text: str) -> list[tuple[str, str]]:
    """(key, raw_line) for every assignment, comments and blanks preserved by the caller."""
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        key = s.split("=", 1)[0].strip()
        if not key or "=" not in s:
            continue
        out.append((key, line))
    return out


def read_keys(profile_dir: Path) -> list[dict]:
    """Every key in the profile's .env, WITHOUT its value.

    `has_value` is all the pane needs to show state; a false one means the key is
    declared but empty, which is usually a sign it was never filled in.
    """
    p = env_file(profile_dir)
    if not p.exists():
        return []
    out = []
    for key, line in parse_env(p.read_text(encoding="utf-8", errors="replace")):
        value = line.split("=", 1)[1].strip().strip('"').strip("'")
        # Value length is unnecessary telemetry about a credential. The pane
        # only needs the presence bit, so keep the response non-sensitive.
        out.append({"key": key, "has_value": bool(value)})
    return out


def merge_keys(anchor_lines: list[str], member_lines: list[str], keys: list[str]) -> list[str]:
    """The member's .env with the named keys taken from the anchor.

    MERGE, not replace: a key the member has and the anchor does not is kept. An anchor
    key already present in the member is updated in place, so the file keeps its order
    and its comments. `keys` is matched exactly — nothing is copied that was not named.
    """
    want = set(keys)
    source = {k: ln for k, ln in parse_env("\n".join(anchor_lines)) if k in want}
    out: list[str] = []
    done: set[str] = set()
    for line in member_lines:
        parsed = parse_env(line)
        if parsed and parsed[0][0] in want:
            k = parsed[0][0]
            out.append(source.get(k, line))          # update in place, keep position
            done.add(k)
        else:
            out.append(line)
    for k in keys:                                   # append the rest, in the given order
        if k in done or k not in source:
            continue
        if out and out[-1].strip():
            out.append("")
        out.append(f"# synced by profile-pane from the group anchor")
        out.append(source[k])
        done.add(k)
    return out


def push(src_dir: Path, dest_dir: Path, keys: list[str]) -> dict:
    """Copy the named keys from the anchor's .env into a member's. Snapshot first."""
    src_dir, dest_dir = Path(src_dir), Path(dest_dir)
    if not keys:
        return {"ok": False, "reason": "no keys selected"}
    src_p, dest_p = env_file(src_dir), env_file(dest_dir)
    if not src_p.exists():
        return {"ok": False, "reason": "the anchor has no .env"}

    src_keys = {k for k, _ in parse_env(src_p.read_text(encoding="utf-8", errors="replace"))}
    missing = [k for k in keys if k not in src_keys]
    usable = [k for k in keys if k in src_keys]
    if not usable:
        return {"ok": False, "reason": "the anchor holds none of the selected keys",
                "missing": missing}

    anchor_lines = src_p.read_text(encoding="utf-8", errors="replace").splitlines()
    member_lines = (dest_p.read_text(encoding="utf-8", errors="replace").splitlines()
                    if dest_p.exists() else [])
    merged = merge_keys(anchor_lines, member_lines, usable)
    text = "\n".join(merged).rstrip("\n") + "\n"

    if dest_p.exists() and dest_p.read_text(encoding="utf-8", errors="replace") == text:
        return {"ok": True, "unchanged": True, "keys": usable, "missing": missing,
                "path": str(dest_p)}

    existed = dest_p.exists()
    snap = apply_mod.snapshot(dest_p) if existed else None
    dest_p.parent.mkdir(parents=True, exist_ok=True)
    # `Path.write_text` creates a new .env at the process umask (commonly
    # 0644). Write privately and replace atomically instead; preserve an
    # existing destination's mode, otherwise default to owner-read/write.
    mode = (dest_p.stat().st_mode & 0o777) if existed else 0o600
    fd, tmp_name = tempfile.mkstemp(prefix=".env.profile-pane-", dir=dest_p.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, dest_p)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return {"ok": True, "unchanged": False, "snapshot": snap, "created": not existed,
            "keys": usable, "missing": missing, "path": str(dest_p),
            "member_keys_kept": len([k for k, _ in parse_env("\n".join(member_lines))
                                     if k not in usable])}
