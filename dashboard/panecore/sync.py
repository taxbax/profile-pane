"""Auto-sync — the reconcile pass that keeps a group's members matching their anchor.

THE MANIFEST IS THE DESIRED STATE. An Apply records the skills it copied and the
source each came from (`<profile>/.profile-pane/manifest.json`). This pass re-copies
an entry only when its source has MOVED, and re-pushes the group's enabled layers
from the anchor. A skill this pane never applied is never synced by it — the reconciler
has no opinion about the rest of the machine.

Properties that make it safe to leave running on a schedule:

  * **Manifest-bounded.** Only recorded entries are considered, so a first run is a
    no-op and the blast radius is exactly what the user already applied once.
  * **Snapshot before every write.** SOUL and the memory files have no CAS and are not
    atomic; the snapshot is the reverse, and it is taken before the bytes move.
  * **Secret scan still blocks.** A copy the scanner refuses is reported, never forced.
    Auto-sync is not a bypass around the gate manual Apply respects.
  * **Local divergence is reported before it is overwritten.** The three-hash state is
    recorded on the op, so the journal shows what was replaced and the snapshot makes
    it reversible.
  * **Dry run is the default in the API.** `dry_run=False` must be asked for.

A pass that changes nothing is the healthy outcome, not a failure to act.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import apply as apply_mod
from . import memory as mem_mod
from . import soul as soul_mod
from . import secrets as secrets_mod
from . import models as models_mod

LAYERS = ("memory", "user", "profile", "models")

# The Hermes ROOT is itself a profile, named `default`. Every other profile lives at
# `<profiles>/<name>`. The reconcile walked `profiles_root / name` for ALL of them, so a
# group containing `default` wrote its SOUL/memory/.env into `<profiles>/default/` — a
# phantom directory the root never reads. `/trial` resolves through the profile list and
# wrote to the right place, so the button and the schedule DISAGREED about where the
# default profile lives. Set by the caller; falls back to the naive join.
DEFAULT_HOME: Path | None = None


def home_of(name: str, profiles_root: Path) -> Path:
    """The profile's actual home. The root profile is NOT under `profiles/`."""
    if name == "default" and DEFAULT_HOME is not None:
        return Path(DEFAULT_HOME)
    return Path(profiles_root) / name


def reconcile_skills(profile_dir: Path, *, dry_run: bool = False) -> list[dict]:
    """Re-copy every manifested skill whose source has moved. Never invents entries."""
    profile_dir = Path(profile_dir)
    try:
        man = apply_mod.read_manifest(profile_dir)
    except apply_mod.ManifestCorrupt as e:
        # The pass REFUSES rather than proceeding. Continuing would copy skills and then
        # fail inside write_manifest, leaving copies on disk with no record of them —
        # untracked forever, and never reconciled again.
        return [{"skill": "*", "action": "refused", "reason": str(e)}]
    ops: list[dict] = []
    for key in sorted(man):
        if not key.startswith("skills/"):
            continue
        rel = key[len("skills/"):]
        rec = man[key]
        src = Path(rec.get("src") or "")
        dest = profile_dir / "skills" / rel

        if not src.is_dir():
            ops.append({"skill": rel, "action": "skipped", "reason": "source no longer exists",
                        "src": str(src)})
            continue

        upstream = apply_mod.hash_tree(src)
        local = apply_mod.hash_tree(dest) if dest.exists() else None
        state = apply_mod.drift(rec, upstream, local)

        if state == "In-sync":
            ops.append({"skill": rel, "action": "none", "state": state})
            continue
        if dry_run:
            ops.append({"skill": rel, "action": "would-recop", "state": state,
                        "src": str(src)})
            continue

        # `prune` so the pass CONVERGES: without it a single extra file in the copy made
        # the hash differ again next pass, and the reconcile re-copied every 15 minutes
        # forever. `snapshot_before` because this is a write and every write here needs a
        # reverse — this is the scheduled path, which has no trial to belong to.
        r = apply_mod.apply_skill(src, dest, force=True, prune=True, snapshot_before=True)
        if r.get("unchanged"):
            # Nothing actually moved. Reporting "recopied" here is what made a healthy
            # pass indistinguishable from a churning one.
            ops.append({"skill": rel, "action": "none", "state": "In-sync"})
            continue
        op = {"skill": rel, "action": "recopied" if r.get("ok") else "refused",
              "state": state, **r}
        if r.get("ok"):
            apply_mod.write_manifest(profile_dir, key, {"hash": r["hash"], "src": str(src)})
        ops.append(op)
    return ops


