"""Tests for panecore — the pure logic behind the profile pane.

Run:  python3 -m pytest ~/.hermes/plugins/profile-pane/tests/ -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))

from panecore.paths import resolve_target, is_skill_dir, safe_join          # noqa: E402
from panecore.groups import GroupRegistry, SOUL_SCOPE_CONSEQUENCE            # noqa: E402


def test_a_corrupt_registry_is_never_overwritten(tmp_path):
    """The failure mode that could delete every group.

    `_all()` caught every exception and returned {}. So a read that failed looked like an
    EMPTY registry, and the next save then wrote a file holding only that one group —
    every other group in it destroyed by a read that failed. Absent is a fact; unreadable
    is a failure, and a failure must never be persisted.
    """
    p = tmp_path / "groups.json"
    p.write_text('{"a": {"id": "a", "members": []}, "b": {"id": "b", "members": []}}')
    good = p.read_text()
    p.write_text(good[:20])                       # truncated, as a crashed write leaves it

    reg = GroupRegistry(p)
    try:
        reg.save({"id": "c", "members": []})
    except ValueError as e:
        assert "unreadable" in str(e)
    else:
        raise AssertionError("save must refuse rather than persist a failed read")

    assert p.read_text() == good[:20], "the corrupt file must be left alone, not replaced"


def test_two_saves_keep_both_groups(tmp_path):
    reg = GroupRegistry(tmp_path / "groups.json")
    reg.save({"id": "a", "members": ["x"]})
    reg.save({"id": "b", "members": ["y"]})
    assert sorted(g["id"] for g in reg.all()) == ["a", "b"]


def test_a_concurrent_save_does_not_lose_the_other_group(tmp_path):
    """save() is read-whole-file, change-one, write-whole-file. Interleaved, the second
    writer reverts the first. The lock is what stops that."""
    import threading
    reg = GroupRegistry(tmp_path / "groups.json")
    reg.save({"id": "seed", "members": []})

    barrier = threading.Barrier(8)
    def writer(n):
        barrier.wait()
        GroupRegistry(tmp_path / "groups.json").save({"id": f"g{n}", "members": []})

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()

    ids = sorted(g["id"] for g in reg.all())
    assert ids == ["g0", "g1", "g2", "g3", "g4", "g5", "g6", "g7", "seed"], ids


def test_the_registry_write_is_atomic(tmp_path):
    """A reader must never observe a half-written file."""
    p = tmp_path / "groups.json"
    reg = GroupRegistry(p)
    reg.save({"id": "a", "members": []})
    assert p.read_text().strip().endswith("}")          # complete JSON, not a fragment
    assert not list(tmp_path.glob("*.tmp")), "no temp file may be left behind"


def test_resolve_target_refuses_a_nested_non_skill(tmp_path):
    """The #436 class, in the guard whose own docstring cites #436.

    It validated a FLATTENED path (`skills/<last>`) and then returned the NESTED one
    (`skills/<category>/<sub>`). A rel whose nested target is a CATEGORY directory passed,
    and `/remove-skill` then moved the whole category — every sibling skill with it.
    """
    skills = tmp_path / "skills"
    (skills / "github").mkdir(parents=True)
    (skills / "github" / "DESCRIPTION.md").write_text("category")     # not a skill
    ok, got = resolve_target(skills, "github/DESCRIPTION.md")         # nested, non-skill
    assert not ok, f"must refuse a target that is not a skill: {got}"


def test_resolve_target_still_allows_a_real_nested_skill(tmp_path):
    skills = tmp_path / "skills"
    d = skills / "software-development" / "github"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: github\n---\n")
    ok, target = resolve_target(skills, "software-development/github")
    assert ok and Path(target) == d


def test_resolve_target_refuses_traversal_and_absolute(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    assert not resolve_target(skills, "../outside")[0]
    assert not resolve_target(skills, "a/../../b")[0]
    assert not resolve_target(skills, "/etc/passwd")[0]


def test_resolve_target_refuses_a_symlink_that_escapes(tmp_path):
    """`exists()`/`is_dir()` follow links, so a symlinked skill passed every check and
    the copy was written THROUGH it, outside the profile.

    The link target is a REAL skill, so the name and skill checks both pass and only the
    containment check can catch it — otherwise this proves the wrong guard fired.
    """
    outside = tmp_path / "outside" / "real"
    outside.mkdir(parents=True)
    (outside / "SKILL.md").write_text("---\nname: real\n---\n")     # a genuine skill
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "sneaky").symlink_to(outside)

    ok, got = resolve_target(skills, "sneaky")
    assert not ok, f"a symlink out of the profile must be refused, got: {got}"
    assert "outside" in got, got


def test_a_snapshot_never_widens_permissions(tmp_path):
    """`write_bytes` created the copy 0644 under the usual umask, so snapshotting a 0600
    .env produced a copy of every secret in it that was MORE readable than the original."""
    import os
    import stat
    from panecore import apply as A
    f = tmp_path / "config"
    f.write_text("SECRET=1\n")
    os.chmod(f, 0o600)

    snap = Path(A.snapshot(f))
    src_mode = stat.S_IMODE(f.stat().st_mode)
    snap_mode = stat.S_IMODE(snap.stat().st_mode)
    assert snap_mode == src_mode, f"{oct(src_mode)} became {oct(snap_mode)}"


def test_snapshots_are_pruned(tmp_path):
    """Nothing ever cleaned these, so every write left another retained generation."""
    import os
    import time
    from panecore import apply as A
    A.SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    old = A.SNAPSHOTS / "ancient"
    old.write_text("stale")
    os.utime(old, (time.time() - 100 * 3600, time.time() - 100 * 3600))
    fresh = A.SNAPSHOTS / "recent"
    fresh.write_text("keep")
    assert A.prune_snapshots(older_than_hours=72) == 1
    assert not old.exists() and fresh.exists()


def _tree(tmp_path: Path) -> Path:
    """Reproduce the LIVE collision: a category dir and a skill that share a flat name."""
    skills = tmp_path / "skills"
    (skills / "github").mkdir(parents=True)                       # CATEGORY dir
    (skills / "github" / "DESCRIPTION.md").write_text("category")
    (skills / "github" / "github-auth").mkdir()
    (skills / "github" / "github-auth" / "SKILL.md").write_text("---\nname: github-auth\n---\n")
    (skills / "software-development" / "github").mkdir(parents=True)   # SKILL, flat name collides
    (skills / "software-development" / "github" / "SKILL.md").write_text("---\nname: github\n---\n")
    return skills


def test_category_dir_is_not_a_skill(tmp_path):
    skills = _tree(tmp_path)
    assert not is_skill_dir(skills / "github")
    assert is_skill_dir(skills / "github" / "github-auth")


def test_collision_is_refused(tmp_path):
    """The #436 data-loss path: flat target lands on a category directory."""
    skills = _tree(tmp_path)
    ok, why = resolve_target(skills, "software-development/github")
    assert ok is False
    assert "category directory" in why


