"""Tests for panecore.apply — the only module that writes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))

from panecore import apply as A   # noqa: E402


def _skill(root: Path, name: str, body: str = "---\nname: x\n---\n"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body)
    return d


def test_apply_copies_and_reports_hash(tmp_path):
    src = _skill(tmp_path / "lib", "alpha")
    dest = tmp_path / "pa" / "skills" / "alpha"
    r = A.apply_skill(src, dest)
    assert r["ok"] and (dest / "SKILL.md").is_file() and r["hash"]


def test_apply_refuses_diverged_dest_without_force(tmp_path):
    src = _skill(tmp_path / "lib", "alpha")
    dest = _skill(tmp_path / "pa" / "skills", "alpha", "---\nname: x\n---\nLOCALLY EDITED\n")
    r = A.apply_skill(src, dest)
    assert r["ok"] is False and "diverged" in r["reason"]
    assert A.apply_skill(src, dest, force=True)["ok"] is True


def test_not_a_skill_is_refused(tmp_path):
    empty = tmp_path / "lib" / "nope"
    empty.mkdir(parents=True)
    r = A.apply_skill(empty, tmp_path / "pa" / "skills" / "nope")
    assert r["ok"] is False and "not a skill" in r["reason"]


def test_secret_scan_blocks_a_value(tmp_path):
    # The key is BUILT AT RUNTIME, never written as a literal: a test fixture that
    # matches the credential pattern makes every secret scanner flag this tree —
    # including the plugin's own scan_secrets, if the plugin were ever synced as a skill.
    src = _skill(tmp_path / "lib", "leaky", "---\nname: x\n---\nkey: sk-" + "a" * 24 + "\n")
    r = A.apply_skill(src, tmp_path / "pa" / "skills" / "leaky")
    assert r["ok"] is False and r["reason"] == "secret scan blocked"
    assert r["secrets"][0]["kind"] == "openai-style key"


def test_secret_scan_allows_a_reference(tmp_path):
    src = _skill(tmp_path / "lib", "clean", "---\nname: x\n---\nUse $OPENAI_API_KEY from .env\n")
    assert A.scan_secrets(src) == []


def test_drift_three_hash_read():
    assert A.drift(None, "aaa", "aaa") == "Untracked"
    assert A.drift({"hash": "aaa"}, "aaa", "aaa") == "In-sync"
    assert A.drift({"hash": "aaa"}, "aaa", "bbb") == "Local-diverged"
    assert A.drift({"hash": "aaa"}, "bbb", "aaa") == "Upstream-moved"
    assert A.drift({"hash": "aaa"}, "bbb", "ccc") == "Both-diverged"


def test_soul_write_snapshots_the_reverse(tmp_path):
    p = tmp_path / "SOUL.md"
    p.write_text("ORIGINAL")
    r = A.apply_soul(p, "NEW")
    assert Path(r["snapshot"]).read_text() == "ORIGINAL"      # the reverse exists
    assert p.read_text() == "NEW"


def test_manifest_roundtrip(tmp_path):
    pd = tmp_path / "pa"
    pd.mkdir()
    A.write_manifest(pd, "skills/alpha", {"hash": "abc"})
    assert A.read_manifest(pd)["skills/alpha"]["hash"] == "abc"
