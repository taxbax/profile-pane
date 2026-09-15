"""Tests for panecore.secrets — the .env KEY layer.

The invariant that matters: values move but are never RETURNED. If `read_keys` or
`push` ever hands a value back, that value can reach the pane, the journal and a log.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))

from panecore import secrets as S   # noqa: E402


def build(tmp_path, anchor_env, member_env=None):
    a, m = tmp_path / "anchor", tmp_path / "member"
    a.mkdir(); m.mkdir()
    (a / ".env").write_text(anchor_env)
    if member_env is not None:
        (m / ".env").write_text(member_env)
    return a, m


# ── reading ──────────────────────────────────────────────────────────────────────

def test_read_keys_lists_names_and_never_values(tmp_path):
    a, _ = build(tmp_path, "OPENROUTER_API_KEY=sk-or-verysecret\nXAI_API_KEY=abc123\n")
    keys = S.read_keys(a)
    assert [k["key"] for k in keys] == ["OPENROUTER_API_KEY", "XAI_API_KEY"]
    blob = repr(keys)
    assert "verysecret" not in blob and "abc123" not in blob


def test_read_keys_reports_an_empty_value(tmp_path):
    a, _ = build(tmp_path, "FILLED=yes\nEMPTY=\nEMPTY2=   \n")
    got = {k["key"]: k["has_value"] for k in S.read_keys(a)}
    assert got == {"FILLED": True, "EMPTY": False, "EMPTY2": False}


def test_read_keys_skips_comments_and_blanks(tmp_path):
    a, _ = build(tmp_path, "# a comment\n\nREAL=1\n  # indented comment\n")
    assert [k["key"] for k in S.read_keys(a)] == ["REAL"]


def test_read_keys_handles_a_missing_env(tmp_path):
    assert S.read_keys(tmp_path / "nowhere") == []


def test_read_keys_handles_a_value_with_equals_signs(tmp_path):
    a, _ = build(tmp_path, "TOKEN=a=b=c\n")
    assert S.read_keys(a)[0]["key"] == "TOKEN"


# ── merging ──────────────────────────────────────────────────────────────────────

def test_push_adds_the_selected_key(tmp_path):
    a, m = build(tmp_path, "WANTED=1\n", "MINE=own\n")
    r = S.push(a, m, ["WANTED"])
    text = (m / ".env").read_text()
    assert "WANTED=1" in text and "MINE=own" in text


def test_push_keeps_the_members_own_keys(tmp_path):
    """Merge, not replace — a member's own keys must survive."""
    a, m = build(tmp_path, "WANTED=1\n", "MINE=own\nOTHER=mine2\n")
    S.push(a, m, ["WANTED"])
    text = (m / ".env").read_text()
    assert "MINE=own" in text and "OTHER=mine2" in text


def test_push_updates_a_key_in_place_and_keeps_its_position(tmp_path):
    a, m = build(tmp_path, "SHARED=new\n", "# header\nSHARED=old\nKEPT=1\n")
    S.push(a, m, ["SHARED"])
    lines = (m / ".env").read_text().splitlines()
    assert lines[0] == "# header"
    assert lines[1] == "SHARED=new"          # updated where it was
    assert lines[2] == "KEPT=1"              # nothing displaced


def test_push_copies_only_the_keys_named(tmp_path):
    a, m = build(tmp_path, "TAKE=1\nLEAVE=2\n", "")
    S.push(a, m, ["TAKE"])
    text = (m / ".env").read_text()
    assert "TAKE=1" in text and "LEAVE" not in text


def test_push_reports_a_key_the_anchor_does_not_have(tmp_path):
    a, m = build(tmp_path, "HAVE=1\n", "")
    r = S.push(a, m, ["HAVE", "MISSING"])
    assert r["ok"] and r["missing"] == ["MISSING"] and r["keys"] == ["HAVE"]


def test_push_refuses_when_the_anchor_holds_none_of_them(tmp_path):
    a, m = build(tmp_path, "OTHER=1\n", "")
    r = S.push(a, m, ["NOPE"])
    assert r["ok"] is False and "none of the selected" in r["reason"]


def test_push_creates_the_env_when_absent(tmp_path):
    a, m = build(tmp_path, "WANTED=1\n")
    r = S.push(a, m, ["WANTED"])
    assert r["created"] is True and r["snapshot"] is None      # reverse is deletion
    text = (m / ".env").read_text()
    assert "WANTED=1" in text
    # a synced key says where it came from, so the file is self-explaining later
    assert "profile-pane" in text


def test_push_snapshots_before_overwriting(tmp_path):
    a, m = build(tmp_path, "WANTED=1\n", "WANTED=old\n")
    r = S.push(a, m, ["WANTED"])
    assert Path(r["snapshot"]).read_text() == "WANTED=old\n"


def test_push_is_idempotent(tmp_path):
    a, m = build(tmp_path, "WANTED=1\n", "")
    S.push(a, m, ["WANTED"])
    second = S.push(a, m, ["WANTED"])
    assert second["unchanged"] is True


def test_push_never_returns_a_value(tmp_path):
    a, m = build(tmp_path, "TOPSECRET=hunter2\n", "")
    r = S.push(a, m, ["TOPSECRET"])
    assert "hunter2" not in repr(r)          # the receipt carries names, not values


def test_push_refuses_an_empty_selection(tmp_path):
    a, m = build(tmp_path, "X=1\n", "")
    r = S.push(a, m, [])
    assert r["ok"] is False and "no keys selected" in r["reason"]


def test_push_does_not_touch_the_anchor(tmp_path):
    a, m = build(tmp_path, "WANTED=1\n", "")
    before = (a / ".env").read_text()
    S.push(a, m, ["WANTED"])
    assert (a / ".env").read_text() == before


def test_push_quoted_values_survive_verbatim(tmp_path):
    a, m = build(tmp_path, 'Q="a value with spaces"\n', "")
    S.push(a, m, ["Q"])
    assert 'Q="a value with spaces"' in (m / ".env").read_text()