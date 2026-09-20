"""Tests for the pane's CRUD surface — backups, rename, and the overlap detector.

The load-bearing invariants here are (1) a backup must NOT quietly contain secrets, and
(2) a restore must itself be reversible.
"""
import importlib.util
import sys
from pathlib import Path

try:
    import pytest
except ImportError:                    # the Hermes venv has FastAPI but no pytest
    pytest = None

if pytest is not None:
    # Under a bare `python3 -m pytest` this module SKIPS rather than failing collection,
    # so the suite stays green either way. Run it with the Hermes venv to exercise it.
    pytest.importorskip("fastapi", reason="router tests need the Hermes venv")

from fastapi import FastAPI                                    # noqa: E402
from fastapi.testclient import TestClient                      # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

spec = importlib.util.spec_from_file_location("ppa", ROOT / "dashboard" / "plugin_api.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)

from panecore import trial as T   # noqa: E402


def build(tmp_path, profiles=("sys", "trad")):
    T.TRIALS = tmp_path / "trials"
    api.trial_mod.TRIALS = T.TRIALS
    api.BACKUPS = tmp_path / "backups"
    for name in profiles:
        (tmp_path / "profiles" / name / "memories").mkdir(parents=True)
        (tmp_path / "profiles" / name / "profile.yaml").write_text(f"description: {name}\n")
    api.HERMES_HOME = tmp_path
    api.PROFILES_ROOT = tmp_path / "profiles"
    api.GROUPS_PATH = tmp_path / "groups.json"
    api.JOURNAL_PATH = tmp_path / "journal.jsonl"
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app), tmp_path


def add_skill(root, profile, rel, body="v1"):
    d = root / "profiles" / profile / "skills" / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {rel.split('/')[-1]}\n---\n{body}\n")
    return d


# ── backups ──────────────────────────────────────────────────────────────────────

def test_backup_excludes_secrets_by_default(tmp_path):
    c, root = build(tmp_path)
    (root / "profiles" / "sys" / ".env").write_text("SECRET=hunter2\n")
    (root / "profiles" / "sys" / "auth.json").write_text('{"token":"x"}\n')

    r = c.post("/backups", json={"profile": "sys"}).json()
    assert r["created"][0]["ok"]
    assert ".env" not in r["created"][0]["included"]
    assert "auth.json" not in r["created"][0]["included"]

    blob = (api.BACKUPS / r["created"][0]["file"]).read_bytes()
    assert b"hunter2" not in blob                       # the value is not in the archive


def test_backup_includes_secrets_only_when_asked(tmp_path):
    c, root = build(tmp_path)
    (root / "profiles" / "sys" / ".env").write_text("SECRET=hunter2\n")
    r = c.post("/backups", json={"profile": "sys", "include_secrets": True}).json()
    assert ".env" in r["created"][0]["included"] and r["created"][0]["secrets"] is True


def test_backup_all_makes_one_archive_per_profile(tmp_path):
    c, root = build(tmp_path)
    r = c.post("/backups", json={"all": True}).json()
    assert sorted(x["profile"] for x in r["created"] if x["ok"]) == ["sys", "trad"]
    assert len(list(api.BACKUPS.glob("*.tar.gz"))) == 2


def test_restore_puts_the_files_back(tmp_path):
    c, root = build(tmp_path)
    soul = root / "profiles" / "sys" / "SOUL.md"
    soul.write_text("# Canonical\n")
    b = c.post("/backups", json={"profile": "sys"}).json()["created"][0]["file"]

    soul.write_text("# MUTATED\n")
    r = c.post("/backups/restore", json={"file": b}).json()
    assert r["ok"] and soul.read_text() == "# Canonical\n"


def test_a_restore_is_itself_reversible(tmp_path):
    """The invariant that makes restore safe to press."""
    c, root = build(tmp_path)
    soul = root / "profiles" / "sys" / "SOUL.md"
    soul.write_text("# Canonical\n")
    b = c.post("/backups", json={"profile": "sys"}).json()["created"][0]["file"]

    soul.write_text("# MUTATED\n")
    r = c.post("/backups/restore", json={"file": b}).json()
    assert soul.read_text() == "# Canonical\n"

    assert c.post("/trial/revert", json={"trial": r["trial"]}).json()["ok"]
    assert soul.read_text() == "# MUTATED\n"            # the restore itself was undone


def test_restore_refuses_a_path_that_escapes_the_profile(tmp_path):
    c, _ = build(tmp_path)
    assert c.post("/backups/restore", json={"file": ".." + "/.." + "/etc" + "/passwd"}).json()["ok"] is False
    assert c.post("/backups/delete", json={"file": "../x.tar.gz"}).json()["ok"] is False


def test_backup_delete_removes_the_archive_only(tmp_path):
    c, root = build(tmp_path)
    r = c.post("/backups", json={"profile": "sys"}).json()
    f = r["created"][0]["file"]
    assert (api.BACKUPS / f).exists()
    assert c.post("/backups/delete", json={"file": f}).json()["ok"]
    assert not (api.BACKUPS / f).exists()
    assert (root / "profiles" / "sys" / "profile.yaml").exists()   # the profile is untouched


# ── rename ───────────────────────────────────────────────────────────────────────

def test_skill_rename_is_universal(tmp_path):
    c, root = build(tmp_path)
    for p in ("sys", "trad"):
        add_skill(root, p, "shared/common")

    r = c.post("/skill-rename", json={"from": "shared/common", "to": "renamed"}).json()
    assert r["ok"] and r["renamed"] == 2
    for p in ("sys", "trad"):
        assert (root / "profiles" / p / "skills" / "shared" / "renamed" / "SKILL.md").is_file()
        assert not (root / "profiles" / p / "skills" / "shared" / "common").exists()


def test_skill_rename_keeps_the_category(tmp_path):
    c, root = build(tmp_path)
    add_skill(root, "sys", "media/torrent-search")
    c.post("/skill-rename", json={"from": "media/torrent-search", "to": "torrent-search-v2"})
    assert (root / "profiles" / "sys" / "skills" / "media" / "torrent-search-v2" / "SKILL.md").is_file()


