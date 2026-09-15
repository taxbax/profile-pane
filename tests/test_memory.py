"""Tests for panecore.memory — block files + the profile entry."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))

from panecore import memory as M   # noqa: E402


def test_merge_blocks_puts_the_anchors_entries_first():
    got = M.merge_blocks(["mine a", "mine b"], ["canon one"])
    assert got == ["canon one", "mine a", "mine b"]


def test_merge_blocks_keeps_the_members_extras():
    got = M.merge_blocks(["my own note"], ["canon one", "canon two"])
    assert got == ["canon one", "canon two", "my own note"]


def test_merge_blocks_drops_the_members_copy_of_an_anchor_entry():
    got = M.merge_blocks(["canon one", "my own note"], ["canon one"])
    assert got == ["canon one", "my own note"]          # no duplicate


def test_merge_blocks_is_idempotent():
    once = M.merge_blocks(["mine"], ["canon"])
    twice = M.merge_blocks(once, ["canon"])
    assert once == twice == ["canon", "mine"]


def test_merge_blocks_with_an_empty_member_is_just_the_anchor():
    assert M.merge_blocks([], ["a", "b"]) == ["a", "b"]


def test_split_blocks_handles_the_live_format():
    # verified against the real file: blocks separated by a LONE § line
    assert M.split_blocks("alpha\n\u00a7\nbeta\n\u00a7\ngamma\n") == ["alpha", "beta", "gamma"]


def test_split_blocks_tolerates_a_leading_separator():
    assert M.split_blocks("\u00a7\nalpha\n\u00a7\nbeta") == ["alpha", "beta"]


def test_read_blocks_missing_file(tmp_path):
    r = M.read_blocks(tmp_path, "memory")
    assert r["ok"] and r["exists"] is False and r["blocks"] == []


def test_write_then_read_roundtrip(tmp_path):
    (tmp_path / "memories").mkdir()
    M.write_blocks(tmp_path, "memory", ["one", "two", "three"])
    r = M.read_blocks(tmp_path, "memory")
    assert [b["text"] for b in r["blocks"]] == ["one", "two", "three"]


def test_write_snapshots_when_the_file_exists(tmp_path):
    (tmp_path / "memories").mkdir()
    p = tmp_path / "memories" / "MEMORY.md"
    p.write_text("ORIGINAL\n")
    r = M.write_blocks(tmp_path, "memory", ["NEW"])
    assert Path(r["snapshot"]).read_text().strip() == "ORIGINAL"   # the reverse
    assert p.read_text().strip() == "NEW"


def test_write_records_creation_when_absent(tmp_path):
    r = M.write_blocks(tmp_path, "user", ["only"])
    assert r["created"] is True and r["snapshot"] is None


def test_blank_blocks_are_dropped(tmp_path):
    M.write_blocks(tmp_path, "memory", ["a", "   ", "", "b"])
    assert [b["text"] for b in M.read_blocks(tmp_path, "memory")["blocks"]] == ["a", "b"]


def test_profile_entry_reads_a_simple_scalar(tmp_path):
    (tmp_path / "profile.yaml").write_text("description: A short one.\nui_meta:\n  x: 1\n")
    assert M.read_profile_entry(tmp_path)["description"] == "A short one."


def test_profile_entry_reads_a_wrapped_scalar(tmp_path):
    (tmp_path / "profile.yaml").write_text(
        "description: Trader Bot — operates\n  the control plane.\nui_meta:\n  x: 1\n")
    assert M.read_profile_entry(tmp_path)["description"].startswith("Trader Bot")


def test_profile_write_preserves_every_other_key(tmp_path):
    p = tmp_path / "profile.yaml"
    p.write_text("description: old\nui_meta:\n  hermes-bots:\n    title: X\n"
                 "_ui_meta_revisions:\n  hermes-bots: 2\n")
    M.write_profile_entry(tmp_path, "NEW TEXT")
    out = p.read_text()
    assert "NEW TEXT" in out
    assert "old" not in out
    # the gateway's own bookkeeping must survive byte-for-byte
    assert "hermes-bots" in out and "title: X" in out and "_ui_meta_revisions" in out


def test_profile_entry_joins_a_wrapped_quoted_scalar(tmp_path):
    """The live shape: a description that wraps across indented lines with the closing
    quote on the last one. Reading only line 1 leaves a dangling open-quote."""
    (tmp_path / "profile.yaml").write_text(
        "description: 'Media Control: manages the torrent pipeline (qBittorrent +\n"
        "  Sonarr + Prowlarr over the IVPN tunnel) and the Emby library.'\n"
        "description_auto: false\nui_meta:\n  x: 1\n")
    r = M.read_profile_entry(tmp_path)
    assert r["description"].startswith("Media Control: manages")
    assert r["description"].endswith("the Emby library.")
    assert "'" not in r["description"]          # no stray quote anywhere


def test_profile_entry_joins_a_wrapped_plain_scalar(tmp_path):
    (tmp_path / "profile.yaml").write_text(
        "description: Trader Bot — operates the control plane\n  (Bankr/Avantis/Polymarket).\n"
        "ui_meta:\n  x: 1\n")
    assert M.read_profile_entry(tmp_path)["description"] == \
        "Trader Bot — operates the control plane (Bankr/Avantis/Polymarket)."


def test_profile_entry_strips_yaml_single_quotes(tmp_path):
    (tmp_path / "profile.yaml").write_text("description: 'Media Control: runs the pipeline'\n")
    assert M.read_profile_entry(tmp_path)["description"] == "Media Control: runs the pipeline"


def test_profile_entry_strips_yaml_double_quotes(tmp_path):
    (tmp_path / "profile.yaml").write_text('description: "Trader Bot: operates the plane"\n')
    assert M.read_profile_entry(tmp_path)["description"] == "Trader Bot: operates the plane"


def test_profile_entry_reads_a_literal_block_scalar(tmp_path):
    (tmp_path / "profile.yaml").write_text("description: |\n  line one\n  line two\nui_meta:\n  x: 1\n")
    assert M.read_profile_entry(tmp_path)["description"] == "line one\nline two"


def test_profile_entry_reads_a_folded_block_scalar(tmp_path):
    (tmp_path / "profile.yaml").write_text("description: >\n  folded one\n  folded two\nother: 1\n")
    assert M.read_profile_entry(tmp_path)["description"] == "folded one folded two"


def test_profile_entry_reports_a_missing_key(tmp_path):
    (tmp_path / "profile.yaml").write_text("ui_meta:\n  x: 1\n")
    r = M.read_profile_entry(tmp_path)
    assert r["has_key"] is False and r["description"] == ""


def test_profile_write_quotes_a_value_that_needs_it(tmp_path):
    """A round-trip must survive: written then read back identical."""
    p = tmp_path / "profile.yaml"
    p.write_text("description: old\n")
    for text in ["Media Control: runs the pipeline", "has 'quotes' inside", "starts with - dash"]:
        M.write_profile_entry(tmp_path, text)
        assert M.read_profile_entry(tmp_path)["description"] == text


def test_profile_write_replaces_a_block_scalar_with_a_scalar(tmp_path):
    p = tmp_path / "profile.yaml"
    p.write_text("description: |\n  line one\n  line two\nui_meta:\n  x: 1\n")
    M.write_profile_entry(tmp_path, "now a single line")
    out = p.read_text()
    assert "line two" not in out                      # the whole block went
    assert "ui_meta" in out and "x: 1" in out         # other keys survived
    assert M.read_profile_entry(tmp_path)["description"] == "now a single line"


def test_profile_write_is_a_noop_when_unchanged(tmp_path):
    p = tmp_path / "profile.yaml"
    p.write_text("description: same\nui_meta:\n  x: 1\n")
    before = p.stat().st_mtime_ns
    r = M.write_profile_entry(tmp_path, "same")
    assert r["unchanged"] is True and r["snapshot"] is None
    assert p.stat().st_mtime_ns == before          # the file was not touched


def test_profile_write_reports_a_real_change(tmp_path):
    (tmp_path / "profile.yaml").write_text("description: old\n")
    r = M.write_profile_entry(tmp_path, "new")
    assert r["unchanged"] is False and r["snapshot"] is not None


def test_block_write_is_a_noop_when_unchanged(tmp_path):
    (tmp_path / "memories").mkdir()
    M.write_blocks(tmp_path, "memory", ["a", "b"])
    p = tmp_path / "memories" / "MEMORY.md"
    before = p.stat().st_mtime_ns
    r = M.write_blocks(tmp_path, "memory", ["a", "b"])
    assert r["unchanged"] is True and r["snapshot"] is None
    assert p.stat().st_mtime_ns == before