def _push_layer(layer: str, src_dir: Path, dest_dir: Path, *, dry_run: bool,
                mode: str = "push", picks: list[str] | None = None) -> dict:
    """mode: 'push' makes the member match the anchor; 'append' guarantees the anchor's
    entries are present and keeps whatever extras the member already had.

    `picks` narrows WHICH items move — a list of content keys from `/items/{layer}/{name}`.
    `None` means every item (the All setting); an empty list means none, which is a real
    answer and not the same thing as None. The two modes compose with it:

      push + picks   -> the member ends up holding EXACTLY the chosen items
      append + picks -> the chosen items are guaranteed present; the member keeps its extras
    """
    if dry_run:
        return {"action": "would-push", "layer": layer, "mode": mode,
                "picked": None if picks is None else len(picks)}
    if layer in ("memory", "user"):
        anchor = mem_mod.read_blocks(src_dir, layer).get("blocks", [])
        if picks is not None:
            want = set(picks)
            anchor_blocks = [b["text"] for b in anchor if b.get("key") in want]
        else:
            anchor_blocks = [b["text"] for b in anchor]
        if mode == "append":
            mine = [b["text"] for b in mem_mod.read_blocks(dest_dir, layer).get("blocks", [])]
            blocks = mem_mod.merge_blocks(mine, anchor_blocks)
        else:
            blocks = anchor_blocks
        r = mem_mod.write_blocks(dest_dir, layer, blocks)
    elif layer == "profile":
        # `description` is a single scalar — there is nothing to append to, so append
        # degrades to push here and the pane does not offer the option. `picks` is the only
        # way to narrow it, since the entry is a record of fields rather than a list.
        src = mem_mod.read_profile_entry(src_dir)
        if picks is not None:
            chosen = {f: src.get(f, "") for f in picks if f in src}
        else:
            chosen = {k: src.get(k, "") for k in ("description", "description_auto") if k in src}
        r = mem_mod.write_profile_entry(
            dest_dir,
            chosen.get("description", mem_mod.read_profile_entry(dest_dir).get("description", "")))
    elif layer == "models":
        # `config.yaml`'s provider/model SELECTION. Distinct from secrets: no credential
        # lives here (the key is in `.env`), but pushing it changes what every member RUNS
        # ON — so it is named, offered, and reversible rather than silent. Only the keys
        # this layer owns move; a member's other tuning survives.
        r = models_mod.push(src_dir, dest_dir, picks=picks, dry_run=False)
    else:
        return {"action": "skipped", "layer": layer, "reason": f"unknown layer '{layer}'"}
    return {"action": "unchanged" if r.get("unchanged") else "pushed",
            "layer": layer, "mode": mode,
            "picked": None if picks is None else len(picks), **r}