def test_legit_target_resolves(tmp_path):
    skills = _tree(tmp_path)
    ok, target = resolve_target(skills, "github/github-auth")
    assert ok is True
    assert target.endswith("github/github-auth")


def test_empty_rel_refused(tmp_path):
    ok, _ = resolve_target(_tree(tmp_path), "")
    assert ok is False


def test_safe_join_blocks_traversal(tmp_path):
    ok, _ = safe_join(tmp_path, "../../etc/passwd")
    assert ok is False
    ok2, _ = safe_join(tmp_path, "a/b")
    assert ok2 is True


def test_group_defaults_are_the_conservative_rung(tmp_path):
    r = GroupRegistry(tmp_path / "groups.json")
    g = r.save({"id": "trading", "members": ["trad", "default"]})
    assert g["policy"]["soul"] == "off"
    assert g["policy"]["skills"] == "manual"
    assert g["policy"]["anchor"] is None            # no member is canonical until you say so
    assert g["policy"]["memory"] == "off"
    assert g["policy"]["auto"] == "off"             # never auto-writes until you turn it on
    assert r.load("trading")["members"] == ["trad", "default"]


def test_group_is_content_free(tmp_path):
    r = GroupRegistry(tmp_path / "groups.json")
    g = r.save({"id": "trading", "members": ["trad"]})
    assert set(g.keys()) >= {"id", "members", "policy"}
    assert "content" not in g and "skills" not in g


def test_empty_match_blocks_loudly(tmp_path):
    r = GroupRegistry(tmp_path / "groups.json")
    r.save({"id": "trading", "members": ["trad", "default"]})
    empty = r.resolve("trading", present=[])
    assert empty["blocks"] is True and empty["reason"]
    part = r.resolve("trading", present=["trad"])
    assert part["found"] == ["trad"] and part["missing"] == ["default"]
    assert part["blocks"] is False


def test_every_soul_scope_states_its_consequence():
    for mode in ("off", "block", "section", "whole"):
        assert SOUL_SCOPE_CONSEQUENCE[mode]
    assert "OVERWRITTEN" in SOUL_SCOPE_CONSEQUENCE["whole"]
