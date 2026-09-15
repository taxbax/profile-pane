"""Path safety for cross-profile writes.

WHY THIS EXISTS: skills-manager issue #436 ("[Data loss] Workspace import computes
a flat target path, can delete a category directory (Hermes)") — a naive importer
computes `dest = skills_root / skill_name` and can land on a CATEGORY directory
instead of a skill, then delete it.

This tree has a LIVE instance of exactly that collision:

    ~/.hermes/skills/github/                       <- a CATEGORY dir (holds github-auth/, ...)
    ~/.hermes/skills/software-development/github/  <- a SKILL (holds SKILL.md)

Both flatten to `<skills>/github`. The guard below refuses that target.
"""

from __future__ import annotations

from pathlib import Path


def is_skill_dir(p: Path) -> bool:
    """A directory is a skill iff it directly contains SKILL.md."""
    return (Path(p) / "SKILL.md").is_file()


def resolve_target(skills_root: Path, rel: str) -> tuple[bool, str]:
    """Return `(ok, target_or_reason)`.

    The invariant: the path this RETURNS must be safe to write and safe to remove.

    It used to validate a flattened reconstruction (`skills_root / parts[-1]`) and then
    return the NESTED path (`skills_root.joinpath(*parts)`). Those are different paths, so
    validation said nothing about the one that came back: a `rel` like `<category>/<sub>`
    passed because `skills/<sub>` did not exist, and the caller then moved
    `skills/<category>/<sub>` — a whole CATEGORY directory — into the trash. That is
    skills-manager #436 relived, in the guard whose docstring cites it.
    """
    skills_root = Path(skills_root)
    raw = Path(rel)
    if raw.is_absolute():
        return False, "absolute paths are refused"
    if ".." in raw.parts:
        return False, "path traversal is refused"

    parts = [p for p in raw.parts if p not in ("", ".", "..")]
    if not parts:
        return False, "empty relative path"

    target = skills_root.joinpath(*parts)

    # 1. The FLAT name must not collide with an existing non-skill directory. This is the
    #    #436 copy-side guard: `github/` (a category) vs `software-development/github/`
    #    (a skill) both flatten to `<skills>/github`.
    flat = skills_root / parts[-1]
    if flat.exists() and not is_skill_dir(flat):
        return False, f"{flat} is a category directory, not a skill (would collide)"

    # 2. The RETURNED path itself. If it exists it must be a skill — removing or
    #    overwriting anything else is how a category gets eaten.
    if target.exists() and not is_skill_dir(target):
        return False, (f"{target} exists but is not a skill (no SKILL.md) — refusing to "
                       f"touch a directory that is not a skill")

    # 3. Its parent must be a directory, not something a path segment sits on by accident.
    parent = target.parent
    if parent != skills_root and parent.exists() and not parent.is_dir():
        return False, f"{parent} exists and is not a directory"

    # 4. Containment, symlinks resolved. `exists()`/`is_dir()` above follow links, so a
    #    skill directory symlinked outside the profile would otherwise pass every check
    #    and be written THROUGH. The root itself is allowed through unchanged.
    root_r = skills_root.resolve()
    try:
        cand_r = target.resolve()
    except OSError:
        return False, f"{target} cannot be resolved"
    if not cand_r.is_relative_to(root_r):
        return False, f"{target} resolves outside the skills root ({cand_r})"

    return True, str(target)


def safe_join(root: Path, rel: str) -> tuple[bool, str]:
    """Traversal guard for any profile-relative path (mirrors ctx.rest's '..' rejection)."""
    root = Path(root).resolve()
    cand = (root / rel).resolve()
    if root != cand and root not in cand.parents:
        return False, "path escapes root"
    return True, str(cand)
