"""profile-pane backend — mounted at /api/plugins/profile-pane/ inside the gateway.

A THIN router over `panecore.*`; every rule lives in a testable module there.
Runs in the gateway process, so it has real filesystem authority — which is why
the panecore guards (path safety, blocking selectors) are not optional.

Cross-profile writes must be scoped. `profiles.configure` (tui_gateway) remains the
canonical RPC for SOUL/description/model; this router reads/writes files directly
only for what has no RPC (skill copies, memory, group registry).
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

# panecore is a sibling package — make it importable regardless of gateway load order.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import APIRouter, Body                                    # noqa: E402

from panecore.groups import GroupRegistry, SOUL_SCOPE_CONSEQUENCE      # noqa: E402
from panecore import paths as paths_mod                                # noqa: E402
from panecore import apply as apply_mod                                # noqa: E402
from panecore import memory as mem_mod                                 # noqa: E402
from panecore import soul as soul_mod                                  # noqa: E402
from panecore import sync as sync_mod                                  # noqa: E402
from panecore import trial as trial_mod                                # noqa: E402
from panecore import secrets as secrets_mod                            # noqa: E402
from panecore import models as models_mod                               # noqa: E402

router = APIRouter()

HERMES_HOME = Path.home() / ".hermes"
PROFILES_ROOT = HERMES_HOME / "profiles"

# The reconcile must resolve `default` to the Hermes ROOT, not to
# `<profiles>/default` — see sync.home_of.
sync_mod.DEFAULT_HOME = HERMES_HOME
GROUPS_PATH = HERMES_HOME / "profile-pane" / "groups.json"
JOURNAL_PATH = HERMES_HOME / "profile-pane" / "journal.jsonl"


# ----------------------------------------------------------------------------- helpers

def _registry() -> GroupRegistry:
    return GroupRegistry(GROUPS_PATH)


def _registry_path() -> Path:
    return GROUPS_PATH


def list_profiles() -> list[dict]:
    """HOME-anchored by design (mirrors hermes_cli.profiles._get_profiles_root) so
    one profile's gateway can address every profile.

    Two de-duplications, both load-bearing:

    * The ROOT profile is named `default`, and its home is HERMES_HOME — not
      `<profiles>/default`. Anything that lists profiles by walking the directory has to
      know that, or `default` appears TWICE: once as the real root and once as an empty
      phantom directory.
    * Deduped by RESOLVED PATH as well as by name, so a directory that aliases an
      already-listed home cannot produce a second row.

    `profile_dir()` returns the first match, so a duplicate made every write's
    destination depend on iteration order.
    """
    out: list[dict] = [{"name": "default", "path": str(HERMES_HOME)}]
    seen = {str(Path(HERMES_HOME).expanduser())}
    if PROFILES_ROOT.is_dir():
        for d in sorted(PROFILES_ROOT.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            if d.name == "default":
                # `<profiles>/default` is NOT the default profile. The root is.
                continue
            key = str(d.expanduser())
            if key in seen:
                continue
            seen.add(key)
            out.append({"name": d.name, "path": str(d)})
    return out


def profile_dir(name: str) -> Path | None:
    for p in list_profiles():
        if p["name"] == name:
            return Path(p["path"])
    return None


def skill_inventory(pdir: Path) -> list[dict]:
    """Read the DEPLOYED layer, never a declared one — skills-manager #384 showed a
    badge going green for 11 agents with only 2 real rows on disk."""
    root = pdir / "skills"
    out: list[dict] = []
    if not root.is_dir():
        return out
    for sm in sorted(root.rglob("SKILL.md")):
        rel = sm.parent.relative_to(root)
        out.append({"rel": str(rel), "name": sm.parent.name})
    return out


def budget(pdir: Path) -> dict:
    """Always-on lines = SOUL + memory + AGENTS. Warn 120 / alert 200 (dilution risk)."""
    total, parts = 0, {}
    for f, label in ((pdir / "SOUL.md", "soul"),
                     (pdir / "memories" / "MEMORY.md", "memory"),
                     (pdir / "AGENTS.md", "agents")):
        n = len(f.read_text(encoding="utf-8", errors="replace").splitlines()) if f.exists() else 0
        parts[label] = n
        total += n
    return {"lines": total, "parts": parts,
            "level": "alert" if total > 200 else "warn" if total > 120 else "ok"}


def journal(entry: dict) -> None:
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {"at": time.time(), **entry}
    with JOURNAL_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


# ----------------------------------------------------------------------------- routes


def profile_facts(pdir: Path) -> dict:
    """What a profile IS — the description it carries and the size of each durable file.

    The expanded row answers "what is this profile?". Its skills are already visible in
    the SKILLS box, marked against it, so listing them again here was pure duplication.
    """
    mem = mem_mod.read_blocks(pdir, "memory")
    usr = mem_mod.read_blocks(pdir, "user")
    soul = soul_mod.read_soul(pdir)
    return {
        "description": mem_mod.read_profile_entry(pdir).get("description", ""),
        "soul_lines": len(soul.splitlines()) if soul else 0,
        "soul_has_region": bool(soul) and soul_mod.managed_region(soul) is not None,
        "soul_sections": len(soul_mod.split_sections(soul)) if soul else 0,
        "memory_blocks": len(mem.get("blocks", [])),
        "user_blocks": len(usr.get("blocks", [])),
    }


@router.get("/profiles")
async def profiles() -> dict:
    out = []
    for p in list_profiles():
        pdir = Path(p["path"])
        out.append({
            "name": p["name"], "path": p["path"],
            "skills": skill_inventory(pdir),        # counts come from the deployed layer
            "budget": budget(pdir),
            "has_soul": (pdir / "SOUL.md").is_file(),
            "has_memory": (pdir / "memories" / "MEMORY.md").is_file(),
            **profile_facts(pdir),
        })
    return {"profiles": out}


@router.get("/groups")
async def groups_list() -> dict:
    present = [p["name"] for p in list_profiles()]
    reg = _registry()
    # NOTE: this was `{"**": None, **g, ...}` — `"**"` in a dict display is a literal
    # string KEY, not a spread, so every group the API returned carried a junk `"**"`
    # field. The pane spreads the group back on save, so the junk was PERSISTED into
    # groups.json. A read endpoint must never be able to write into the record it reads.
    return {"groups": [{**g, "resolved": reg.resolve(g["id"], present)}
                       for g in reg.all()],
            "soul_scopes": SOUL_SCOPE_CONSEQUENCE}


@router.post("/groups")
async def groups_save(group: dict = Body(...)) -> dict:
    try:
        return {"ok": True, "group": _registry().save(group)}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@router.delete("/groups/{gid}")
async def groups_delete(gid: str) -> dict:
    return {"ok": _registry().delete(gid)}


@router.get("/groups/{gid}/resolve")
async def groups_resolve(gid: str) -> dict:
    present = [p["name"] for p in list_profiles()]
    return _registry().resolve(gid, present)


@router.post("/plan")
async def plan(body: dict = Body(...)) -> dict:
    """READ-ONLY plan — the same planner serves drag-drop, the bulk verb, and the CLI.

    Preview runs the SAME preflight as apply (skills-manager #437: a dry-run reported
    ok:true while the real run refused).
    """
    gid = body.get("group")
    reg = _registry()
    group = reg.load(gid) if gid else None
    present = [p["name"] for p in list_profiles()]
    resolved = reg.resolve(gid, present) if gid else {"found": body.get("profiles", []), "missing": [], "blocks": False, "reason": ""}
    if resolved["blocks"]:
        return {"ok": False, "dry_run": True, "blocked": True, "reason": resolved["reason"]}

    actions, refusals = [], []

    # PAIRED targeting, mirroring /trial. Two flat sets would be multiplied here and the
    # preview would report more copies than the apply it guards actually makes.
    pairs = body.get("pairs")
    if pairs is None:
        pairs = [{"profile": p, "skill": r}
                 for p in resolved["found"] for r in body.get("skills", [])]

    for pair in pairs:
        name, rel = pair.get("profile"), pair.get("skill")
        pdir = profile_dir(name)
        if pdir is None:
            refusals.append({"profile": name, "reason": "profile not found"})
            continue
        ok, target = paths_mod.resolve_target(pdir / "skills", rel)
        if not ok:
            refusals.append({"profile": name, "skill": rel, "reason": target})
            continue
        src = find_skill_source(rel)
        if src is None:
            refusals.append({"profile": name, "skill": rel,
                             "reason": "no profile on this machine has this skill"})
            continue
        # Run the SAME blocking scan apply_skill runs. Without it Preview reports
        # "ready" for a skill Apply then refuses — the skills-manager #437 failure.
        hits = apply_mod.scan_secrets(src)
        if hits:
            refusals.append({"profile": name, "skill": rel,
                             "reason": "secret scan blocked", "secrets": hits})
            continue
        actions.append({"profile": name, "skill": rel, "op": "copy",
                        "target": target, "from": str(src)})
    return {"ok": True, "dry_run": True, "group": gid,
            "actions": actions, "refusals": refusals,
            "soul_scope": (group or {}).get("policy", {}).get("soul", "off"),
            "soul_consequence": SOUL_SCOPE_CONSEQUENCE.get((group or {}).get("policy", {}).get("soul", "off"), "")}


def find_skill_source(rel: str) -> Path | None:
    """The profile whose DEPLOYED tree actually contains this skill.

    `/apply` used to resolve every skill against one `source_root`. That works while the
    library is one profile's inventory, but the library now spans the machine — a skill
    that exists only in another profile has no `SKILL.md` under that root and the copy
    fails. Each skill is resolved to wherever it actually lives.
    """
    for p in list_profiles():
        cand = Path(p["path"]) / "skills" / rel
        if (cand / "SKILL.md").is_file():
            return cand
    return None


@router.get("/soul/{name}")
async def soul_read(name: str) -> dict:
    """Read a profile's SOUL, plus the headings the pane offers as a section checklist."""
    pdir = profile_dir(name)
    if pdir is None:
        return {"ok": False, "error": "profile not found"}
    text = soul_mod.read_soul(pdir)
    return {"ok": True, "profile": name, "text": text, "exists": bool(text),
            "lines": len(text.splitlines()),
            "sections": [{"title": s["title"], "level": s["level"], "lines": s["lines"]}
                         for s in soul_mod.split_sections(text)],
            "managed_region": soul_mod.managed_region(text) is not None,
            "own_lines": len(soul_mod.own_text(text).splitlines()) if text else 0}


