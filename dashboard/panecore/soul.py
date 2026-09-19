"""SOUL sync — anchored to a MEMBER, never to text typed into the pane.

The pane does not ask the user to author a SOUL. It asks which agent's SOUL is
canonical — the group's `soul_source` — and then how much of that anchor each member
receives:

  whole    the anchor's file, byte for byte (members become identical)
  block    the anchor's file spliced into the member between ownership markers,
           leaving every other line of the member's OWN SOUL in place
  section  only the `##` sections the group names, spliced in the same way

Splicing is IDEMPOTENT: a re-apply REPLACES the marked region instead of appending a
second copy. That property is what makes block/section re-runnable, and re-runnable is
what makes auto-sync safe to leave running.

Profile SOUL files vary widely in size; the spread is why an anchor matters. (Re-measure
on your own machine rather than trusting any copied figure — line counts go stale the
moment a file changes.)
"""

from __future__ import annotations

import re
from pathlib import Path

from . import apply as apply_mod

MARK_BEGIN = "<!-- profile-pane:shared:begin -->"
MARK_END = "<!-- profile-pane:shared:end -->"

_REGION = re.compile(re.escape(MARK_BEGIN) + r".*?" + re.escape(MARK_END), re.S)
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.M)


def soul_path(profile_dir: Path) -> Path:
    return Path(profile_dir) / "SOUL.md"


def read_soul(profile_dir: Path) -> str:
    p = soul_path(profile_dir)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def split_sections(text: str) -> list[dict]:
    """Every heading-delimited section, in document order.

    The anchor is usually a big file (sys = 1705 lines), so the pane needs the real
    headings to offer as a checklist rather than making the user guess them.
    """
    text = text or ""
    heads = list(_HEADING.finditer(text))
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[m.start():end].rstrip("\n")
        out.append({
            "level": len(m.group(1)),
            "title": m.group(2).strip(),
            "text": body,
            "lines": body.count("\n") + 1,
        })
    return out


def managed_region(text: str) -> str | None:
    """The text inside the ownership markers, or None if the file has none."""
    m = _REGION.search(text or "")
    return m.group(0) if m else None


def own_text(text: str) -> str:
    """The member's own content, with the pane-managed region removed."""
    return re.sub(r"\n{0,2}" + _REGION.pattern + r"\n{0,2}", "\n\n", text or "", flags=re.S).strip()


def splice(current: str, incoming: str) -> str:
    """Replace the marked region, or append it. Idempotent by construction."""
    region = f"{MARK_BEGIN}\n{incoming.strip()}\n{MARK_END}"
    if MARK_BEGIN in (current or "") and MARK_END in (current or ""):
        return _REGION.sub(lambda _m: region, current, count=1)
    if not (current or "").strip():
        return region + "\n"
    return current.rstrip() + "\n\n" + region + "\n"


def render(anchor_text: str, scope: str, selected: list[str] | None = None) -> str:
    """What the anchor contributes at this scope. '' means nothing to write."""
    if scope == "whole":
        return anchor_text
    if scope == "block":
        # if the anchor itself carries a managed region, that region IS the shared
        # block; otherwise the whole anchor is the block.
        return (managed_region(anchor_text) or anchor_text).strip()
    if scope == "section":
        want = set(selected or [])
        if not want:
            return ""
        return "\n\n".join(s["text"] for s in split_sections(anchor_text) if s["title"] in want).strip()
    return ""


def apply_scope(dest_dir: Path, anchor_text: str, scope: str,
                selected: list[str] | None = None) -> dict:
    """Write the anchor's contribution into ONE member's SOUL.md.

    `whole` replaces the file. `block`/`section` splice into whatever the member
    already has, so a per-bot role SOUL survives. Snapshot first — SOUL has no CAS.
    """
    if scope == "off":
        return {"ok": True, "skipped": "scope is off for this group"}
    incoming = render(anchor_text, scope, selected)
    if not incoming.strip():
        return {"ok": False, "reason": f"anchor contributes nothing at scope '{scope}'"
                                       + (" — no sections selected" if scope == "section" else "")}

    current = read_soul(dest_dir)
    if scope == "whole":
        new_text = incoming if incoming.endswith("\n") else incoming + "\n"
    else:
        new_text = splice(current, incoming)

    # An unchanged file is not rewritten: the reconciler must be able to report
    # "nothing to do", and a no-op write would litter a snapshot every pass.
    if current == new_text:
        return {"ok": True, "unchanged": True, "scope": scope,
                "bytes_in": len(incoming), "bytes_out": len(new_text), "snapshot": None}

    r = apply_mod.apply_soul(soul_path(dest_dir), new_text)
    return {
        **r, "unchanged": False, "scope": scope,
        "bytes_in": len(incoming), "bytes_out": len(new_text),
        "replaced_region": scope != "whole" and MARK_BEGIN in current,
        "own_text_lines": len(own_text(current).splitlines()) if scope != "whole" else 0,
    }
