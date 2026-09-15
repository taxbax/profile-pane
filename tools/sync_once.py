#!/usr/bin/env python3
"""profile-pane auto-sync — the reconcile pass as a standalone entrypoint.

Run by a schedule (or by hand). Deliberately self-contained: stdlib + panecore only, so
it works whether or not the desktop app is running.

    python3 tools/sync_once.py            # DRY RUN — report what would change
    python3 tools/sync_once.py --apply    # write the changes

Only groups whose policy sets `auto: "on"` are considered. Within a group, only the
layers it enabled and the skills its own manifest records. A pass that changes nothing
prints "nothing to do" and exits 0 — that is the healthy outcome, not a failure.

Exit codes:  0 = ran (changed or not)   1 = the pass itself failed
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "dashboard"))

from panecore.groups import GroupRegistry      # noqa: E402
from panecore import apply as apply_mod
from panecore import sync as sync_mod          # noqa: E402

HERMES_HOME = Path.home() / ".hermes"
PROFILES_ROOT = HERMES_HOME / "profiles"
# the registry the pane writes; fall back to the legacy plugin-dir location
GROUPS_CANDIDATES = [
    HERMES_HOME / "profile-pane" / "groups.json",
    HERMES_HOME / "plugins" / "profile-pane" / "groups.json",
]


def find_groups_path() -> Path:
    for p in GROUPS_CANDIDATES:
        if p.exists():
            return p
    return GROUPS_CANDIDATES[0]


def present_profiles() -> list[str]:
    """Live profile names, read the same way the gateway resolves them."""
    root = PROFILES_ROOT
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir()
                  if d.is_dir() and not d.name.startswith("."))


def main() -> int:
    ap = argparse.ArgumentParser(description="profile-pane reconcile pass")
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (default is a dry run that writes nothing)")
    ap.add_argument("--group", help="limit the pass to one group id")
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    ap.add_argument("--quiet", action="store_true",
                    help="print nothing when there is nothing to do — for a scheduled run, "
                         "where silence is the healthy signal")
    args = ap.parse_args()

    def say(msg: str) -> None:
        if not args.quiet:
            print(msg)

    reg = GroupRegistry(find_groups_path())
    groups = reg.all()
    if args.group:
        g = reg.load(args.group)
        groups = [g] if g else []

    # Naming a group by hand means "do it now", exactly as POST /sync {group: X} does.
    # The Auto filter belongs to the SCHEDULE, so it applies only when no group is named.
    auto = groups if getattr(args, "group", None) else [
        g for g in groups if (g.get("policy", {}) or {}).get("auto") == "on"]
    if not auto:
        say(f"profile-pane auto-sync: no group has auto on ({len(groups)} group(s) exist) — nothing to do")
        return 0

    # An unattended schedule must not grow a snapshot pile without bound.
    apply_mod.prune_snapshots()
    sync_mod.DEFAULT_HOME = HERMES_HOME
    report = sync_mod.sync_all(groups, present_profiles(), PROFILES_ROOT,
                               dry_run=not args.apply)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    verb = "applied" if args.apply else "would apply"
    if report["changes"] == 0:
        say(f"profile-pane auto-sync: {report['groups_considered']} group(s) checked, "
            f"nothing to do")
        return 0

    lines = [f"profile-pane auto-sync: {verb} {report['changes']} change(s) "
             f"across {report['groups_considered']} group(s)"]
    for g in report["groups"]:
        for prof, ops in g.get("skills", {}).items():
            for o in ops:
                if o["action"] in ("recopied", "would-recop"):
                    lines.append(f"  skill  {prof}/{o['skill']}  ({o.get('state','')})")
        for layer, v in g.get("layers", {}).items():
            for o in v.get("results", []):
                # "would-push" is what _push_layer returns in dry-run and what sync_all counts as a
                # change — omitting it printed "1 change(s)" and then named none.
                if o.get("action") in ("wrote", "would-write", "pushed", "would-push"):
                    lines.append(f"  {layer:7s} {o.get('profile','')}  "
                                 f"{o.get('scope') or o.get('layer','')}")
    if not args.apply:
        lines.append("  (dry run — re-run with --apply to write)")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                                  # noqa: BLE001
        print(f"profile-pane auto-sync FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