def test_skill_rename_refuses_a_path_as_the_new_name(tmp_path):
    c, root = build(tmp_path)
    add_skill(root, "sys", "shared/common")
    r = c.post("/skill-rename", json={"from": "shared/common", "to": "other/place"}).json()
    assert r["ok"] is False and "NAME only" in r["error"]


def test_skill_rename_refuses_an_occupied_destination(tmp_path):
    c, root = build(tmp_path)
    add_skill(root, "sys", "shared/common")
    add_skill(root, "sys", "shared/taken")
    r = c.post("/skill-rename", json={"from": "shared/common", "to": "taken"}).json()
    assert r["ok"] is False
    assert (root / "profiles" / "sys" / "skills" / "shared" / "common").exists()   # nothing moved


def test_group_rename_carries_members_and_policy(tmp_path):
    c, _ = build(tmp_path)
    c.post("/groups", json={"id": "old", "members": ["sys", "trad"], "policy": {"anchor": "sys", "soul": "block"}})
    r = c.post("/rename-group", json={"from": "old", "to": "New Name"}).json()
    assert r["ok"] and r["to"] == "new-name"
    g = [x for x in c.get("/groups").json()["groups"] if x["id"] == "new-name"][0]
    assert g["members"] == ["sys", "trad"] and g["policy"]["soul"] == "block"
    assert not [x for x in c.get("/groups").json()["groups"] if x["id"] == "old"]


def test_group_rename_refuses_a_taken_name(tmp_path):
    c, _ = build(tmp_path)
    c.post("/groups", json={"id": "a", "members": []})
    c.post("/groups", json={"id": "b", "members": []})
    assert c.post("/rename-group", json={"from": "a", "to": "b"}).json()["ok"] is False


# ── overlap ──────────────────────────────────────────────────────────────────────

def test_overlap_without_disagreement_is_not_a_conflict(tmp_path):
    c, _ = build(tmp_path)
    c.post("/groups", json={"id": "a", "members": ["sys"], "policy": {"anchor": "sys", "soul": "block"}})
    c.post("/groups", json={"id": "b", "members": ["sys"], "policy": {"anchor": "sys", "soul": "block"}})
    r = c.get("/conflicts").json()
    assert r["multi_group_profiles"] == 1 and r["conflicts"] == []


def test_overlap_with_disagreement_is_reported(tmp_path):
    c, _ = build(tmp_path)
    c.post("/groups", json={"id": "a", "members": ["sys"], "policy": {"anchor": "sys", "soul": "whole"}})
    c.post("/groups", json={"id": "b", "members": ["sys"], "policy": {"anchor": "sys", "soul": "block"}})
    r = c.get("/conflicts").json()
    assert len(r["conflicts"]) == 1
    assert r["conflicts"][0]["profile"] == "sys"
    assert "soul" in r["conflicts"][0]["clashes"]


def test_exactly_one_journal_route_exists():
    """A second route on the same path is served FIRST and silently shadows the real
    one — the pane read oldest-first from a stale handler for exactly this reason."""
    import re
    text = (ROOT / "dashboard" / "plugin_api.py").read_text()
    assert len(re.findall(r'@router\.get\("/journal"\)', text)) == 1
    # METHOD + path is the identity: GET and POST on one path is a route pair, not a
    # collision. Two decorators with the SAME method on the same path shadow each other.
    routes = re.findall(r'@router\.(get|post|delete|put)\("([^"]+)"\)', text)
    dupes = {r for r in routes if routes.count(r) > 1}
    assert not dupes, f"duplicate routes shadow each other: {dupes}"


def test_an_all_off_layer_is_not_a_clash(tmp_path):
    """Only groups that actually ACT on a layer can clash. Off-vs-off, and off-vs-push,
    are not disagreements — one side simply does nothing."""
    c, _ = build(tmp_path)
    c.post("/groups", json={"id": "a", "members": ["sys"], "policy": {"memory": "off"}})
    c.post("/groups", json={"id": "b", "members": ["sys"], "policy": {"memory": "push"}})
    assert c.get("/conflicts").json()["conflicts"] == []

    c.post("/groups", json={"id": "b", "members": ["sys"], "policy": {"memory": "off"}})
    assert c.get("/conflicts").json()["conflicts"] == []


def test_two_groups_acting_differently_IS_a_clash(tmp_path):
    c, _ = build(tmp_path)
    c.post("/groups", json={"id": "a", "members": ["sys"], "policy": {"memory": "push"}})
    c.post("/groups", json={"id": "b", "members": ["sys"], "policy": {"memory": "append"}})
    r = c.get("/conflicts").json()["conflicts"]
    assert len(r) == 1 and r[0]["clashes"]["memory"] == {"a": "push", "b": "append"}


def test_disagreeing_anchors_are_a_clash(tmp_path):
    c, _ = build(tmp_path)
    c.post("/groups", json={"id": "a", "members": ["sys", "trad"], "policy": {"anchor": "sys", "soul": "block"}})
    c.post("/groups", json={"id": "b", "members": ["sys", "trad"], "policy": {"anchor": "trad", "soul": "block"}})
    r = c.get("/conflicts").json()["conflicts"]
    assert r and "anchor" in r[0]["clashes"]


# ── journal ──────────────────────────────────────────────────────────────────────

def test_every_write_lands_in_the_journal(tmp_path):
    c, root = build(tmp_path)
    add_skill(root, "sys", "shared/common")
    c.post("/backups", json={"profile": "sys"})
    c.post("/skill-rename", json={"from": "shared/common", "to": "renamed"})
    ops = [e["op"] for e in c.get("/journal").json()["entries"]]
    assert "backup_create" in ops and "skill_rename" in ops


def test_the_journal_is_newest_first(tmp_path):
    c, root = build(tmp_path)
    c.post("/backups", json={"profile": "sys"})
    add_skill(root, "sys", "shared/common")
    c.post("/skill-rename", json={"from": "shared/common", "to": "renamed"})
    first = c.get("/journal").json()["entries"][0]
    assert first["op"] == "skill_rename"


# ── secrets, through the router ───────────────────────────────────────────────────