def sync_group(group: dict, present: list[str], profiles_root: Path, *,
               dry_run: bool = True) -> dict:
    """Reconcile one group: anchored layers, then the manifested skills of each member."""
    profiles_root = Path(profiles_root)
    gid = group.get("id")
    policy = group.get("policy", {}) or {}
    members = [m for m in group.get("members", []) if m in present]
    missing = [m for m in group.get("members", []) if m not in present]
    source = policy.get("anchor")
    src_dir = home_of(source, profiles_root) if source and source in present else None

    out: dict = {"group": gid, "dry_run": dry_run, "members": members,
                 "missing": missing, "anchor": source, "layers": {}, "skills": {}}

    # ── the anchored layers ──────────────────────────────────────────────────────
    for layer in ("soul", *LAYERS):
        if layer == "soul":
            scope = policy.get("soul", "off")
            if scope == "off":
                continue
            if src_dir is None:
                out["layers"]["soul"] = {"action": "skipped",
                                         "reason": "no anchor selected for this group"}
                continue
            anchor_text = soul_mod.read_soul(src_dir)
            res = []
            for m in members:
                if m == source:
                    res.append({"profile": m, "action": "skipped", "reason": "is the anchor"})
                    continue
                if dry_run:
                    res.append({"profile": m, "action": "would-write", "scope": scope})
                    continue
                r = soul_mod.apply_scope(home_of(m, profiles_root), anchor_text, scope,
                                         policy.get("soul_sections") or [])
                action = "refused" if not r.get("ok") else (
                    "unchanged" if r.get("unchanged") else "wrote")
                res.append({"profile": m, "action": action, **r})
            out["layers"]["soul"] = {"action": "ran", "scope": scope, "results": res}
            continue

        # Two orthogonal axes, so the panel shows two small controls rather than a ladder
        # of combined states:
        #   mode  (policy[layer])        = off | push | append   — HOW to apply
        #   scope (policy[layer_scope])  = all | pick            — WHICH items
        # An existing record carries no `<layer>_scope`, so it defaults to `all` and behaves
        # exactly as it did before this existed. Adding granularity must not change the
        # meaning of a record already on disk.
        setting = policy.get(layer) or "off"
        if setting not in ("push", "append"):
            continue
        if src_dir is None:
            out["layers"][layer] = {"action": "skipped",
                                    "reason": "no anchor selected for this group"}
            continue
        mode = setting
        scope = policy.get(f"{layer}_scope") or "all"
        picks = list(policy.get(f"{layer}_picks") or []) if scope == "pick" else None
        res = []
        for m in members:
            if m == source:
                continue
            res.append({"profile": m, **_push_layer(layer, src_dir, home_of(m, profiles_root),
                                                    dry_run=dry_run, mode=mode, picks=picks)})
        out["layers"][layer] = {"action": "ran", "mode": mode, "results": res}

    # ── the .env KEY layer ───────────────────────────────────────────────────────
    # Values move; they are never RETURNED, so this block can be logged and journalled
    # without a secret ever entering a log line or a UI payload. `push` takes every key
    # the anchor holds, `granular` takes only the ticked ones.
    sec_mode = policy.get("secrets", "off")
    if sec_mode in ("push", "granular"):
        if src_dir is None:
            out["layers"]["secrets"] = {"action": "skipped",
                                        "reason": "no anchor selected for this group"}
        else:
            sec_keys = ([k["key"] for k in secrets_mod.read_keys(src_dir) if k["has_value"]]
                        if sec_mode == "push" else (policy.get("secret_keys") or []))
            res = []
            for m in members:
                if m == source:
                    res.append({"profile": m, "action": "skipped", "reason": "is the anchor"})
                    continue
                if dry_run:
                    res.append({"profile": m, "action": "would-write", "keys": sec_keys})
                    continue
                r = secrets_mod.push(src_dir, home_of(m, profiles_root), sec_keys)
                action = "refused" if not r.get("ok") else (
                    "unchanged" if r.get("unchanged") else "wrote")
                res.append({"profile": m, "action": action,
                            **{k: v for k, v in r.items() if k != "snapshot"}})
            out["layers"]["secrets"] = {"action": "ran", "mode": sec_mode,
                                        "keys": sec_keys, "results": res}

    # ── the manifested skills, per member ────────────────────────────────────────
    for m in members:
        ops = reconcile_skills(home_of(m, profiles_root), dry_run=dry_run)
        if ops:
            out["skills"][m] = ops
    return out


def sync_all(groups: list[dict], present: list[str], profiles_root: Path, *,
             dry_run: bool = True, only_auto: bool = True) -> dict:
    """The reconcile pass over a set of groups.

    `only_auto` is the difference between the two callers. The scheduled pass (no group
    named) must respect each group's Auto setting — that is what Auto MEANS. A pass that
    names ONE group is a human asking for that group by name, so it runs regardless:
    pressing "sync now" and getting silence because a switch elsewhere is off is a
    control that lies about what it does.
    """
    started = time.time()
    considered = [g for g in groups if not only_auto or (g.get("policy", {}) or {}).get("auto") == "on"]
    results = [sync_group(g, present, profiles_root, dry_run=dry_run) for g in considered]
    changed = sum(
        1
        for r in results
        for ops in r["skills"].values()
        for o in ops if o["action"] in ("recopied", "would-recop")
    ) + sum(
        1
        for r in results
        for v in r["layers"].values()
        for o in v.get("results", [])
        if o.get("action") in ("wrote", "would-write", "would-push", "pushed")
    )
    return {
        "ok": True, "dry_run": dry_run, "at": started,
        "groups_considered": len(considered),
        "groups_total": len(groups), "changes": changed, "groups": results,
    }