@router.post("/soul")
async def soul_write(body: dict = Body(...)) -> dict:
    """Write the group's SOUL ANCHOR into its members.

    There is deliberately no `text` parameter. The pane never asks the user to author
    a SOUL — it asks which MEMBER is canonical (`soul_source`), and the scope decides
    how much of that anchor lands in everyone else:

      whole    the anchor's file, byte for byte
      block    the anchor's file spliced under ownership markers, member's own text kept
      section  only the `##` sections named in `soul_sections`, spliced the same way

    Every destination snapshots first (SOUL has no CAS), and each write is journalled.
    """
    reg = _registry()
    present = [p["name"] for p in list_profiles()]
    group = reg.load(body["group"]) if body.get("group") else None
    policy = (group or {}).get("policy", {}) or {}

    source = body.get("anchor") or policy.get("anchor")
    scope = body.get("scope") or policy.get("soul", "off")
    selected = body.get("sections") or policy.get("soul_sections") or []

    if not source:
        return {"ok": False, "error": "no SOUL anchor — pick which member's SOUL is canonical"}
    src_dir = profile_dir(source)
    if src_dir is None:
        return {"ok": False, "error": f"anchor profile '{source}' not found"}
    if scope == "off":
        return {"ok": True, "skipped": True, "reason": "SOUL is off for this group"}

    anchor_text = soul_mod.read_soul(src_dir)
    if not anchor_text.strip():
        return {"ok": False, "error": f"anchor '{source}' has no SOUL.md to copy from"}

    if body.get("profiles"):
        targets = list(body["profiles"])
    elif body.get("group"):
        resolved = reg.resolve(body["group"], present)
        if resolved["blocks"]:
            return {"ok": False, "blocked": True, "reason": resolved["reason"]}
        targets = resolved["found"]
    else:
        return {"ok": False, "error": "nothing to write to — pass a group or profiles"}

    results = []
    for name in targets:
        if name == source:
            results.append({"profile": name, "ok": True, "skipped": "this is the anchor"})
            continue
        pdir = profile_dir(name)
        if pdir is None:
            results.append({"profile": name, "ok": False, "reason": "profile not found"})
            continue
        r = soul_mod.apply_scope(pdir, anchor_text, scope, selected)
        results.append({"profile": name, **r})
        journal({"op": "apply_soul", "profile": name, "scope": scope, "anchor": source,
                 "snapshot": r.get("snapshot"), "bytes_out": r.get("bytes_out")})

    return {"ok": all(x.get("ok") for x in results) if results else True,
            "scope": scope, "source": source, "sections": selected,
            "anchor_lines": len(anchor_text.splitlines()), "results": results,
            "consequence": SOUL_SCOPE_CONSEQUENCE.get(scope, "")}