def push_secrets_via_trial(c, anchor, members, keys, mode="granular", gid="sec"):
    """Push .env keys the way the PANE does: through /trial with a group policy.

    These tests used to drive `/push-secrets`, a second endpoint for the same operation
    that nothing called. Testing the live path is strictly better — it is the one that
    runs when you press Apply.
    """
    c.post("/groups", json={"id": gid, "members": [anchor, *members],
                            "policy": {"anchor": anchor, "secrets": mode,
                                       "secret_keys": keys}})
    return c.post("/trial", json={
        "group": gid, "profiles": [], "skills": [], "remove": [],
        "commit_policy": {"anchor": anchor, "secrets": mode, "secret_keys": keys},
    }).json()


def sec_rows(res): 
    return [r for r in res.get("results", []) if r.get("layer") == "secrets"]


def test_secrets_endpoint_lists_names_and_never_values(tmp_path):
    c, root = build(tmp_path)
    (root / "profiles" / "sys" / ".env").write_text("BANKR_API_KEY=sk-live-abc\nTMDB_API_KEY=x\n")
    r = c.get("/secrets/sys").json()
    assert r["ok"] and r["count"] == 2
    assert [k["key"] for k in r["keys"]] == ["BANKR_API_KEY", "TMDB_API_KEY"]
    assert "sk-live-abc" not in str(r)          # the value is nowhere in the payload


def test_secrets_endpoint_reports_a_profile_with_no_env(tmp_path):
    c, _ = build(tmp_path)
    r = c.get("/secrets/sys").json()
    assert r["ok"] and r["has_env"] is False and r["keys"] == []


def test_secrets_endpoint_refuses_an_unknown_profile(tmp_path):
    c, _ = build(tmp_path)
    assert c.get("/secrets/nope").json()["ok"] is False


def test_granular_pushes_only_the_selected_keys(tmp_path):
    c, root = build(tmp_path)
    (root / "profiles" / "sys" / ".env").write_text("SHARED=1\nPRIVATE=2\n")
    (root / "profiles" / "trad" / ".env").write_text("TRADS_OWN=3\n")
    push_secrets_via_trial(c, "sys", ["trad"], ["SHARED"])
    text = (root / "profiles" / "trad" / ".env").read_text()
    assert "SHARED=1" in text
    assert "PRIVATE" not in text                # never ticked
    assert "TRADS_OWN=3" in text                # the member keeps its own
def test_a_group_without_an_anchor_pushes_no_secrets(tmp_path):
    c, root = build(tmp_path)
    (root / "profiles" / "sys" / ".env").write_text("X=1\n")
    c.post("/groups", json={"id": "sec", "members": ["sys", "trad"],
                            "policy": {"secrets": "granular", "secret_keys": ["X"]}})
    c.post("/trial", json={"group": "sec", "profiles": [], "skills": [], "remove": []})
    assert not (root / "profiles" / "trad" / ".env").exists(), "no anchor must move nothing"
def test_a_secret_push_is_visible_in_the_results(tmp_path):
    """The pane reports per-layer results; a push that happened must be in them."""
    c, root = build(tmp_path)
    (root / "profiles" / "sys" / ".env").write_text("SHARED=1\n")
    res = push_secrets_via_trial(c, "sys", ["trad"], ["SHARED"])
    rows = sec_rows(res)
    assert rows and rows[0]["mode"] == "granular", res
def test_the_anchor_is_not_pushed_to_itself(tmp_path):
    c, root = build(tmp_path)
    (root / "profiles" / "sys" / ".env").write_text("SHARED=1\n")
    before = (root / "profiles" / "sys" / ".env").read_text()
    push_secrets_via_trial(c, "sys", ["trad"], ["SHARED"])
    assert (root / "profiles" / "sys" / ".env").read_text() == before
def test_a_secret_push_never_puts_a_value_in_the_response(tmp_path):
    c, root = build(tmp_path)
    (root / "profiles" / "sys" / ".env").write_text("TOPSECRET=hunter2\n")
    res = push_secrets_via_trial(c, "sys", ["trad"], ["TOPSECRET"])
    assert "hunter2" not in str(res)
def test_push_mode_copies_EVERY_key_the_anchor_has(tmp_path):
    c, root = build(tmp_path)
    (root / "profiles" / "sys" / ".env").write_text("A=1\nB=2\nC=3\n")
    push_secrets_via_trial(c, "sys", ["trad"], [], mode="push")
    text = (root / "profiles" / "trad" / ".env").read_text()
    assert all(k in text for k in ("A=1", "B=2", "C=3"))
def test_granular_mode_copies_ONLY_the_ticked_keys(tmp_path):
    c, root = build(tmp_path)
    (root / "profiles" / "sys" / ".env").write_text("A=1\nB=2\nC=3\n")
    push_secrets_via_trial(c, "sys", ["trad"], ["B"])
    text = (root / "profiles" / "trad" / ".env").read_text()
    assert "B=2" in text and "A=1" not in text and "C=3" not in text
def test_push_mode_skips_empty_keys(tmp_path):
    """A key declared with no value moves nothing — copying it is noise."""
    c, root = build(tmp_path)
    (root / "profiles" / "sys" / ".env").write_text("FILLED=1\nBLANK=\n")
    push_secrets_via_trial(c, "sys", ["trad"], [], mode="push")
    text = (root / "profiles" / "trad" / ".env").read_text()
    assert "FILLED=1" in text and "BLANK" not in text


def test_trial_body_cannot_enable_secret_movement(tmp_path):
    """Only the persisted group policy can authorize moving credential values."""
    c, root = build(tmp_path)
    (root / "profiles" / "sys" / ".env").write_text("A=1\n")
    c.post("/groups", json={"id": "locked", "members": ["sys", "trad"],
                            "policy": {"anchor": "sys", "secrets": "off"}})
    c.post("/trial", json={"group": "locked", "profiles": [], "skills": [], "remove": [],
                            "secrets": "push", "secret_keys": ["A"]})
    assert not (root / "profiles" / "trad" / ".env").exists()


