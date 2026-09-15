"""Tests for panecore.soul — the anchor model.

The load-bearing property is IDEMPOTENCE: re-applying must replace the managed
region, never append a second copy. Auto-sync re-runs this on a schedule, so a
splice that grew by one region per run would corrupt every member's SOUL.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))

from panecore import soul as S   # noqa: E402

ANCHOR = """# Canonical agent

Intro line.

## Loyalty
Always act in the principal's interest.

## Care
Ground every fact in a fetched, dated source.

## Boundaries
Never spend the principal's money without an ask.
"""


def test_split_sections_finds_every_heading():
    titles = [s["title"] for s in S.split_sections(ANCHOR)]
    assert titles == ["Canonical agent", "Loyalty", "Care", "Boundaries"]


def test_split_sections_carries_body_and_level():
    secs = {s["title"]: s for s in S.split_sections(ANCHOR)}
    assert secs["Loyalty"]["level"] == 2
    assert "principal's interest" in secs["Loyalty"]["text"]
    assert secs["Loyalty"]["lines"] == 2


def test_splice_appends_when_there_is_no_region():
    out = S.splice("my own soul\n", "SHARED")
    assert "my own soul" in out
    assert S.MARK_BEGIN in out and S.MARK_END in out


def test_splice_is_idempotent():
    once = S.splice("my own soul\n", "SHARED")
    twice = S.splice(once, "SHARED")
    assert once.count(S.MARK_BEGIN) == 1
    assert twice.count(S.MARK_BEGIN) == 1      # replaced, not appended
    assert twice.count(S.MARK_END) == 1


def test_splice_replaces_the_region_with_new_content():
    once = S.splice("own\n", "OLD")
    twice = S.splice(once, "NEW")
    assert "NEW" in twice and "OLD" not in twice
    assert "own" in twice                       # the member's own text survives


def test_own_text_strips_the_managed_region():
    spliced = S.splice("my own soul here\n", "SHARED")
    assert S.own_text(spliced).strip() == "my own soul here"


def test_render_whole_is_the_anchor_verbatim():
    assert S.render(ANCHOR, "whole") == ANCHOR


def test_render_block_uses_the_anchors_managed_region_when_it_has_one():
    anchor_with_region = S.splice("anchor own text\n", "THE SHARED BLOCK")
    assert "THE SHARED BLOCK" in S.render(anchor_with_region, "block")
    assert "anchor own text" not in S.render(anchor_with_region, "block")


def test_render_section_selects_only_the_named_sections():
    got = S.render(ANCHOR, "section", ["Care", "Boundaries"])
    assert "Ground every fact" in got and "Never spend" in got
    assert "act in the principal's interest" not in got


def test_render_section_with_nothing_selected_is_empty():
    assert S.render(ANCHOR, "section", []) == ""


def test_render_off_is_empty():
    assert S.render(ANCHOR, "off") == ""


def test_apply_scope_whole_replaces_the_file(tmp_path):
    (tmp_path / "SOUL.md").write_text("old role soul\n")
    r = S.apply_scope(tmp_path, ANCHOR, "whole")
    assert r["ok"] is True
    assert (tmp_path / "SOUL.md").read_text() == ANCHOR      # byte-identical
    assert Path(r["snapshot"]).read_text() == "old role soul\n"   # the reverse


def test_apply_scope_block_preserves_the_members_own_text(tmp_path):
    (tmp_path / "SOUL.md").write_text("# Trader Bot\nI reason about trades.\n")
    S.apply_scope(tmp_path, ANCHOR, "block")
    out = (tmp_path / "SOUL.md").read_text()
    assert "I reason about trades." in out      # per-bot identity survives
    assert "Ground every fact" in out           # anchor content arrived
    assert out.count(S.MARK_BEGIN) == 1


def test_apply_scope_block_twice_does_not_duplicate(tmp_path):
    (tmp_path / "SOUL.md").write_text("# Trader Bot\nmine\n")
    S.apply_scope(tmp_path, ANCHOR, "block")
    S.apply_scope(tmp_path, ANCHOR, "block")
    out = (tmp_path / "SOUL.md").read_text()
    assert out.count(S.MARK_BEGIN) == 1
    assert out.count("Ground every fact") == 1


def test_apply_scope_section_writes_only_what_was_ticked(tmp_path):
    (tmp_path / "SOUL.md").write_text("# mine\nkeep me\n")
    S.apply_scope(tmp_path, ANCHOR, "section", ["Care"])
    out = (tmp_path / "SOUL.md").read_text()
    assert "Ground every fact" in out
    assert "Never spend" not in out
    assert "keep me" in out


def test_apply_scope_reports_nothing_to_write(tmp_path):
    r = S.apply_scope(tmp_path, ANCHOR, "section", [])
    assert r["ok"] is False and "no sections selected" in r["reason"]


def test_apply_scope_off_is_a_noop(tmp_path):
    r = S.apply_scope(tmp_path, ANCHOR, "off")
    assert r["ok"] is True and "skipped" in r


def test_apply_scope_marks_created_when_member_had_no_soul(tmp_path):
    r = S.apply_scope(tmp_path, ANCHOR, "whole")
    assert r["created"] is True and r["snapshot"] is None   # reverse is deletion