@router.post("/sync")
async def sync_run(body: dict = Body(default={})) -> dict:
    """Run the reconcile pass.

    DRY RUN IS THE DEFAULT — a caller must pass `dry_run: false` to write anything.
    Only groups whose policy sets `auto: "on"` are considered, and within a group only
    the layers it enabled plus the skills its own manifest records. A pass that changes
    nothing is the healthy outcome.
    """
    reg = _registry()
    present = [p["name"] for p in list_profiles()]
    if body.get("group"):
        g = reg.load(body["group"])
        groups = [g] if g else []
    else:
        groups = reg.all()
    return sync_mod.sync_all(groups, present, PROFILES_ROOT,
                             dry_run=bool(body.get("dry_run", True)),
                             # a NAMED group is a human asking by name; only the
                             # schedule (no group named) defers to each group's Auto.
                             only_auto=not body.get("group"))


@router.post("/trial")
async def trial_apply(body: dict = Body(...)) -> dict:
    """Apply the queue FOR REAL, recording the reverse of every single write.

    Preview says what would happen; a trial DOES it and hands you a revert. Skills, the
    SOUL/memory/profile layers, and the group record are all backed up before they move,
    so one call puts the whole tree back.
    """
    trial_mod.prune()
    apply_mod.prune_snapshots()
    tid = trial_mod.begin(label=body.get("label") or "pane trial")
    n, results = 0, []

    # ── skills: PAIRED (profile, skill), never a cross-product ─────────────────
    # The request used to carry `profiles: [...]` and `skills: [...]` as two flat sets,
    # which the loop below multiplied. So staging `SkillA → X` and then `SkillB → Y`
    # sent BOTH skills to BOTH profiles — four copies where the pane showed exactly two,
    # and every skill staged while browsing landed on every profile touched.
    #
    # `pairs` carries the targeting the UI actually displays. The flat lists are still
    # accepted so a direct API caller that wants the same skill everywhere keeps working.
    pairs = body.get("pairs")
    if pairs is None:
        pairs = ([{"profile": p, "skill": r, "op": "add"}
                  for p in body.get("profiles", []) for r in body.get("skills", [])]
                 + [{"profile": p, "skill": r, "op": "remove"}
                    for p in body.get("profiles", []) for r in body.get("remove", [])])

    for pair in pairs:
        name, rel = pair.get("profile"), pair.get("skill")
        op = pair.get("op") or "add"
        pdir = profile_dir(name) if name else None
        if pdir is None:
            results.append({"profile": name, "skill": rel, "ok": False,
                            "reason": "profile not found"})
            continue
        ok, target = paths_mod.resolve_target(pdir / "skills", rel)
        if not ok:
            results.append({"profile": name, "skill": rel, "ok": False, "reason": target})
            continue
        dest = Path(target)

        if op == "remove":
            # MOVED ASIDE, never deleted: the reverse is a rename back.
            if not dest.exists():
                results.append({"profile": name, "skill": rel, "ok": False,
                                "reason": "not installed in this profile"})
                continue
            snap = trial_mod.backup_dir(tid, dest, n)
            trial_mod.record(tid, {"kind": "dir", "path": str(dest), "existed": True,
                                   "snapshot": snap, "what": f"remove {rel} ← {name}"})
            n += 1
            stamp = time.strftime("%Y%m%d-%H%M%S")
            t = trash_root(pdir) / f"{stamp}--{rel.replace('/', '__')}"
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest), str(t))
            # recorded as a CREATED dir => the revert removes it (LIFO, so it goes first)
            trial_mod.record(tid, {"kind": "dir", "path": str(t), "existed": False,
                                   "snapshot": None, "what": f"trash entry for {rel}"})
            n += 1
            results.append({"profile": name, "skill": rel, "ok": True, "removed": str(t)})
            continue

        src = find_skill_source(rel)
        if src is None:
            results.append({"profile": name, "skill": rel, "ok": False,
                            "reason": "no profile on this machine has this skill"})
            continue
        existed = dest.exists()
        snap = trial_mod.backup_dir(tid, dest, n) if existed else None
        trial_mod.record(tid, {"kind": "dir", "path": str(dest),
                               "existed": existed, "snapshot": snap,
                               "what": f"skill {rel} → {name}"})
        n += 1
        r = apply_mod.apply_skill(src, dest, force=True)
        results.append({"profile": name, "skill": rel, **r})

    # ── layers: their writers already snapshot; record what they returned ──
    gid = body.get("group")
    group = _registry().load(gid) if gid else None
    if group:
        policy = group.get("policy", {}) or {}
        anchor = body.get("anchor") or policy.get("anchor")
        src_dir = profile_dir(anchor) if anchor else None
        members = [m for m in group.get("members", []) if profile_dir(m) is not None]

        if src_dir is not None:
            for layer in ("soul", *("memory", "user", "profile")):
                if layer == "soul":
                    scope = body.get("scope") or policy.get("soul", "off")
                    if scope == "off":
                        continue
                    anchor_text = soul_mod.read_soul(src_dir)
                    for m in members:
                        if m == anchor:
                            continue
                        r = soul_mod.apply_scope(profile_dir(m), anchor_text, scope,
                                                 body.get("sections") or policy.get("soul_sections") or [])
                        if r.get("snapshot"):
                            trial_mod.record(tid, {"kind": "file", "path": str(soul_mod.soul_path(profile_dir(m))),
                                                   "existed": True, "snapshot": r["snapshot"], "what": f"soul → {m}"})
                            n += 1
                        elif r.get("ok") and not r.get("unchanged"):
                            trial_mod.record(tid, {"kind": "file", "path": str(soul_mod.soul_path(profile_dir(m))),
                                                   "existed": False, "snapshot": None, "what": f"soul (new) → {m}"})
                            n += 1
                        results.append({"profile": m, "layer": "soul", **r})
                    continue
                mode = body.get(layer) or policy.get(layer, "off")
                if mode not in ("push", "append"):
                    continue
                for m in members:
                    if m == anchor:
                        continue
                    r = sync_mod._push_layer(layer, src_dir, profile_dir(m), dry_run=False, mode=mode)
                    if r.get("snapshot"):
                        trial_mod.record(tid, {"kind": "file", "path": str(r.get("path")),
                                               "existed": True, "snapshot": r["snapshot"],
                                               "what": f"{layer} → {m}"})
                        n += 1
                    elif r.get("ok") and not r.get("unchanged"):
                        trial_mod.record(tid, {"kind": "file", "path": str(r.get("path")),
                                               "existed": False, "snapshot": None,
                                               "what": f"{layer} (new) → {m}"})
                        n += 1
                    results.append({"profile": m, "layer": layer, **r})

        # ── secrets: the .env KEY layer — values move, but are never returned ──
        # Two whole-file-free modes: `push` copies EVERY key the anchor holds,
        # `granular` copies only the ticked ones. The difference is the difference
        # between handing over the keyring and handing over one key.
        #
        # `src_dir is not None` is load-bearing: this block sat OUTSIDE the anchor guard
        # that covers the layers above it, so a group with secrets enabled and NO anchor
        # reached `read_keys(None)` and raised TypeError — a 500 on a configuration the
        # pane lets you build (turn Secrets on before picking an anchor).
        sec_mode = body.get("secrets") or policy.get("secrets", "off")
        if sec_mode in ("push", "granular") and src_dir is None:
            results.append({"layer": "secrets", "ok": False,
                            "reason": "no anchor — nothing to copy keys FROM"})
        elif sec_mode in ("push", "granular"):
            if sec_mode == "push":
                sec_keys = [k["key"] for k in secrets_mod.read_keys(src_dir) if k["has_value"]]
            else:
                sec_keys = body.get("secret_keys") or policy.get("secret_keys") or []
            for m in members:
                if m == anchor:
                    continue
                r = secrets_mod.push(src_dir, profile_dir(m), sec_keys)
                if r.get("snapshot"):
                    trial_mod.record(tid, {"kind": "file", "path": str(r.get("path")),
                                           "existed": True, "snapshot": r["snapshot"],
                                           "what": f".env keys → {m}"})
                    n += 1
                elif r.get("ok") and not r.get("unchanged"):
                    trial_mod.record(tid, {"kind": "file", "path": str(r.get("path")),
                                           "existed": False, "snapshot": None,
                                           "what": f".env (new) → {m}"})
                    n += 1
                results.append({"profile": m, "layer": "secrets", "mode": sec_mode,
                                **{k: v for k, v in r.items() if k != "snapshot"}})

        # ── the group record itself: policy AND membership, one snapshot ──
        # The group is a single object, so both halves commit together. Either may
        # arrive alone; whichever did is applied to the loaded record, never replacing
        # what was not sent.
        if body.get("commit_policy") is not None or body.get("commit_members") is not None:
            gp = _registry_path()
            existed = gp.exists()
            snap = trial_mod.backup_file(tid, gp, n) if existed else None
            trial_mod.record(tid, {"kind": "file", "path": str(gp), "existed": existed,
                                   "snapshot": snap, "what": "group record (policy + members)"})
            n += 1
            reg = _registry()
            next_group = {k: v for k, v in group.items() if k != "resolved"}
            if body.get("commit_members") is not None:
                next_group["members"] = body["commit_members"]
            if body.get("commit_policy") is not None:
                next_group["policy"] = body["commit_policy"]
            reg.save(next_group)

    trial_mod._save(tid, {**(trial_mod.load(tid) or {}), "summary": {
        "ops": n, "results": len(results)}})
    # ok reflects what ACTUALLY happened. It was hard-coded True, so a batch whose
    # every op failed still reported success to anyone checking `ok` alone.
    failed = [r for r in results if r.get("ok") is False]
    return {"ok": not failed, "trial": tid, "ops": n, "results": results,
            "failed": len(failed), "revert_with": {"trial": tid}}


