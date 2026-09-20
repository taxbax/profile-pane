---
name: profile-pane
description: "Use when operating the profile-pane desktop plugin to stage skills and sync SOUL / memory / user / profile across grouped agents."
version: 1.0.0
author: taxbax
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [hermes, profiles, desktop, pane, sync, config]
---

# profile-pane — cross-agent configuration

A native Hermes desktop pane at `~/.hermes/plugins/profile-pane/`. It stages skills
onto agents and keeps SOUL / memory / user / profile layers matched to a group's
**anchor** — on demand, or on a schedule. The pane is the agent-assisted way to
edit profiles: you do not hand-edit `SOUL.md` or `MEMORY.md` by hand across nine
machines; you pick which member is canonical and the pane copies FROM it INTO the
others.

## The anchor model (the one idea)

The pane never asks you to **author** a SOUL. It asks **which member is canonical**
(`policy.anchor`), and every layer copies from that anchor into the others. A design
that takes SOUL text as input is wrong — it puts authoring on you and has no source
of truth.

| scope | what lands in each member |
|---|---|
| `whole` | the anchor's file, byte for byte — members become identical |
| `block` | the anchor's text spliced under `<!-- profile-pane:shared:begin/end -->`; each member's own text is kept |
| `section` | only the ticked `##` sections, spliced the same way |

`block`/`section` splice is **idempotent** — a re-apply replaces the marked region, so
it never grows a second copy. That property is what makes a schedule safe to leave
running.

## Operating it

Open the pane (Settings → Plugins → **profile-pane** on), then:

- **ACTIVITY bar** (top) lights accent when work is staged. Staged skills list, then
  **Preview** (writes nothing) and **Apply** (writes, records its own reverse).
- **SYNC POLICY** — group tabs with members, the four/layer toggles, and the detail
  for whichever layer has any.
- **SKILLS | PROFILES** flip — the library and the member list.

The layout encodes "decide what moves, then tune how": every layer toggle is in block
1; every per-layer detail is in block 2, under the row it belongs to. `Apply` is
enabled only when something is actually pending.

### Layers and modes

| layer | `push` | `append` |
|---|---|---|
| memory / user (`§`-block files) | member identical to anchor | anchor's entries guaranteed present, member keeps its own extras |
| profile (`profile.yaml`) | description = anchor's | n/a — one scalar |

`append` is how a member keeps its differences. It is deterministic, so it re-runs to
a no-op like everything else.

### The library is the machine, not a profile

`/all-skills` returns the **union** of every profile's deployed skills, marked against
the focused profile: **bold + ✓** = already there (clicking is a no-op); **plain** =
available, one click stages the add. Each skill resolves to whichever profile actually
has it, so a skill living only in another profile still copies correctly.

**Copies are PAIRED, never a cross-product.** A request carries `pairs: [{profile,
skill, op}]`. The server never multiplies two flat lists — staging `A → X` then
`B → Y` copies each to its own target, not both to both.

### Trial, undo, and reversibility

- **Preview** runs the same preflight Apply does and writes nothing.
- **Apply → Undo last** (ACTIVITY ▸ Commits) is the try-it flow. Undo names *what* it
  would reverse (which act, which paths, whether each existed before).
- **Trial** applies for real, then reverts — the only way to watch a subject actually
  behave under a change without keeping it. A live trial is single-use (once reverted
  it refuses to run again) and reverses in LIFO order.

## Auto-sync (the scheduled pass)

The cron job `profile-pane auto-sync` (every 15m, `no_agent`, silent when nothing
moved) re-runs each enabled group via `tools/sync_once.py --apply --quiet`. Silence
means "checked, nothing to do". A group's `auto` switch ships **OFF** by default —
turning it on is a per-group decision.

## Hard rules

1. **Never default a sync source.** An absent anchor must be REFUSED, never fall back
   to `profiles[0]` (which would silently copy a random agent's memory over the group).
2. **The anchor is never written to itself.**
3. **Snapshot before every write.** SOUL and memory files have no CAS and are not
   atomic — the snapshot is the reverse.
4. **Secret scan blocks.** A refused copy is reported, never forced; auto-sync is not a
   bypass around the gate manual Apply respects.
5. **Manifest-bounded.** Nothing is synced that the pane did not already apply once.

## Verification (run before trusting it)

```bash
cd ~/.hermes/plugins/profile-pane
node --check desktop/plugin.js                    # syntax
node tools/audit.mjs desktop/plugin.js            # undefined idents + invariants + glyph colour
python3 -m pytest tests/ -q                       # pure-logic suite (system python)
~/.hermes/hermes-agent/venv/bin/python tests/test_crud.py   # the router half (needs FastAPI)
cp desktop/plugin.js ~/.hermes/desktop-plugins/profile-pane/plugin.js   # mirror the JS!
```

The Python half mounts **once at gateway start** — new routes 404 until the desktop app
is quit and reopened. The JS half hot-reloads from `desktop-plugins/` (a copied file,
not the source) — after editing `desktop/plugin.js`, copy it over or the pane runs
stale JS.

## Pitfalls that cost real time

- **`jsxs(type, props, key)` — the 3rd argument is the KEY, never children.** A children
  array there renders an EMPTY row with no error; `node --check` can't see it, the arity
  audit can.
- **`window.prompt()` is not implemented in Electron** — it returns `undefined`, so a
  button guarded on the value silently does nothing. Use an inline input.
- **Reconcile loops must reach a fixed point.** Every writer returns `unchanged: True`
  and does not touch the file; verify as `pass1 > 0, pass2 == 0`.
- **`profile.yaml` `ui_meta` is CAS-guarded** — write only the `description` scalar,
  never a YAML round-trip.
- **Memory files are `§`-block files** — a lone `§` line separates entries. Split on
  that line, not blank lines.
- **The two copies of `plugin.js`** (source vs `desktop-plugins/`) drift silently — see
  the mirror command above.

## Shape of the code

- `dashboard/panecore/apply.py` — the only module that writes copies; snapshot, secret scan, manifest
- `dashboard/panecore/soul.py` — anchor render + idempotent splice
- `dashboard/panecore/memory.py` — `§`-blocks + the profile description
- `dashboard/panecore/sync.py` — the reconciler (manifest = desired state)
- `dashboard/plugin_api.py` — thin router; all rules live in panecore
- `desktop/plugin.js` — the pane
- `tools/audit.mjs` — undefined-identifier + invariant + glyph audit
- `tools/sync_once.py` — the standalone reconcile entrypoint