def test_trial_body_cannot_broaden_granular_secret_keys(tmp_path):
    c, root = build(tmp_path)
    (root / "profiles" / "sys" / ".env").write_text("A=1\nB=2\n")
    c.post("/groups", json={"id": "narrow", "members": ["sys", "trad"],
                            "policy": {"anchor": "sys", "secrets": "granular",
                                       "secret_keys": ["A"]}})
    c.post("/trial", json={"group": "narrow", "profiles": [], "skills": [], "remove": [],
                            "secret_keys": ["B"]})
    text = (root / "profiles" / "trad" / ".env").read_text()
    assert "A=1" in text and "B=2" not in text
def test_the_sync_pass_also_carries_secrets(tmp_path):
    """Auto-sync is the cron's path — secrets must move there too, not only on Apply."""
    c, root = build(tmp_path)
    (root / "profiles" / "sys" / ".env").write_text("A=1\nB=2\n")
    # ONE save: `POST /groups` replaces the whole record, so a second call with only a
    # policy would wipe the members. Send the complete group.
    c.post("/groups", json={"id": "g", "members": ["sys", "trad"],
                            "policy": {"anchor": "sys", "soul": "off",
                                       "secrets": "granular", "secret_keys": ["A"]}})
    out = c.post("/sync", json={"group": "g", "dry_run": False}).json()
    assert out["groups"][0]["layers"]["secrets"]["mode"] == "granular", out
    text = (root / "profiles" / "trad" / ".env").read_text()
    assert "A=1" in text and "B=2" not in text


def test_a_named_group_syncs_even_with_auto_off(tmp_path):
    """'sync now' must do what it says — silence because a switch elsewhere is off is a
    control that lies about what it does."""
    c, root = build(tmp_path)
    (root / "profiles" / "sys" / ".env").write_text("A=1\n")
    c.post("/groups", json={"id": "g", "members": ["sys", "trad"],
                            "policy": {"anchor": "sys", "auto": "off",
                                       "secrets": "granular", "secret_keys": ["A"]}})
    out = c.post("/sync", json={"group": "g", "dry_run": False}).json()
    assert out["groups_considered"] == 1
    assert "A=1" in (root / "profiles" / "trad" / ".env").read_text()


def test_the_scheduled_pass_still_respects_auto_off(tmp_path):
    """The cron names no group, so it must skip a group whose Auto is off."""
    c, root = build(tmp_path)
    (root / "profiles" / "sys" / ".env").write_text("A=1\n")
    c.post("/groups", json={"id": "g", "members": ["sys", "trad"],
                            "policy": {"anchor": "sys", "auto": "off",
                                       "secrets": "granular", "secret_keys": ["A"]}})
    out = c.post("/sync", json={"dry_run": False}).json()          # no group named
    assert out["groups_considered"] == 0
    assert not (root / "profiles" / "trad" / ".env").exists()


def test_saving_a_group_without_members_replaces_the_record(tmp_path):
    """Pinned on purpose: `save` is a whole-record API. The pane always spreads the full
    group, so this is only reachable by a caller that sends a partial one."""
    c, _ = build(tmp_path)
    c.post("/groups", json={"id": "g", "members": ["sys", "trad"]})
    c.post("/groups", json={"id": "g", "policy": {"auto": "on"}})
    assert c.get("/groups").json()["groups"][0]["members"] == []


def test_a_commit_writes_members_and_policy_together(tmp_path):
    """The group record is ONE object. A member must never land in a group whose policy
    is still half-configured — the gap between two writes is a window the cron can fire in."""
    c, _ = build(tmp_path)
    c.post("/groups", json={"id": "g", "members": [], "policy": {"auto": "off"}})
    c.post("/trial", json={"group": "g", "profiles": [], "skills": [], "remove": [],
                           "commit_policy": {"anchor": "sys", "auto": "on"},
                           "commit_members": ["sys", "trad"]})
    g = next(x for x in c.get("/groups").json()["groups"] if x["id"] == "g")
    assert g["members"] == ["sys", "trad"]
    assert g["policy"]["anchor"] == "sys" and g["policy"]["auto"] == "on"


def test_a_commit_of_only_members_leaves_the_policy_alone(tmp_path):
    """Whichever half was not sent is left exactly as it was — never reset to defaults."""
    c, _ = build(tmp_path)
    c.post("/groups", json={"id": "g", "members": [],
                            "policy": {"auto": "on", "anchor": "sys", "secrets": "granular",
                                       "secret_keys": ["A"]}})
    c.post("/trial", json={"group": "g", "profiles": [], "skills": [], "remove": [],
                           "commit_members": ["sys"]})
    g = next(x for x in c.get("/groups").json()["groups"] if x["id"] == "g")
    assert g["members"] == ["sys"]
    assert g["policy"]["auto"] == "on" and g["policy"]["anchor"] == "sys"
    assert g["policy"]["secret_keys"] == ["A"]


def test_a_commit_of_only_policy_leaves_members_alone(tmp_path):
    c, _ = build(tmp_path)
    c.post("/groups", json={"id": "g", "members": ["sys", "trad"], "policy": {"auto": "off"}})
    c.post("/trial", json={"group": "g", "profiles": [], "skills": [], "remove": [],
                           "commit_policy": {"auto": "on"}})
    g = next(x for x in c.get("/groups").json()["groups"] if x["id"] == "g")
    assert g["members"] == ["sys", "trad"]
    assert g["policy"]["auto"] == "on"


def test_the_group_record_commit_is_undoable(tmp_path):
    """Committing membership is as reversible as anything else the pane writes."""
    c, _ = build(tmp_path)
    c.post("/groups", json={"id": "g", "members": [], "policy": {"auto": "off"}})
    r = c.post("/trial", json={"group": "g", "profiles": [], "skills": [], "remove": [],
                               "commit_members": ["sys", "trad"]}).json()
    assert next(x for x in c.get("/groups").json()["groups"] if x["id"] == "g")["members"] == ["sys", "trad"]
    c.post("/trial/revert", json={"trial": r["trial"]})
    assert next(x for x in c.get("/groups").json()["groups"] if x["id"] == "g")["members"] == []


