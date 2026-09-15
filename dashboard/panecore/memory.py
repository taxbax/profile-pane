"""Memory + profile-entry read/write — the layers the pane must treat most carefully.

Facts about these files (verified against the live tree, 2026-09-12):

  * `memories/MEMORY.md` and `memories/USER.md` are BLOCK files. Blocks are
    separated by a line containing exactly one `§`. The default profile's
    MEMORY.md has 43 blocks / 42 separators; USER.md has 23 / 22.
  * Writing one is a WHOLE-FILE write. There is no CAS and no row-level edit, so a
    concurrent append in the running agent can be lost. Every write therefore
    snapshots first, and the caller must surface that risk.
  * `profile.yaml` is YAML. Only `description` is a semantic entry a user authors;
    `ui_meta` is CAS-guarded by the gateway (per-key `_ui_meta_revisions`) and must
    NOT be touched from here — writing it would fight the gateway's own bookkeeping.

Nothing in this module decides policy. It reads and writes what it is told to.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from . import apply as apply_mod

# A block separator is a line that is exactly `§` (allow surrounding whitespace).
_SEP_RE = re.compile(r"(?:^|\n)[ \t]*\u00a7[ \t]*(?:\n|$)")


def _mem_file(profile_dir: Path, which: str) -> Path:
    name = "MEMORY.md" if which == "memory" else "USER.md"
    return Path(profile_dir) / "memories" / name


def split_blocks(text: str) -> list[str]:
    """Split a block file into its entries. Robust to a leading separator."""
    return [b.strip() for b in _SEP_RE.split(text or "") if b and b.strip()]


def merge_blocks(member_blocks: list[str], anchor_blocks: list[str]) -> list[str]:
    """The anchor's set is canonical and comes FIRST; the member's own extras trail it.

    This is what makes `append` different from `push`: push makes the member identical
    to the anchor, append guarantees the anchor's entries are present while every extra
    the member accumulated stays. Deterministic, so a re-run is a no-op — the anchor's
    entries keep their order and the extras keep theirs.
    """
    anchor = [b.strip() for b in anchor_blocks if b and b.strip()]
    seen = set(anchor)
    extras = [b.strip() for b in member_blocks if b and b.strip() and b.strip() not in seen]
    return anchor + extras


def block_key(text: str) -> str:
    """A stable key for one block, so a pick can name a block across reads.

    Content, not position. Indices shift whenever the anchor gains or loses an entry, and a
    pick that silently resolves to a DIFFERENT block after such a change is worse than a
    pick that resolves to nothing. A content key makes that impossible: if the text changed,
    the key is simply gone, and the caller is told rather than given the wrong block.
    """
    return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()[:12]


def read_blocks(profile_dir: Path, which: str) -> dict:
    p = _mem_file(profile_dir, which)
    if not p.exists():
        return {"ok": True, "exists": False, "blocks": [], "chars": 0, "path": str(p)}
    text = p.read_text(encoding="utf-8", errors="replace")
    blocks = split_blocks(text)
    return {
        "ok": True, "exists": True, "path": str(p), "chars": len(text),
        "blocks": [{"i": i, "text": b, "chars": len(b), "key": block_key(b)}
                   for i, b in enumerate(blocks)],
    }


def write_blocks(profile_dir: Path, which: str, blocks: list[str]) -> dict:
    """Whole-file rewrite of a block file. Snapshots first — no CAS exists here.

    Identical content is NOT rewritten: a no-op write would litter a snapshot and bump
    the mtime, which makes "nothing to sync" indistinguishable from "synced".
    """
    p = _mem_file(profile_dir, which)
    existed = p.exists()
    clean = [b.strip() for b in blocks if b and b.strip()]
    text = ("\n\u00a7\n".join(clean) + "\n") if clean else ""

    if existed and p.read_text(encoding="utf-8", errors="replace") == text:
        return {"ok": True, "path": str(p), "snapshot": None, "unchanged": True,
                "created": False, "blocks": len(clean), "chars": len(text)}

    snap = apply_mod.snapshot(p) if existed else None
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return {"ok": True, "path": str(p), "snapshot": snap, "created": not existed,
            "unchanged": False, "blocks": len(clean), "chars": len(text)}


def _unquote(s: str) -> str:
    """A YAML scalar keeps its quotes unless you take them off.

    `description: 'Media Control: …'` is stored WITH the quotes, so a naive scalar grab
    renders the quote as part of the text — which is exactly what the pane was showing.
    """
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        inner = s[1:-1]
        return inner.replace("''", "'") if s[0] == "'" else inner.replace('\\"', '"')
    return s


def _quote(s: str) -> str:
    """Emit a scalar that survives a YAML round-trip: quote it when it could be read as
    anything other than a plain string."""
    if s == "" or s != s.strip() or any(c in s for c in ":#{}[],&*?|>%@`\"'") or s[0] in "- ":
        return "'" + s.replace("'", "''") + "'"
    return s


def read_profile_entry(profile_dir: Path) -> dict:
    """The authored `description` from profile.yaml. ui_meta is left alone."""
    p = Path(profile_dir) / "profile.yaml"
    if not p.exists():
        return {"ok": True, "exists": False, "has_key": False, "description": "", "path": str(p)}
    text = p.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^description:[ \t]*(.*)$", text, flags=re.M)
    if not m:
        return {"ok": True, "exists": True, "has_key": False, "description": "", "path": str(p)}

    rest = m.group(1).rstrip()
    if rest in ("|", "|-", "|+", ">", ">-", ">+"):
        # a block scalar — the value is the indented run that follows
        body = []
        for ln in text[m.end():].split("\n")[1:]:
            if ln.strip() and not ln[:1].isspace():
                break                       # back at a top-level key
            body.append(ln.strip())
        joined = ("\n" if rest.startswith("|") else " ").join(x for x in body if x)
        return {"ok": True, "exists": True, "has_key": True,
                "description": joined.strip(), "path": str(p), "style": "block"}

    # A quoted OR plain scalar may WRAP onto indented continuation lines:
    #   description: 'long text…
    #     …still going.'
    # Taking only the first line leaves a dangling open-quote — which is what the pane
    # was rendering for every profile whose description wrapped.
    parts = [rest]
    for ln in text[m.end():].split("\n")[1:]:
        if ln.strip() and not ln[:1].isspace():
            break                           # back at a top-level key
        if not ln.strip():
            continue
        parts.append(ln.strip())
        if rest[:1] in ("'", '"') and ln.rstrip().endswith(rest[0]):
            break                           # the closing quote arrived
    rest = " ".join(x for x in parts if x)

    return {"ok": True, "exists": True, "has_key": True,
            "description": _unquote(rest), "path": str(p), "style": "scalar"}


def write_profile_entry(profile_dir: Path, description: str) -> dict:
    """Replace ONLY the top-level `description:` scalar, leaving every other key
    (ui_meta, revisions, ordering) byte-identical. Never a YAML round-trip."""
    p = Path(profile_dir) / "profile.yaml"
    existed = p.exists()
    snap = apply_mod.snapshot(p) if existed else None
    text = p.read_text(encoding="utf-8") if existed else ""

    # a YAML block scalar is written as `description: |` + indented lines; a simple
    # scalar starts on the same line. Match either, replacing through to the next
    # top-level key.
    block = re.search(r"^description:[ \t]*(\||>|-)[-+]?[ \t]*$", text, flags=re.M)
    if block:
        # a block scalar: replace the header plus its indented run
        end = block.end()
        for ln in text[end:].split("\n")[1:]:
            if ln.strip() and not ln[:1].isspace():
                break
            end += len(ln) + 1
        new = text[:block.start()] + f"description: {_quote(description.strip())}" + text[end:]
    else:
        scalar = re.search(r"^description:.*$", text, flags=re.M)
        rendered = "description: " + _quote(description.strip())
        if scalar:
            new = text[:scalar.start()] + rendered + text[scalar.end():]
        else:
            new = rendered + "\n" + text

    # Same no-op rule as the block files: an unchanged scalar is not rewritten, or the
    # reconciler reports a change on every pass forever.
    if existed and new == text:
        return {"ok": True, "path": str(p), "snapshot": None, "created": False,
                "unchanged": True, "description": description.strip()}

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(new, encoding="utf-8")
    return {"ok": True, "path": str(p), "snapshot": snap, "created": not existed,
            "unchanged": False, "description": description.strip()}
