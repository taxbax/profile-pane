"""Tests for panecore.sync — the reconcile pass.

The properties that matter, in order: (1) a first run on an untouched profile is a
NO-OP — the reconciler is manifest-bounded and has no opinion about skills it never
copied; (2) running twice changes nothing the second time (idempotence — this is what
makes a schedule safe); (3) dry_run never writes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))

from panecore import apply as A   # noqa: E402
from panecore import sync as SY   # noqa: E402


def make_skill(root: Path, name: str, body: str = "v1") -> Path:
    d = root / name
    (d / "scripts").mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n{body}\n")
    return d


def make_profile(root: Path, name: str) -> Path:
    p = root / name
    (p / "skills").mkdir(parents=True, exist_ok=True)
    (p / "memories").mkdir(parents=True, exist_ok=True)
    return p


def test_first_run_on_an_untouched_profile_is_a_noop(tmp_path):
    p = make_profile(tmp_path / "profiles", "a")
    make_skill(tmp_path / "src", "thing")           # exists, but NOT manifested
    assert SY.reconcile_skills(p) == []


def test_the_reconcile_reaches_a_fixed_point_with_an_extra_file(tmp_path):
    """The whole point of a reconciler, and it could not be reached.

    `copytree(dirs_exist_ok=True)` never removes extras, so ONE stray file in the copy
    made the hash differ from the source's again on the next pass — so the cron re-copied
    every 15 minutes forever, and "a pass that changes nothing" was unreachable. Proves
    convergence: pass 1 fixes it, pass 2 is a genuine no-op.
    """
    src = make_skill(tmp_path / "src", "thing")
    p = make_profile(tmp_path / "profiles", "a")
    dest = p / "skills" / "thing"
    r = A.apply_skill(src, dest)
    A.write_manifest(p, "skills/thing", {"hash": r["hash"], "src": str(src)})

    (dest / "stray-note.md").write_text("a local addition")     # the extra file

    first = SY.reconcile_skills(p)
    assert first[0]["action"] == "recopied", first
    assert not (dest / "stray-note.md").exists(), "the extra file must be pruned"

    second = SY.reconcile_skills(p)
    assert second[0]["action"] == "none", second
    assert second[0]["state"] == "In-sync"


def test_a_byproduct_does_not_read_as_drift(tmp_path):
    """`__pycache__` from RUNNING a skill, or a `.DS_Store`, is not a content change.

    Hashing every file meant these made a copy permanently "Local-diverged", so the
    reconcile overwrote the tree every 15 minutes — repeatedly, for nothing.
    """
    src = make_skill(tmp_path / "src", "thing")
    p = make_profile(tmp_path / "profiles", "a")
    dest = p / "skills" / "thing"
    r = A.apply_skill(src, dest)
    A.write_manifest(p, "skills/thing", {"hash": r["hash"], "src": str(src)})

    (dest / "__pycache__").mkdir()
    (dest / "__pycache__" / "mod.cpython-313.pyc").write_bytes(b"\x00\x01")
    (dest / ".DS_Store").write_bytes(b"\x00")

    ops = SY.reconcile_skills(p)
    assert ops[0]["action"] == "none" and ops[0]["state"] == "In-sync", ops


def test_the_reconcile_retains_a_reverse_before_it_overwrites(tmp_path):
    """It is a write, and a write whose reverse is not retained is not reversible."""
    src = make_skill(tmp_path / "src", "thing")
    p = make_profile(tmp_path / "profiles", "a")
    dest = p / "skills" / "thing"
    r = A.apply_skill(src, dest)
    A.write_manifest(p, "skills/thing", {"hash": r["hash"], "src": str(src)})

    (dest / "SKILL.md").write_text("---\nname: thing\n---\nLOCAL EDIT\n")
    ops = SY.reconcile_skills(p)

    snap = ops[0].get("snapshot")
    assert snap, ops
    assert "LOCAL EDIT" in (Path(snap) / "SKILL.md").read_text()
    assert "LOCAL EDIT" not in (dest / "SKILL.md").read_text()


def test_a_skip_is_not_reported_as_a_change(tmp_path):
    """A healthy pass must be distinguishable from a churning one."""
    src = make_skill(tmp_path / "src", "thing")
    p = make_profile(tmp_path / "profiles", "a")
    dest = p / "skills" / "thing"
    r = A.apply_skill(src, dest)
    A.write_manifest(p, "skills/thing", {"hash": r["hash"], "src": str(src)})
    assert SY.reconcile_skills(p)[0]["action"] == "none"


def test_in_sync_entry_reports_none(tmp_path):
    src = make_skill(tmp_path / "src", "thing")
    p = make_profile(tmp_path / "profiles", "a")
    dest = p / "skills" / "thing"
    r = A.apply_skill(src, dest)
    A.write_manifest(p, "skills/thing", {"hash": r["hash"], "src": str(src)})

    ops = SY.reconcile_skills(p)
    assert len(ops) == 1 and ops[0]["action"] == "none" and ops[0]["state"] == "In-sync"


def test_a_moved_source_is_recopied(tmp_path):
    src = make_skill(tmp_path / "src", "thing")
    p = make_profile(tmp_path / "profiles", "a")
    dest = p / "skills" / "thing"
    r = A.apply_skill(src, dest)
    A.write_manifest(p, "skills/thing", {"hash": r["hash"], "src": str(src)})

    (src / "SKILL.md").write_text("---\nname: thing\n---\nv2 CHANGED\n")     # upstream moves
    ops = SY.reconcile_skills(p)
    assert ops[0]["action"] == "recopied" and ops[0]["state"] == "Upstream-moved"
    assert "v2 CHANGED" in (dest / "SKILL.md").read_text()


def test_running_twice_changes_nothing_the_second_time(tmp_path):
    src = make_skill(tmp_path / "src", "thing")
    p = make_profile(tmp_path / "profiles", "a")
    r = A.apply_skill(src, p / "skills" / "thing")
    A.write_manifest(p, "skills/thing", {"hash": r["hash"], "src": str(src)})
    (src / "SKILL.md").write_text("---\nname: thing\n---\nv2\n")

    first = SY.reconcile_skills(p)
    second = SY.reconcile_skills(p)
    assert first[0]["action"] == "recopied"
    assert second[0]["action"] == "none"          # idempotent — the schedule is safe


def test_dry_run_never_writes(tmp_path):
    src = make_skill(tmp_path / "src", "thing")
    p = make_profile(tmp_path / "profiles", "a")
    dest = p / "skills" / "thing"
    r = A.apply_skill(src, dest)
    A.write_manifest(p, "skills/thing", {"hash": r["hash"], "src": str(src)})
    (src / "SKILL.md").write_text("---\nname: thing\n---\nv2\n")

    ops = SY.reconcile_skills(p, dry_run=True)
    assert ops[0]["action"] == "would-recop"
    assert "v1" in (dest / "SKILL.md").read_text()      # untouched


def test_a_vanished_source_is_skipped_not_deleted(tmp_path):
    src = make_skill(tmp_path / "src", "thing")
    p = make_profile(tmp_path / "profiles", "a")
    dest = p / "skills" / "thing"
    r = A.apply_skill(src, dest)
    A.write_manifest(p, "skills/thing", {"hash": r["hash"], "src": str(src)})

    import shutil
    shutil.rmtree(src)
    ops = SY.reconcile_skills(p)
    assert ops[0]["action"] == "skipped"
    assert dest.exists()                                 # never deletes a destination


def test_a_skill_with_a_secret_is_refused_not_forced(tmp_path):
    src = make_skill(tmp_path / "src", "leaky")
    p = make_profile(tmp_path / "profiles", "a")
    r = A.apply_skill(src, p / "skills" / "leaky")
    A.write_manifest(p, "skills/leaky", {"hash": r["hash"], "src": str(src)})

    # the secret arrives upstream after the first copy
    (src / "SKILL.md").write_text("---\nname: leaky\n---\nkey = 'sk-" + "a" * 32 + "'\n")
    ops = SY.reconcile_skills(p)
    assert ops[0]["action"] == "refused"                 # auto-sync is not a bypass
    assert "secret scan blocked" in ops[0]["reason"]


def make_group(tmp_path, **policy):
    g = {"id": "g", "members": ["anchor", "a", "b"],
         "policy": {"soul": "off", "memory": "off", "user": "off", "profile": "off",
                    "anchor": "anchor", "auto": "on", **policy}}
    return g


def test_sync_group_skips_layers_that_are_off(tmp_path):
    root = tmp_path / "profiles"
    for n in ("anchor", "a", "b"):
        make_profile(root, n)
    r = SY.sync_group(make_group(tmp_path), ["anchor", "a", "b"], root, dry_run=False)
    assert r["layers"] == {}                             # nothing enabled, nothing written


def test_sync_group_pushes_only_enabled_layers(tmp_path):
    root = tmp_path / "profiles"
    for n in ("anchor", "a", "b"):
        make_profile(root, n)
    (root / "anchor" / "memories" / "MEMORY.md").write_text("anchor fact\n")

    r = SY.sync_group(make_group(tmp_path, memory="push"), ["anchor", "a", "b"], root,
                      dry_run=False)
    assert "memory" in r["layers"] and "user" not in r["layers"]
    assert (root / "a" / "memories" / "MEMORY.md").read_text().strip() == "anchor fact"


def test_sync_group_does_not_overwrite_the_anchor_with_itself(tmp_path):
    root = tmp_path / "profiles"
    for n in ("anchor", "a"):
        make_profile(root, n)
    (root / "anchor" / "SOUL.md").write_text("# Canonical\n## Loyalty\nbe loyal\n")
    r = SY.sync_group(make_group(tmp_path, soul="whole", soul_source="anchor"),
                      ["anchor", "a"], root, dry_run=False)
    souls = r["layers"]["soul"]["results"]
    assert any(x.get("reason") == "is the anchor" for x in souls)


def test_sync_group_soul_whole_needs_an_anchor(tmp_path):
    root = tmp_path / "profiles"
    for n in ("a", "b"):
        make_profile(root, n)
    r = SY.sync_group(make_group(tmp_path, soul="whole"), ["a", "b"], root, dry_run=False)
    assert r["layers"]["soul"]["action"] == "skipped"
    assert "no anchor" in r["layers"]["soul"]["reason"]


def test_sync_all_only_considers_auto_on_groups(tmp_path):
    root = tmp_path / "profiles"
    for n in ("a", "b"):
        make_profile(root, n)
    on = {"id": "on", "members": ["a"], "policy": {"auto": "on"}}
    off = {"id": "off", "members": ["b"], "policy": {"auto": "off"}}
    r = SY.sync_all([on, off], ["a", "b"], root, dry_run=True)
    assert r["groups_considered"] == 1 and r["groups_total"] == 2
    assert [g["group"] for g in r["groups"]] == ["on"]


def test_append_mode_keeps_the_members_own_extras(tmp_path):
    """The group's anchor guarantees its entries; a member's accumulated extras stay.
    This is the difference between `push` (make identical) and `append` (top up)."""
    root = tmp_path / "profiles"
    for n in ("anchor", "a"):
        make_profile(root, n)
    (root / "anchor" / "memories" / "MEMORY.md").write_text("canon one\n§\ncanon two\n")
    (root / "a" / "memories" / "MEMORY.md").write_text("a's own note\n§\ncanon one\n")

    g = make_group(tmp_path, memory="append")
    SY.sync_group(g, ["anchor", "a"], root, dry_run=False)
    got = [b["text"] for b in __import__("panecore.memory", fromlist=["x"]).read_blocks(root / "a", "memory")["blocks"]]
    assert got == ["canon one", "canon two", "a's own note"]


def test_push_mode_still_replaces_everything(tmp_path):
    root = tmp_path / "profiles"
    for n in ("anchor", "a"):
        make_profile(root, n)
    (root / "anchor" / "memories" / "MEMORY.md").write_text("canon one\n")
    (root / "a" / "memories" / "MEMORY.md").write_text("a's own note\n")

    g = make_group(tmp_path, memory="push")
    SY.sync_group(g, ["anchor", "a"], root, dry_run=False)
    got = [b["text"] for b in __import__("panecore.memory", fromlist=["x"]).read_blocks(root / "a", "memory")["blocks"]]
    assert got == ["canon one"]                          # the extra is gone


def test_append_is_idempotent_across_passes(tmp_path):
    root = tmp_path / "profiles"
    for n in ("anchor", "a"):
        make_profile(root, n)
    (root / "anchor" / "memories" / "MEMORY.md").write_text("canon\n")
    (root / "a" / "memories" / "MEMORY.md").write_text("mine\n")
    g = make_group(tmp_path, memory="append")
    first = SY.sync_group(g, ["anchor", "a"], root, dry_run=False)
    second = SY.sync_group(g, ["anchor", "a"], root, dry_run=False)
    assert first["layers"]["memory"]["results"][0]["action"] == "pushed"
    assert second["layers"]["memory"]["results"][0]["action"] == "unchanged"


def test_sync_all_reports_zero_changes_on_a_settled_fleet(tmp_path):
    root = tmp_path / "profiles"
    for n in ("a", "b"):
        make_profile(root, n)
    r = SY.sync_all([{"id": "on", "members": ["a", "b"], "policy": {"auto": "on"}}],
                    ["a", "b"], root, dry_run=True)
    assert r["changes"] == 0                             # a quiet pass is the healthy outcome


def _for(res, profile):
    return next(x for x in res["results"] if x.get("profile") == profile)


def test_a_layer_push_is_idempotent_across_passes(tmp_path):
    """The reconciler must reach a fixed point. An unchanged rewrite would report a
    change on every pass forever, and 'sync counts nothing' would be meaningless."""
    root = tmp_path / "profiles"
    for n in ("anchor", "a"):
        make_profile(root, n)
    (root / "anchor" / "memories" / "MEMORY.md").write_text("one fact\n")
    (root / "anchor" / "SOUL.md").write_text("# Canonical\n## Care\nbe careful\n")
    g = make_group(tmp_path, memory="push", soul="whole")

    first = SY.sync_group(g, ["anchor", "a"], root, dry_run=False)
    assert _for(first["layers"]["memory"], "a")["action"] == "pushed"
    assert _for(first["layers"]["soul"], "a")["action"] == "wrote"

    second = SY.sync_group(g, ["anchor", "a"], root, dry_run=False)
    assert _for(second["layers"]["memory"], "a")["action"] == "unchanged"
    assert _for(second["layers"]["soul"], "a")["action"] == "unchanged"


def test_the_anchor_is_never_written_to_itself(tmp_path):
    root = tmp_path / "profiles"
    for n in ("anchor", "a"):
        make_profile(root, n)
    (root / "anchor" / "SOUL.md").write_text("# Canonical\n")
    g = make_group(tmp_path, soul="whole")
    res = SY.sync_group(g, ["anchor", "a"], root, dry_run=False)
    anchor_row = _for(res["layers"]["soul"], "anchor")
    assert anchor_row["action"] == "skipped" and anchor_row["reason"] == "is the anchor"
    assert (root / "anchor" / "SOUL.md").read_text() == "# Canonical\n"


def test_a_settled_group_reports_zero_changes_after_applying(tmp_path):
    root = tmp_path / "profiles"
    for n in ("anchor", "a"):
        make_profile(root, n)
    (root / "anchor" / "memories" / "MEMORY.md").write_text("one fact\n")
    g = make_group(tmp_path, memory="push")
    SY.sync_all([g], ["anchor", "a"], root, dry_run=False)
    settled = SY.sync_all([g], ["anchor", "a"], root, dry_run=False)
    assert settled["changes"] == 0


def test_a_corrupt_manifest_is_NOT_replaced(tmp_path):
    """`read_manifest` returned {} on a parse error, so a failed read looked like
    "nothing manifested" — and write_manifest re-reads before writing, so the next copy
    persisted a manifest holding exactly one key. Every other skill then read as
    Untracked and was never reconciled again."""
    src = make_skill(tmp_path / "src", "thing")
    p = make_profile(tmp_path / "profiles", "a")
    dest = p / "skills" / "thing"
    r = A.apply_skill(src, dest)
    A.write_manifest(p, "skills/thing", {"hash": r["hash"], "src": str(src)})

    mf = A.manifest_path(p)
    good = mf.read_text()
    mf.write_text(good[:20])                         # truncated

    try:
        A.write_manifest(p, "skills/other", {"hash": "x", "src": "/y"})
    except A.ManifestCorrupt as e:
        assert "unreadable" in str(e)
    else:
        raise AssertionError("write_manifest must refuse on a corrupt read")

    assert mf.read_text() == good[:20], "the corrupt manifest must be left alone"


def test_the_reconcile_refuses_on_a_corrupt_manifest(tmp_path):
    """It must not copy a skill and then fail to record it — that leaves a copy on disk
    with no manifest entry, untracked forever."""
    src = make_skill(tmp_path / "src", "thing")
    p = make_profile(tmp_path / "profiles", "a")
    A.manifest_path(p).parent.mkdir(parents=True, exist_ok=True)
    A.manifest_path(p).write_text("{ truncated")

    ops = SY.reconcile_skills(p)
    assert ops and ops[0]["action"] == "refused", ops
    assert "unreadable" in ops[0]["reason"]


def test_an_absent_manifest_is_still_empty(tmp_path):
    p = make_profile(tmp_path / "profiles", "a")
    assert A.read_manifest(p) == {}