@router.post("/trial/revert")
async def trial_revert(body: dict = Body(default={})) -> dict:
    """Undo a trial — every recorded reverse, in reverse order. Single-use."""
    tid = body.get("trial")
    if not tid:
        live = trial_mod.latest_live()
        if not live:
            return {"ok": False, "error": "no live trial to revert"}
        tid = live["id"]
    r = trial_mod.revert(tid)
    journal({"op": "trial_revert", "trial": tid, "ok": r.get("ok"), "count": r.get("count")})
    return r


@router.get("/trial/live")
async def trial_state() -> dict:
    """Whether a trial is live, and WHAT pressing Undo would put back.

    The count alone was the bug: the pane asked the principal to press Undo with no way
    to see what it would reverse, so the label and every op were collapsed away here.
    Paths and kinds only — the record holds snapshot PATHS, never content, so nothing
    in this response is a value.
    """
    live = trial_mod.latest_live()
    if not live:
        return {"ok": True, "live": False, "trial": None, "ops": 0, "at": None,
                "label": None, "what": [], "total": 0}

    ops = live.get("ops", [])
    what: list[dict] = []
    seen: set[str] = set()
    # Display shortening. Trim the Hermes home with `~` when the path is under it, so the
    # list reads as `~/.hermes/profiles/trad/SOUL.md`. A path outside every known root is
    # shown in full rather than mangled — an unreadable path in an undo list is worse than
    # a long one.
    roots = [str(Path.home()), str(DEFAULT_HOME)] if "DEFAULT_HOME" in globals() else [str(Path.home())]
    for op in ops:
        raw = str(op.get("path") or "")
        if not raw or raw in seen:      # one path can carry two ops (dir + its file)
            continue
        seen.add(raw)
        short = raw
        for r in sorted(roots, key=len, reverse=True):
            if raw.startswith(r):
                short = "~" + raw[len(r):]
                break
        what.append({
            "path": short,
            "name": Path(raw).name,
            "kind": op.get("kind"),               # file | dir
            "existed": bool(op.get("existed")),   # False => Undo REMOVES this path
        })

    # A reconcile can touch hundreds of paths. Send a bounded list plus the true total,
    # so the pane can say "+N more" instead of either truncating silently or streaming
    # a wall of paths every five seconds.
    CAP = 60
    return {"ok": True, "live": True,
            "trial": live["id"],
            "ops": len(ops),
            "at": live.get("at"),
            "label": live.get("label") or "",
            "what": what[:CAP],
            "total": len(what)}


# ------------------------------------------------------------- remove / restore
# Copying a skill IN had no counterpart. A pane that can only add is half a CRUD.

def trash_root(pdir: Path) -> Path:
    return Path(pdir) / ".profile-pane" / "trash"


@router.get("/trash/{name}")
async def trash_list(name: str) -> dict:
    """What this profile has had removed. The pane can put any of it back."""
    pdir = profile_dir(name)
    if pdir is None:
        return {"ok": False, "error": "profile not found"}
    root = trash_root(pdir)
    items = []
    if root.is_dir():
        for d in sorted(root.iterdir(), reverse=True):
            if d.is_dir():
                stamp, _, rel = d.name.partition("--")
                items.append({"trash": d.name, "skill": rel.replace("__", "/"),
                              "at": stamp, "files": sum(1 for _ in d.rglob("*") if _.is_file())})
    return {"ok": True, "profile": name, "items": items}