def test_a_read_of_groups_cannot_write_into_the_record(tmp_path):
    """A GET must not be able to plant a field the pane then saves back.

    `/groups` returned a literal `"**"` key (intending a spread), the pane spread the
    group into its save body, and the junk was PERSISTED. The guard is that the API's
    group shape is exactly the record's shape plus the read-only `resolved`.
    """
    c, _ = build(tmp_path)
    c.post("/groups", json={"id": "g", "members": ["sys"], "policy": {"auto": "on"}})
    g = c.get("/groups").json()["groups"][0]
    assert set(g) <= {"id", "members", "policy", "resolved"}, set(g) - {"id", "members", "policy", "resolved"}

    # now do exactly what the pane does: spread it back minus the computed field
    c.post("/groups", json={k: v for k, v in g.items() if k != "resolved"})
    saved = c.get("/groups").json()["groups"][0]
    assert set(saved) <= {"id", "members", "policy", "resolved"}, saved


def test_the_saved_policy_holds_only_known_keys(tmp_path):
    """A legacy key (`user_profile`) survived in the stored record read by nothing.
    Whatever is written must be a key the code actually consults."""
    from panecore import groups as G
    c, _ = build(tmp_path)
    c.post("/groups", json={"id": "g", "members": ["sys"]})
    pol = c.get("/groups").json()["groups"][0]["policy"]
    assert set(pol) <= set(G.DEFAULT_POLICY), set(pol) - set(G.DEFAULT_POLICY)


def test_skill_copies_are_PAIRED_not_a_cross_product(tmp_path):
    """Staging SkillA→X and SkillB→Y must copy exactly TWO things, not four.

    The request carried two flat sets (`profiles`, `skills`) and the server multiplied
    them, so every skill staged while browsing landed on every profile touched. The pane
    displayed a per-row target the payload could not express.
    """
    c, root = build(tmp_path, profiles=("sys", "trad", "third", "fourth"))
    add_skill(root, "sys", "shared/alpha")     # source of alpha
    add_skill(root, "trad", "shared/beta")     # source of beta
    add_skill(root, "third", "shared/seed")    # placeholders so the profile dirs exist
    add_skill(root, "fourth", "shared/seed")

    c.post("/trial", json={
        "pairs": [{"profile": "third", "skill": "shared/alpha", "op": "add"},
                  {"profile": "fourth", "skill": "shared/beta", "op": "add"}],
        "profiles": ["third", "fourth"],
    })

    assert (root / "profiles/third/skills/shared/alpha").exists()
    assert (root / "profiles/fourth/skills/shared/beta").exists()
    # the cross-product would have put these two here as well
    assert not (root / "profiles/third/skills/shared/beta").exists()
    assert not (root / "profiles/fourth/skills/shared/alpha").exists()


def test_the_plan_reports_the_same_pairs_the_trial_would_apply(tmp_path):
    """A preview that counts differently from the apply it guards is worse than none."""
    c, root = build(tmp_path, profiles=("sys", "trad", "third", "fourth"))
    add_skill(root, "sys", "shared/alpha")
    add_skill(root, "trad", "shared/beta")
    add_skill(root, "third", "shared/seed")
    add_skill(root, "fourth", "shared/seed")
    pairs = [{"profile": "third", "skill": "shared/alpha", "op": "add"},
             {"profile": "fourth", "skill": "shared/beta", "op": "add"}]
    plan = c.post("/plan", json={"pairs": pairs, "profiles": ["third", "fourth"]}).json()
    assert len(plan["actions"]) == 2, plan
    assert {(a["profile"], a["skill"]) for a in plan["actions"]} == {
        ("third", "shared/alpha"), ("fourth", "shared/beta")}


def test_a_memory_edit_is_undoable(tmp_path):
    """MEMORY.md is the user's own durable facts, hand-edited through a textarea — and
    it was the ONE write in the pane with no way back. Every other write is trial-backed;
    this route already took a snapshot and the pane threw it away."""
    c, root = build(tmp_path)
    f = root / "profiles" / "sys" / "memories" / "MEMORY.md"
    f.write_text("the original fact\n")

    r = c.post("/memory", json={"profile": "sys", "which": "memory",
                                "blocks": ["a rewritten fact"]}).json()
    assert r["ok"] and r["trial"], r
    assert "a rewritten fact" in f.read_text()

    rev = c.post("/trial/revert", json={"trial": r["trial"]}).json()
    assert rev["ok"], rev
    assert f.read_text() == "the original fact\n", "the exact prior bytes must come back"


def test_a_description_edit_is_undoable(tmp_path):
    c, root = build(tmp_path)
    y = root / "profiles" / "sys" / "profile.yaml"
    y.write_text('description: the original\n')
    r = c.post("/profile-entry", json={"profile": "sys",
                                       "description": "a new description"}).json()
    assert r["ok"] and r["trial"], r
    c.post("/trial/revert", json={"trial": r["trial"]})
    assert "the original" in y.read_text()


def test_an_edit_that_changes_nothing_starts_no_trial(tmp_path):
    """A no-op must not manufacture an undo — an empty trial is a Revert button that
    undoes nothing, which is worse than no button."""
    c, root = build(tmp_path)
    f = root / "profiles" / "sys" / "memories" / "MEMORY.md"
    f.write_text("same\n")
    r = c.post("/memory", json={"profile": "sys", "which": "memory", "blocks": ["same"]}).json()
    assert r.get("unchanged") and not r.get("trial"), r


def test_restore_refuses_an_archive_member_that_traverses(tmp_path):
    """The old guard tested only `member.split('/')[0]`, so `SOUL.md/../../x` PASSED it
    (its top segment is SOUL.md) and then escaped — while its error string claimed a
    check that had never run."""
    import tarfile
    c, root = build(tmp_path)
    evil = tmp_path / "backups"
    evil.mkdir(parents=True, exist_ok=True)
    api.BACKUPS = evil
    payload = tmp_path / "PWNED.txt"
    payload.write_text("escaped")

    with tarfile.open(evil / "sys--20260101.tar.gz", "w:gz") as tf:
        tf.add(payload, arcname="SOUL.md/" + "../" * 6 + "tmp/PWNED.txt")

    r = c.post("/backups/restore", json={"file": "sys--20260101.tar.gz",
                                         "profile": "sys"}).json()
    assert r["ok"] is False, r
    assert "refusing" in r["error"], r