@router.post("/restore-skill")
async def restore_skill(body: dict = Body(...)) -> dict:
    """Put a trashed skill back where it came from."""
    name = body.get("profile")
    pdir = profile_dir(name)
    if pdir is None:
        return {"ok": False, "error": "profile not found"}
    t = trash_root(pdir) / (body.get("trash") or "")
    if not t.is_dir() or t.parent != trash_root(pdir):
        return {"ok": False, "error": "no such trashed skill"}
    rel = t.name.partition("--")[2].replace("__", "/")
    ok, target = paths_mod.resolve_target(pdir / "skills", rel)
    if not ok:
        return {"ok": False, "error": target}
    if Path(target).exists():
        return {"ok": False, "error": "something already occupies that path"}
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(t), str(target))
    journal({"op": "restore_skill", "profile": name, "skill": rel, "from": str(t)})
    return {"ok": True, "profile": name, "skill": rel, "restored": str(target)}


# ------------------------------------------------------- activity / journal

@router.get("/journal")
async def journal_read(limit: int = 120, profile: str | None = None) -> dict:
    """The pane's own activity log. Every write it makes is journalled; this reads it back.

    This is what the COMMITS view shows — what actually happened, when, and where the
    reverse lives.
    """
    if not JOURNAL_PATH.exists():
        return {"ok": True, "entries": [], "total": 0}
    lines = [ln for ln in JOURNAL_PATH.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    out = []
    for ln in reversed(lines):
        try:
            e = json.loads(ln)
        except Exception:
            continue
        if profile and e.get("profile") != profile:
            continue
        out.append(e)
        if len(out) >= max(1, min(limit, 500)):
            break
    return {"ok": True, "entries": out, "total": len(lines)}


@router.post("/reapply")
async def reapply(body: dict = Body(...)) -> dict:
    """Re-copy ONE drifted skill to ONE profile, right now.

    The scheduled pass already does this — invisibly, on a timer, only when something
    moved. This is the verb for when you can see the badge and want it fixed now rather
    than waiting for the next tick.
    """
    name = body.get("profile")
    rel = body.get("skill")
    pdir = profile_dir(name)
    if pdir is None:
        return {"ok": False, "error": "profile not found"}
    ok, target = paths_mod.resolve_target(pdir / "skills", rel)
    if not ok:
        return {"ok": False, "error": target}
    src = find_skill_source(rel)
    if src is None:
        return {"ok": False, "error": "no profile on this machine has this skill"}
    r = apply_mod.apply_skill(src, Path(target), force=True)
    if r.get("ok"):
        apply_mod.write_manifest(pdir, f"skills/{rel}", {"hash": r["hash"], "src": str(src)})
    journal({"op": "reapply", "profile": name, "skill": rel, "from": str(src),
             "ok": bool(r.get("ok")), "reason": r.get("reason", "")})
    return {**r, "profile": name, "skill": rel}


# ------------------------------------------------------- rename

@router.post("/rename-group")
async def rename_group(body: dict = Body(...)) -> dict:
    """Rename a group. Members and policy move with it; NO profile is touched — a group
    is a record outside every profile, so renaming one is a pure registry edit."""
    old, new = body.get("from"), (body.get("to") or "").strip().lower()
    new = "-".join(new.replace("_", "-").split())
    if not old or not new:
        return {"ok": False, "error": "need both a from and a to"}
    reg = _registry()
    g = reg.load(old)
    if g is None:
        return {"ok": False, "error": f"no group '{old}'"}
    if reg.load(new) is not None:
        return {"ok": False, "error": f"'{new}' already exists"}
    g["id"] = new
    reg.save(g)
    reg.delete(old)
    journal({"op": "rename_group", "from": old, "to": new})
    return {"ok": True, "from": old, "to": new}


@router.post("/skill-rename")
async def skill_rename(body: dict = Body(...)) -> dict:
    """Rename a skill UNIVERSALLY — every profile that holds it gets the new name.

    A skill's PATH is its identity: two profiles holding the same skill under different
    names are two different skills as far as copying, drift and the manifest are
    concerned. A per-profile rename would fork it, so this renames everywhere at once,
    and each destination keeps its own reverse (the old directory is moved, not lost).
    """
    old_rel = (body.get("from") or "").strip().strip("/")
    new_name = (body.get("to") or "").strip()
    if not old_rel or not new_name:
        return {"ok": False, "error": "need both a from and a to"}
    if "/" in new_name or new_name in (".", ".."):
        return {"ok": False,
                "error": "the new name is the skill's NAME only (one path segment) — "
                         "its category folder stays where it is"}
    parent = old_rel.rsplit("/", 1)[0] if "/" in old_rel else ""
    new_rel = f"{parent}/{new_name}" if parent else new_name

    out = []
    for p in list_profiles():
        pdir = Path(p["path"])
        ok, target = paths_mod.resolve_target(pdir / "skills", old_rel)
        if not ok:
            out.append({"profile": p["name"], "ok": False, "reason": target})
            continue
        src = Path(target)
        if not (src / "SKILL.md").is_file():
            continue                     # not installed here; nothing to do
        ok, dest = paths_mod.resolve_target(pdir / "skills", new_rel)
        if not ok:
            out.append({"profile": p["name"], "ok": False, "reason": dest})
            continue
        if Path(dest).exists():
            out.append({"profile": p["name"], "ok": False,
                        "reason": f"{new_rel} already exists in this profile"})
            continue
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        # the manifest keys on the old path — re-point it or drift stops resolving
        man = apply_mod.read_manifest(pdir)
        if f"skills/{old_rel}" in man:
            rec = man.pop(f"skills/{old_rel}")
            man[f"skills/{new_rel}"] = rec
            apply_mod.manifest_path(pdir).write_text(json.dumps(man, indent=2, sort_keys=True), encoding="utf-8")
        out.append({"profile": p["name"], "ok": True, "to": str(dest)})
        journal({"op": "skill_rename", "profile": p["name"], "from": old_rel, "to": new_rel})

    renamed = [x for x in out if x.get("ok")]
    if not renamed:
        return {"ok": False, "error": "no profile on this machine holds that skill", "results": out}
    return {"ok": all(x.get("ok") for x in out), "from": old_rel, "to": new_rel,
            "renamed": len(renamed), "results": out}


# ------------------------------------------------------- backups

BACKUPS = HERMES_HOME / "profile-pane" / "backups"
BACKUP_MEMBERS = ("SOUL.md", "AGENTS.md", "profile.yaml", "memories", "skills", ".profile-pane")
# A backup is a SECOND copy of a secret. Making one silently is not the pane's call,
# so these are EXCLUDED unless you explicitly opt in.
SECRET_MEMBERS = (".env", "auth.json")


@router.post("/backups")
async def backup_create(body: dict = Body(...)) -> dict:
    """Snapshot a profile's configuration — or every profile's — into a tar.gz.

    `.env` and `auth.json` are excluded by default (see SECRET_MEMBERS). `all: true`
    makes one archive per profile rather than a single blob, so a restore stays
    per-profile and cannot half-succeed across the machine.
    """
    import tarfile
    BACKUPS.mkdir(parents=True, exist_ok=True)
    names = [p["name"] for p in list_profiles()] if body.get("all") else [body.get("profile")]
    include_secrets = bool(body.get("include_secrets"))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    made = []
    for name in names:
        pdir = profile_dir(name)
        if pdir is None:
            made.append({"profile": name, "ok": False, "reason": "profile not found"})
            continue
        members = [n for n in (*BACKUP_MEMBERS, *SECRET_MEMBERS)
                   if (pdir / n).exists() and (include_secrets or n not in SECRET_MEMBERS)]
        if not members:
            made.append({"profile": name, "ok": False, "reason": "nothing to back up"})
            continue
        f = BACKUPS / f"{name}--{stamp}.tar.gz"
        with tarfile.open(f, "w:gz") as tf:
            for n in members:
                tf.add(pdir / n, arcname=n)
        made.append({"profile": name, "ok": True, "file": f.name, "at": stamp,
                     "bytes": f.stat().st_size, "included": members,
                     "secrets": include_secrets})
        journal({"op": "backup_create", "profile": name, "file": f.name,
                 "secrets": include_secrets})
    return {"ok": all(x.get("ok") for x in made), "created": made, "dir": str(BACKUPS)}


@router.get("/backups")
async def backup_list() -> dict:
    if not BACKUPS.is_dir():
        return {"ok": True, "items": [], "dir": str(BACKUPS)}
    items = []
    for f in sorted(BACKUPS.glob("*.tar.gz"), reverse=True):
        prof, _, at = f.stem.partition("--")
        items.append({"file": f.name, "profile": prof, "at": at,
                      "bytes": f.stat().st_size})
    return {"ok": True, "items": items, "dir": str(BACKUPS)}


def _safe_backup_members(tf, allowed: set) -> tuple[list, str | None]:
    """Every member must be inside the profile and be a plain file or directory.

    `extractall` trusts the archive by default. The previous guard tested only
    `member.split("/")[0]`, so a member like `SOUL.md/../../../tmp/pwn` PASSED it (its top
    segment is `SOUL.md`) and then escaped — while the error string it would have raised,
    "refusing: {member} escapes the profile", claimed a check that had not happened.

    A link member is the other half of the hole: it satisfies any name check and then
    redirects every later write through it.
    """
    out = []
    for m in tf.getmembers():
        parts = Path(m.name).parts
        if Path(m.name).is_absolute() or ".." in parts:
            return [], f"refusing: {m.name} escapes the profile"
        top = parts[0] if parts else ""
        if top not in allowed:
            return [], f"refusing: {m.name} is not one of the backed-up members"
        if m.issym() or m.islnk():
            return [], f"refusing: {m.name} is a link, not a plain file"
        out.append(m)
    return out, None


@router.post("/backups/restore")
async def backup_restore(body: dict = Body(...)) -> dict:
    """Restore a backup over a profile — and make the restore ITSELF reversible.

    The profile's current state is recorded into a trial first, so a restore that turns
    out wrong is undone with the same one call as any other trial.
    """
    import tarfile
    f = BACKUPS / (body.get("file") or "")
    if not f.is_file() or f.parent != BACKUPS:
        return {"ok": False, "error": "no such backup"}
    name = body.get("profile") or f.stem.partition("--")[0]
    pdir = profile_dir(name)
    if pdir is None:
        return {"ok": False, "error": f"profile '{name}' not found"}

    with tarfile.open(f, "r:gz") as tf:
        # Validate EVERY member before anything is written or any trial is opened. A
        # refusal here must leave no trace — the old order began a trial first, so a
        # rejected archive left a live trial with partial ops and a Revert button that
        # undid nothing real.
        members, err = _safe_backup_members(tf, set((*BACKUP_MEMBERS, *SECRET_MEMBERS)))
        if err:
            return {"ok": False, "error": err}

        trial_mod.prune()
        tid = trial_mod.begin(label=f"restore {f.name}")
        n = 0

        # Back up by TOP-LEVEL PATH. Recording per member meant a directory was snapshotted
        # once for every file inside it — `ops` counted members, and the same destination
        # was overwritten again and again, keeping only the last generation.
        tops: list[str] = []
        for m in members:
            top = Path(m.name).parts[0]
            if top not in tops:
                tops.append(top)

        for top in tops:
            dest = pdir / top
            if dest.exists():
                kind = "dir" if dest.is_dir() else "file"
                snap = (trial_mod.backup_dir(tid, dest, n) if kind == "dir"
                        else trial_mod.backup_file(tid, dest, n))
                trial_mod.record(tid, {"kind": kind, "path": str(dest), "existed": True,
                                       "snapshot": snap, "what": f"restore over {top}"})
            else:
                # DIRECTORY when the archive carries anything under it. The old test was
                # `"." not in top`, which calls `.profile-pane` a FILE — so its reverse ran
                # `unlink()` on a directory and the restore could never be undone.
                nested = any(len(Path(m.name).parts) > 1 and Path(m.name).parts[0] == top
                             for m in members)
                is_dir = nested or any(m.isdir() and Path(m.name).parts[0] == top
                                       for m in members)
                trial_mod.record(tid, {"kind": "dir" if is_dir else "file",
                                       "path": str(dest), "existed": False, "snapshot": None,
                                       "what": f"restore created {top}"})
            n += 1

        # Python 3.12+ ships its own guard; on older interpreters the member check above
        # is the guard, so the fallback is not a weaker path.
        try:
            tf.extractall(pdir, filter="data")
        except TypeError:
            tf.extractall(pdir)
    journal({"op": "backup_restore", "profile": name, "file": f.name, "trial": tid, "ops": n})
    return {"ok": True, "profile": name, "file": f.name, "ops": n, "trial": tid}


@router.post("/backups/delete")
async def backup_delete(body: dict = Body(...)) -> dict:
    f = BACKUPS / (body.get("file") or "")
    if not f.is_file() or f.parent != BACKUPS:
        return {"ok": False, "error": "no such backup"}
    size = f.stat().st_size
    f.unlink()
    journal({"op": "backup_delete", "file": f.name, "bytes": size})
    return {"ok": True, "deleted": f.name, "bytes": size}


# ------------------------------------------------------- group overlap

@router.get("/conflicts")
async def conflicts() -> dict:
    """Profiles that sit in MORE THAN ONE group whose enabled layers DISAGREE.

    Nothing stops a profile being a member of several groups: they are independent
    records, and Hermes has no notion of which one owns a profile. Two groups pushing
    different SOUL scopes or layer modes at the same profile is a genuine clash — the
    LAST Apply wins, and with Auto on it becomes a race that re-runs every 15 minutes.
    Detected here so it is visible instead of silent.
    """
    reg = _registry()
    groups = reg.all()
    by_profile: dict[str, list] = {}
    for g in groups:
        pol = g.get("policy", {}) or {}
        for m in g.get("members", []) or []:
            by_profile.setdefault(m, []).append((g["id"], pol))

    out = []
    for prof, gs in sorted(by_profile.items()):
        if len(gs) < 2:
            continue
        clash = {}
        for key in ("soul", "memory", "user", "profile", "skills", "auto"):
            vals = {gid: pol.get(key) for gid, pol in gs
                    if pol.get(key) not in (None, "off", "manual")}
            if len({str(v) for v in vals.values()}) > 1:
                clash[key] = vals
        anchors = {gid: pol.get("anchor") for gid, pol in gs if pol.get("anchor")}
        if len({str(v) for v in anchors.values()}) > 1:
            clash["anchor"] = anchors
        if clash:
            out.append({"profile": prof, "groups": [gid for gid, _ in gs], "clashes": clash})
    return {"ok": True, "conflicts": out, "groups": len(groups),
            "multi_group_profiles": sum(1 for v in by_profile.values() if len(v) > 1)}


# ------------------------------------------------------- secrets (.env key layer)

@router.get("/secrets/{name}")
async def secrets_read(name: str) -> dict:
    """The KEY NAMES in a profile's `.env`, with whether each has a value.

    VALUES ARE NEVER RETURNED — not masked, not truncated. The pane cannot render a
    secret it is never given, which is the only durable way to keep them out of a
    screenshot, a journal line and a log.
    """
    pdir = profile_dir(name)
    if pdir is None:
        return {"ok": False, "error": "profile not found"}
    keys = secrets_mod.read_keys(pdir)
    return {"ok": True, "profile": name, "keys": keys, "count": len(keys),
            "has_env": secrets_mod.env_file(pdir).exists(),
            "path": str(secrets_mod.env_file(pdir))}


@router.get("/drift/{name}")
async def drift_report(name: str) -> dict:
    """The three-hash read for every skill this pane ever applied to a profile.

    This is what makes the pane honest about "sync": it COPIES on Apply and records a
    content hash in the profile's manifest. This endpoint compares the recorded hash
    against the source's CURRENT hash and the destination's CURRENT hash, so drift is
    visible instead of silent.
    """
    pdir = profile_dir(name)
    if pdir is None:
        return {"ok": False, "error": "profile not found"}
    man = apply_mod.read_manifest(pdir)
    entries = []
    for key, rec in man.items():
        if not key.startswith("skills/"):
            continue
        rel = key[len("skills/"):]
        src = Path(rec.get("src") or "")
        dest = pdir / "skills" / rel
        upstream = apply_mod.hash_tree(src) if src.is_dir() else ""
        local = apply_mod.hash_tree(dest) if dest.is_dir() else None
        entries.append({
            "skill": rel, "state": apply_mod.drift(rec, upstream, local),
            "src": str(src), "dest": str(dest), "applied_at": rec.get("applied_at"),
        })
    return {"ok": True, "profile": name, "entries": entries,
            "drifted": [e for e in entries if e["state"] != "In-sync"]}


@router.get("/drift")
async def drift_all() -> dict:
    """Drift across every profile, for a fleet-level view."""
    out = {}
    for p in list_profiles():
        r = await drift_report(p["name"])
        if r.get("ok") and r.get("entries"):
            out[p["name"]] = r["drifted"]
    return {"ok": True, "profiles": out}


# ------------------------------------------------- memory / user-profile / profile

@router.get("/memory/{name}")
async def memory_read(name: str, which: str = "memory") -> dict:
    """Read a profile's durable-fact file as its §-delimited blocks."""
    pdir = profile_dir(name)
    if pdir is None:
        return {"ok": False, "error": "profile not found"}
    if which not in ("memory", "user"):
        return {"ok": False, "error": "which must be 'memory' or 'user'"}
    return {"profile": name, "which": which, **mem_mod.read_blocks(pdir, which)}


def _record_edit_trial(label: str, r: dict, what: str) -> str | None:
    """Register a single-file edit's reverse in a trial, so UNDO LAST covers it.

    Every other write this pane makes is undoable. A hand-edit of MEMORY.md / USER.md was
    the ONE act with no way back — and it edits the user's durable facts, through a
    textarea, from a route whose response already carried the snapshot. The reverse existed
    and was thrown away.
    """
    if not r.get("ok") or r.get("unchanged"):
        return None                       # nothing moved, so there is nothing to reverse
    tid = trial_mod.begin(label=label)
    if r.get("snapshot"):
        trial_mod.record(tid, {"kind": "file", "path": str(r.get("path")), "existed": True,
                               "snapshot": r["snapshot"], "what": what})
    else:
        trial_mod.record(tid, {"kind": "file", "path": str(r.get("path")),
                               "existed": False, "snapshot": None,
                               "what": f"{what} (new file)"})
    trial_mod._save(tid, {**(trial_mod.load(tid) or {}),
                          "summary": {"ops": 1, "results": 1}})
    return tid


@router.post("/memory")
async def memory_write(body: dict = Body(...)) -> dict:
    """Rewrite a block file. Snapshots first — these files have NO CAS, and a
    concurrent append by the running agent would otherwise be lost."""
    name = body.get("profile")
    pdir = profile_dir(name)
    if pdir is None:
        return {"ok": False, "error": "profile not found"}
    which = body.get("which", "memory")
    if which not in ("memory", "user"):
        return {"ok": False, "error": "which must be 'memory' or 'user'"}
    r = mem_mod.write_blocks(pdir, which, body.get("blocks") or [])
    journal({"op": "memory_write", "profile": name, "which": which, **r})
    tid = _record_edit_trial(f"edit {which} \u2192 {name}", r, f"{which} \u2192 {name}")
    return {**r, "profile": name, "which": which, "trial": tid}


# ────────────────────────────────────────────────── one item source, every layer
# The panel used to ask five different questions in five different shapes: soul had a
# heading list, secrets had a key list, and memory / user / profile had NOTHING — they were
# on/off switches over their entire file. That is the incoherence: two layers could be
# configured and three could only be all-or-nothing.
#
# This route answers ONE question — "what can be picked in this layer?" — with ONE shape,
# for every layer. The panel renders one grammar over it, so adding a layer adds a row
# rather than a new widget.
#
# `key` is content-derived wherever the unit is text (a block, a section), so a pick names
# a THING rather than a position: if the anchor's text changes, the key disappears and the
# caller is told, instead of silently receiving a different item.
_MAX_PREVIEW = 160


def _preview(text: str) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= _MAX_PREVIEW else t[: _MAX_PREVIEW - 1] + "\u2026"


@router.get("/items/{layer}/{name}")
async def layer_items(layer: str, name: str) -> dict:
    """Every pickable item in one layer of one profile, in one shape.

    Returns `{ok, layer, profile, items:[{key, label, preview, chars, meta}], count, unit}`.
    `unit` names what one item IS ("block", "section", "field", "key") so the panel can label
    the checklist without knowing which layer it is drawing.
    """
    pdir = profile_dir(name)
    if pdir is None:
        return {"ok": False, "error": "profile not found", "items": [], "count": 0}

    items: list[dict] = []
    unit = "item"

    if layer in ("memory", "user"):
        unit = "block"
        r = mem_mod.read_blocks(pdir, layer)
        for b in r.get("blocks", []):
            items.append({"key": b["key"], "label": _preview(b["text"]),
                          "preview": _preview(b["text"]), "chars": b["chars"],
                          "meta": {"i": b["i"]}})

    elif layer == "profile":
        # The profile entry is a small record of scalars, not a list of prose. Each FIELD is
        # one pickable item — which is what makes "sync the description but not the rest"
        # expressible at all.
        unit = "field"
        entry = mem_mod.read_profile_entry(pdir)
        for field in ("description", "description_auto"):
            if field not in entry:
                continue
            val = entry.get(field)
            items.append({"key": field, "label": field,
                          "preview": _preview(str(val or "")), "chars": len(str(val or "")),
                          "meta": {"field": field, "empty": not val}})

    elif layer == "soul":
        unit = "section"
        text = soul_mod.read_soul(pdir)
        for s in soul_mod.split_sections(text):
            body = s.get("text") or ""
            items.append({"key": s.get("title") or "", "label": s.get("title") or "",
                          "preview": _preview(body), "chars": len(body),
                          "meta": {"level": s.get("level")}})

    elif layer == "secrets":
        unit = "key"
        for k in secrets_mod.read_keys(pdir):
            items.append({"key": k.get("key"), "label": k.get("key"),
                          "preview": "has a value" if k.get("has_value") else "empty",
                          "chars": 0, "meta": {"has_value": bool(k.get("has_value"))}})

    elif layer == "models":
        # Same envelope, same item shape as every other layer — which is the point. The panel
        # renders this checklist without knowing it is looking at config.yaml.
        unit = "setting"
        r = models_mod.read_selection(pdir)
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error"), "items": [], "count": 0}
        for it in r["items"]:
            items.append(it)

    else:
        return {"ok": False, "error": f"unknown layer {layer!r}", "items": [], "count": 0}

    return {"ok": True, "layer": layer, "profile": name, "unit": unit,
            "items": items, "count": len(items)}


@router.post("/block")
async def block_edit(body: dict = Body(...)) -> dict:
    """Edit ONE block of ONE profile, addressed by its content key.

    The whole-file route (`/memory`) already exists and is safe, but it needs the caller to
    send every block back — so a client that read a stale copy silently reverts whatever the
    running agent appended in the meantime. This route takes the edit alone:

      {profile, which, key, text}            -> replace the block whose key matches
      {profile, which, key: null, text}      -> append a new block
      {profile, which, key, text: null}      -> delete that block

    Read-modify-write happens HERE, against the file as it is at this moment, and it goes
    through the same snapshot + trial path every other write uses — so Undo last covers a
    hand-edit of the durable facts exactly as it covers an Apply.
    """
    name = body.get("profile")
    pdir = profile_dir(name)
    if pdir is None:
        return {"ok": False, "error": "profile not found"}
    which = body.get("which", "memory")
    if which not in ("memory", "user"):
        return {"ok": False, "error": "which must be 'memory' or 'user'"}

    key = body.get("key")
    text = body.get("text")
    cur = mem_mod.read_blocks(pdir, which).get("blocks", [])
    blocks = [b["text"] for b in cur]

    if key is None:
        if not (text or "").strip():
            return {"ok": False, "error": "nothing to append"}
        blocks.append(text)
        what = f"add a block to {which} \u2192 {name}"
    else:
        hit = [b for b in cur if b.get("key") == key]
        if not hit:
            # A key that resolves to nothing is a REFUSAL, never a silent append: the caller
            # asked to change a specific block, and changing a different one instead is the
            # one outcome they cannot detect.
            return {"ok": False, "error": "that block is no longer in the file",
                    "reason": "it changed since it was read — reopen the list and re-pick"}
        i = hit[0]["i"]
        if text is None:
            blocks.pop(i)
            what = f"delete a block from {which} \u2192 {name}"
        else:
            if not text.strip():
                return {"ok": False, "error": "a block cannot be emptied — delete it instead"}
            blocks[i] = text
            what = f"edit a block in {which} \u2192 {name}"

    r = mem_mod.write_blocks(pdir, which, blocks)
    if not r.get("ok"):
        return r
    journal({"op": "block_edit", "profile": name, "which": which, **r})
    tid = _record_edit_trial(f"{what} ({len(blocks)} blocks)", r, what)
    return {**r, "profile": name, "which": which, "trial": tid, "action": what}


@router.get("/profile-entry/{name}")
async def profile_entry_read(name: str) -> dict:
    pdir = profile_dir(name)
    if pdir is None:
        return {"ok": False, "error": "profile not found"}
    return {"profile": name, **mem_mod.read_profile_entry(pdir)}


@router.post("/profile-entry")
async def profile_entry_write(body: dict = Body(...)) -> dict:
    name = body.get("profile")
    pdir = profile_dir(name)
    if pdir is None:
        return {"ok": False, "error": "profile not found"}
    r = mem_mod.write_profile_entry(pdir, body.get("description") or "")
    journal({"op": "profile_entry_write", "profile": name, **r})
    tid = _record_edit_trial(f"description \u2192 {name}", r, f"description \u2192 {name}")
    return {**r, "profile": name, "trial": tid}


@router.get("/all-skills")
async def all_skills() -> dict:
    """EVERY skill on the machine — the union across profiles, with where each installs.

    The library shows this whole set. Whether a given profile already HAS a skill is a
    fact about that profile, not a filter on the list: the pane marks what a profile
    already has and leaves the rest available to add.
    """
    seen: dict[str, dict] = {}
    for p in list_profiles():
        for s in skill_inventory(Path(p["path"])):
            e = seen.setdefault(s["rel"], {"rel": s["rel"], "name": s["name"], "in": []})
            e["in"].append(p["name"])
    skills = sorted(seen.values(), key=lambda x: x["rel"])
    present = [p["name"] for p in list_profiles()]
    return {"ok": True, "skills": skills, "total": len(skills),
            "profile_count": len(present),
            "orphans": [s["rel"] for s in skills if not s["in"]]}