def test_restore_refuses_a_link_member(tmp_path):
    """A symlink member satisfies any name check and then redirects every later write."""
    import tarfile
    c, root = build(tmp_path)
    evil = tmp_path / "backups"
    evil.mkdir(parents=True, exist_ok=True)
    api.BACKUPS = evil
    with tarfile.open(evil / "sys--20260102.tar.gz", "w:gz") as tf:
        info = tarfile.TarInfo("SOUL.md")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc" + "/passwd"
        info.size = 0
        tf.addfile(info)

    r = c.post("/backups/restore", json={"file": "sys--20260102.tar.gz",
                                         "profile": "sys"}).json()
    assert r["ok"] is False and "link" in r["error"], r


def test_a_refused_restore_leaves_no_live_trial(tmp_path):
    """A refusal must leave no trace — the old order opened a trial FIRST, so a rejected
    archive left a Revert button that undid nothing real."""
    import tarfile
    c, root = build(tmp_path)
    evil = tmp_path / "backups"
    evil.mkdir(parents=True, exist_ok=True)
    api.BACKUPS = evil
    payload = tmp_path / "x.txt"
    payload.write_text("y")
    with tarfile.open(evil / "sys--20260103.tar.gz", "w:gz") as tf:
        tf.add(payload, arcname="not-a-blessed-member")

    before = c.get("/trial/live").json()
    c.post("/backups/restore", json={"file": "sys--20260103.tar.gz", "profile": "sys"})
    after = c.get("/trial/live").json()
    assert (before.get("trial") is None) == (after.get("trial") is None), (before, after)



def test_profiles_lists_default_exactly_once(tmp_path):
    """The ROOT profile is named `default` and its home is HERMES_HOME — NOT
    <profiles>/default. Walking the directory produced a SECOND `default` row: an empty
    phantom beside the real one. And since profile_dir() returns the FIRST match, a
    duplicate made every write's destination depend on iteration order."""
    c, root = build(tmp_path)
    (root / "profiles" / "default").mkdir(parents=True, exist_ok=True)   # the phantom
    (root / "profiles" / "default" / ".env").write_text("MATRIX_HOME_ROOM=!x:y\n")
    names = [p["name"] for p in c.get("/profiles").json()["profiles"]]
    assert names.count("default") == 1, names
    # and it must resolve to the ROOT, not to the phantom
    assert c.get("/profiles").json()["profiles"][0]["path"] == str(tmp_path)


def test_a_group_containing_default_syncs_to_the_root(tmp_path):
    """sync.py walked `profiles_root / name` for EVERY profile, so a group containing
    `default` wrote into <profiles>/default/ — a phantom the root never reads, while the
    Apply button (which resolves through the profile list) wrote to the right place. The
    scheduled path and the button disagreed about where `default` lives."""
    import sys
    sys.path.insert(0, str(ROOT / "dashboard"))
    from panecore import sync as SY
    c, root = build(tmp_path)
    api.sync_mod.DEFAULT_HOME = root                      # the "Hermes root"
    (root / "profiles" / "sys" / ".env").write_text("A=1\n")
    (root / ".env").write_text("OLD=1\n")
    c.post("/groups", json={"id": "g", "members": ["sys", "default"],
                            "policy": {"anchor": "sys", "secrets": "granular",
                                       "secret_keys": ["A"], "auto": "on"}})
    c.post("/sync", json={"dry_run": False})

    assert "A=1" in (root / ".env").read_text(), "default's home is the ROOT"
    phantom = root / "profiles" / "default" / ".env"
    assert not phantom.exists(), f"wrote to the phantom at {phantom}"


def test_undo_names_what_it_would_reverse(tmp_path):
    """`/trial/live` used to answer with a COUNT — `ops: len(...)` — collapsing the label
    and every op. So the Commits view could only say "3 file(s)", and the principal was
    asked to press Undo with no way to see what it would put back.

    The route must now name what it reverses: the paths, whether each is restored or
    removed, and the label that started the act."""
    import sys
    sys.path.insert(0, str(ROOT / "dashboard"))
    from panecore import trial as T
    c, root = build(tmp_path)

    # a real trial with a real write, recorded the way the pane records one
    tid = T.begin(label="apply 2 skills")
    # a path that did NOT exist before: no snapshot to take, and Undo's reverse is to
    # REMOVE it — which is exactly why `existed` has to reach the pane.
    target = root / "profiles" / "sys" / "skills" / "demo" / "SKILL.md"
    T.record(tid, {"kind": "file", "path": str(target), "existed": False, "snapshot": None})
    # a path that DID exist: backed up, and Undo restores it from that snapshot
    other = root / "profiles" / "sys" / "profile.yaml"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("description: x\n")
    snap2 = T.backup_file(tid, other, 1)
    T.record(tid, {"kind": "file", "path": str(other), "existed": True, "snapshot": snap2})

    r = c.get("/trial/live").json()
    assert r["live"] is True
    assert r["label"] == "apply 2 skills", f"the label must survive: {r.get('label')!r}"
    assert r["total"] == 2, f"two distinct paths, got {r['total']}"
    assert len(r["what"]) == 2

    by_name = {w["name"]: w for w in r["what"]}
    assert "SKILL.md" in by_name and "profile.yaml" in by_name
    # `existed: False` means Undo REMOVES the path — the distinction the UI renders
    assert by_name["SKILL.md"]["existed"] is False, "a new file must be marked as removed-on-undo"
    assert by_name["profile.yaml"]["existed"] is True, "an overwritten file must be marked restore"

    # the path identifies the target. Under a sandbox root (as in tests) it stays absolute;
# under $HOME it is shown with `~`. Either way it must END with the real relative tail.
    assert by_name["profile.yaml"]["path"].endswith("profiles/sys/profile.yaml"), \
        by_name["profile.yaml"]["path"]
    assert by_name["SKILL.md"]["path"].endswith("skills/demo/SKILL.md"), \
        by_name["SKILL.md"]["path"]
    # and no snapshot PATH leaks a content value — this asserts the shape, not the value
    assert "snapshot" not in by_name["profile.yaml"], "the response must not carry snapshot paths"

    # a duplicate path (a dir op plus its inner file) must collapse to one entry
    T.record(tid, {"kind": "file", "path": str(other), "existed": True, "snapshot": snap2})
    r2 = c.get("/trial/live").json()
    assert r2["total"] == 2, f"a repeated path must not be listed twice, got {r2['total']}"


def test_undo_state_is_empty_and_honest_when_nothing_is_pending(tmp_path):
    """No live trial must read as absent, not as a zero-count that looks like a bug."""
    c, _ = build(tmp_path)
    r = c.get("/trial/live").json()
    assert r["live"] is False and r["trial"] is None
    assert r["ops"] == 0 and r["what"] == [] and r["total"] == 0
    assert "label" in r


def test_one_item_shape_for_every_layer(tmp_path):
    """The incoherence this replaced: soul had a section checklist and secrets had a key
    checklist, while memory / user / profile were on/off switches over an entire file.
    `/items/{layer}/{name}` answers "what can be picked?" the SAME way for all five, so the
    panel can render one grammar instead of five widgets."""
    c, root = build(tmp_path)
    P = root / "profiles" / "sys"
    P.mkdir(parents=True, exist_ok=True)
    (P / "memories").mkdir(exist_ok=True)
    (P / "memories" / "MEMORY.md").write_text("one\n\u00a7\ntwo\n")
    (P / "memories" / "USER.md").write_text("about me\n")
    (P / ".env").write_text("A_KEY=1\nB_KEY=2\n")

    seen_units = set()
    for layer in ("memory", "user", "profile", "secrets", "soul"):
        r = c.get(f"/items/{layer}/sys").json()
        assert r["ok"] is True, f"{layer}: {r.get('error')}"
        # the SAME envelope for every layer — that is the point
        assert set(r) >= {"ok", "layer", "profile", "unit", "items", "count"}, (layer, set(r))
        assert r["layer"] == layer and r["profile"] == "sys"
        assert r["count"] == len(r["items"])
        assert r["unit"] and isinstance(r["unit"], str)
        seen_units.add(r["unit"])
        for it in r["items"]:
            assert set(it) >= {"key", "label", "preview", "chars", "meta"}, (layer, set(it))
            assert it["key"], f"{layer}: an item with no key cannot be picked"

    # each layer names what one item IS, so the panel can label a list without knowing the layer
    assert seen_units == {"block", "field", "key", "section"}, seen_units

    # memory items are the real blocks, and the keys are distinct + content-derived
    mem = c.get("/items/memory/sys").json()
    assert mem["count"] == 2, mem["count"]
    assert len({i["key"] for i in mem["items"]}) == 2

    # an unknown layer refuses rather than returning an empty success
    bad = c.get("/items/nonsense/sys").json()
    assert bad["ok"] is False and "unknown layer" in bad["error"]

    # a missing profile refuses too
    gone = c.get("/items/memory/no-such-profile").json()
    assert gone["ok"] is False and gone["items"] == []


def test_block_edit_edits_one_block_and_refuses_a_stale_key(tmp_path):
    """/block addresses ONE block by content key, so a client never has to send the whole
    file back. The load-bearing case is the stale key: the caller asked to change a specific
    block, and silently changing a DIFFERENT one is the single outcome they cannot detect."""
    import sys
    sys.path.insert(0, str(ROOT / "dashboard"))
    import plugin_api as api
    c, root = build(tmp_path)
    P = root / "profiles" / "sys"
    (P / "memories").mkdir(parents=True, exist_ok=True)
    f = P / "memories" / "MEMORY.md"
    f.write_text("alpha\n\u00a7\nbeta\n\u00a7\ngamma\n")

    def texts():
        return [b["text"] for b in api.mem_mod.read_blocks(P, "memory")["blocks"]]

    def keyof(t):
        return next(b["key"] for b in api.mem_mod.read_blocks(P, "memory")["blocks"] if b["text"] == t)

    # edit — and it registers a trial, so Undo last covers a hand-edit of durable facts
    r = c.post("/block", json={"profile": "sys", "which": "memory",
                               "key": keyof("beta"), "text": "BETA"}).json()
    assert r["ok"] is True and texts() == ["alpha", "BETA", "gamma"], texts()
    assert r["trial"], "an edit to the durable facts must be undoable"

    # append (key null)
    r = c.post("/block", json={"profile": "sys", "which": "memory", "key": None, "text": "delta"}).json()
    assert r["ok"] is True and texts() == ["alpha", "BETA", "gamma", "delta"]

    # delete (text null)
    r = c.post("/block", json={"profile": "sys", "which": "memory", "key": keyof("alpha"), "text": None}).json()
    assert r["ok"] is True and texts() == ["BETA", "gamma", "delta"]

    # A STALE KEY MUST REFUSE — never silently edit a neighbour
    before = texts()
    r = c.post("/block", json={"profile": "sys", "which": "memory", "key": "deadbeef0000", "text": "X"}).json()
    assert r["ok"] is False, "a key that resolves to nothing must not be treated as an append"
    assert texts() == before, "a refused edit must leave the file byte-identical"

    # an emptied block is refused — delete it instead
    r = c.post("/block", json={"profile": "sys", "which": "memory", "key": keyof("delta"), "text": "   "}).json()
    assert r["ok"] is False and "delete it instead" in r["error"]

    # a no-op edit is unchanged and starts NO trial (a no-op write would litter a snapshot)
    r = c.post("/block", json={"profile": "sys", "which": "memory", "key": keyof("delta"), "text": "delta"}).json()
    assert r["ok"] is True and r.get("unchanged") is True and not r.get("trial")

    # a bad layer refuses
    r = c.post("/block", json={"profile": "sys", "which": "soul", "key": "x", "text": "y"}).json()
    assert r["ok"] is False and "which must be" in r["error"]


def test_scope_and_mode_are_orthogonal_axes(tmp_path):
    """The coherence fix: HOW a layer applies (off/push/append) and WHICH items it applies
    to (all/pick) are two independent axes, not one ladder of combined states.

    Backward compatibility is the load-bearing assertion: a record written before
    `<layer>_scope` existed carries no such key, must default to `all`, and must behave
    exactly as it did — adding granularity must not change the meaning of stored records."""
    import sys
    sys.path.insert(0, str(ROOT / "dashboard"))
    from panecore import sync as SY, memory as M
    c, root = build(tmp_path)
    api.sync_mod.DEFAULT_HOME = root
    A = root / "profiles" / "a"
    B = root / "profiles" / "b"
    for d in (A, B):
        (d / "memories").mkdir(parents=True, exist_ok=True)
    (A / "memories" / "MEMORY.md").write_text("a-one\n\u00a7\na-two\n\u00a7\na-three\n")
    (B / "memories" / "MEMORY.md").write_text("b-own\n")

    keys = [b["key"] for b in M.read_blocks(A, "memory")["blocks"]]

    def b_blocks():
        return [b["text"] for b in M.read_blocks(B, "memory")["blocks"]]

    def run(policy):
        (B / "memories" / "MEMORY.md").write_text("b-own\n")
        c.post("/groups", json={"id": "g", "members": ["a", "b"], "policy": policy})
        # Name the group. Without it `/sync` runs EVERY group and filters to those with
        # `auto: on` — which this one is not, so it would silently do nothing and every
        # assertion below would "pass" against an untouched file.
        c.post("/sync", json={"group": "g", "dry_run": False})
        return b_blocks()

    # (1) NO scope key at all -> behaves exactly as before this feature existed
    assert run({"anchor": "a", "memory": "push"}) == ["a-one", "a-two", "a-three"]
    assert run({"anchor": "a", "memory": "append"}) == ["a-one", "a-two", "a-three", "b-own"]

    # (2) scope=pick + push -> the member ends up holding EXACTLY the chosen items
    assert run({"anchor": "a", "memory": "push", "memory_scope": "pick",
                "memory_picks": [keys[1]]}) == ["a-two"]

    # (3) scope=pick + append -> the chosen items are guaranteed; the member keeps its own
    assert run({"anchor": "a", "memory": "append", "memory_scope": "pick",
                "memory_picks": [keys[0], keys[2]]}) == ["a-one", "a-three", "b-own"]

    # (4) an EMPTY pick list is a real answer, not a synonym for All
    assert run({"anchor": "a", "memory": "push", "memory_scope": "pick",
                "memory_picks": []}) == []

    # (5) a stale key matches nothing and corrupts nothing
    assert run({"anchor": "a", "memory": "push", "memory_scope": "pick",
                "memory_picks": ["deadbeef0000"]}) == []

    # (6) scope All is unaffected by a leftover pick list
    assert run({"anchor": "a", "memory": "push", "memory_scope": "all",
                "memory_picks": [keys[0]]}) == ["a-one", "a-two", "a-three"]


def test_each_block_edit_returns_its_own_trial(tmp_path):
    """The pane now STAGES several block edits and commits them in one Apply. Each one is a
    separate /block call, so each records its OWN trial — and the pane collects those ids so
    one Undo can reverse the whole Apply.

    That only works if the ids are DISTINCT. If two edits shared a trial id, reverting the
    second would already have spent it and the remaining edits would survive an Undo the
    user believed covered everything. This is the invariant the client relies on."""
    import sys
    sys.path.insert(0, str(ROOT / "dashboard"))
    import plugin_api as api
    c, root = build(tmp_path)
    P = root / "profiles" / "sys"
    (P / "memories").mkdir(parents=True, exist_ok=True)
    (P / "memories" / "MEMORY.md").write_text("one\n\u00a7\ntwo\n\u00a7\nthree\n")

    def keyof(t):
        return next(b["key"] for b in api.mem_mod.read_blocks(P, "memory")["blocks"] if b["text"] == t)

    # three separate edits, as an Apply-with-staged-blocks produces
    t1 = c.post("/block", json={"profile": "sys", "which": "memory", "key": keyof("one"), "text": "ONE"}).json()["trial"]
    t2 = c.post("/block", json={"profile": "sys", "which": "memory", "key": keyof("two"), "text": "TWO"}).json()["trial"]
    t3 = c.post("/block", json={"profile": "sys", "which": "memory", "key": keyof("three"), "text": None}).json()["trial"]

    assert t1 and t2 and t3, "every block edit must be undoable"
    assert len({t1, t2, t3}) == 3, "each edit must get its OWN trial, not a shared one"

    # and each is independently revertible
    r = c.post("/trial/revert", json={"trial": t3}).json()
    assert r["ok"] is True, r
    texts = [b["text"] for b in api.mem_mod.read_blocks(P, "memory")["blocks"]]
    assert "three" in texts, "reverting the delete restores the block"


if __name__ == "__main__":
    # A pytest-free runner, so these routes can be exercised under the Hermes venv
    # (which has FastAPI but not pytest):
    #     ~/.hermes/hermes-agent/venv/bin/python tests/test_crud.py
    #
    # It MUST stay the last block in this file: it snapshots globals() to find the tests,
    # so any test defined BELOW it exists, passes on its own, and is never run.
    import tempfile
    import traceback

    cases = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]

    # Guard the runner's own position. This block snapshots globals(), so any test defined
    # BELOW it is defined after the snapshot: it exists, passes when called directly, and is
    # never run here. The count drifts one short and nothing says so. Comparing what was
    # discovered against what the file actually declares turns that silent gap into a failure.
    import re as _re
    _src = Path(__file__).read_text()
    _declared = len(_re.findall(r"^def (test_\w+)", _src, _re.M))
    if len(cases) != _declared:
        print(f"  FAIL  runner position: found {len(cases)} tests, the file declares {_declared}.")
        print("        A test is defined BELOW `if __name__ == \"__main__\":` — move it above.")
        raise SystemExit(1)
    passed = failed = 0
    for name, fn in cases:
        try:
            # pass a temp dir only to the cases that declare one
            takes_arg = bool(getattr(fn, "__code__", None) and fn.__code__.co_argcount)
            fn(Path(tempfile.mkdtemp(prefix="pane-crud-"))) if takes_arg else fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as e:                                      # noqa: BLE001
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
