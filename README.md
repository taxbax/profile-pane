# profile-pane

**Cross-agent configuration as a desktop pane.** Stage skills onto Hermes agents and keep
SOUL / memory / user / profile layers matched to a group's *anchor* — on demand, or on a
schedule.

![version](https://img.shields.io/badge/version-0.1.0--alpha-informational)
![license](https://img.shields.io/badge/license-MIT-blue)
![platform](https://img.shields.io/badge/platform-macOS-lightgrey)

---

## What it is

profile-pane is a native [Hermes](https://hermes-agent.nousresearch.com/docs) desktop pane at
`~/.hermes/plugins/profile-pane/`. You do not hand-edit `SOUL.md` or `MEMORY.md` across nine
profiles by hand; you pick **which member is canonical** and the pane copies FROM it INTO the
others.

### The anchor model (the one idea)

The pane never asks you to *author* a SOUL. It asks which member is canonical
(`policy.anchor`), and every layer copies from that anchor into the others. A design that
takes SOUL text as input is wrong — it puts authoring on you and has no source of truth.

| SOUL scope | what lands in each member |
|---|---|
| `whole` | the anchor's file, byte for byte — members become identical |
| `blocks` | the anchor's text spliced under ownership markers; each member's own text kept |
| `section` | only the `##` sections you tick, spliced the same way |

`blocks` / `section` splice into `<!-- profile-pane:shared:begin --> … end`. A re-apply
*replaces* that region — it never grows a second copy — which is what makes a schedule safe to
leave running.

---

## Status

**Alpha (0.1.0).** Honest limits, stated up front:

- Every end-to-end run has been **sandboxed** — it has not been applied to a live profile.
- The `auto` switch ships **OFF** on every group; turning it on is a per-group decision.
- The scheduled job is **silent by design** — it reports only when it actually moved something.

---

## What it syncs

| layer | source | `push` | `append` |
|---|---|---|---|
| **Skills** | the union of all profiles | copy + manifest (never symlink) | n/a |
| **SOUL** | `SOUL.md` (whole-file, **no CAS**) | `whole` / `blocks` / `section` scope | n/a |
| **Memory** | `memories/MEMORY.md` (`§`-block file) | member identical to anchor | anchor entries present, member keeps extras |
| **User** | `memories/USER.md` (`§`-block file) | member identical to anchor | anchor entries present, member keeps extras |
| **Profile** | `profile.yaml` — the `description` scalar only | description = anchor's | n/a — one scalar |
| **Secrets** | key list (advisory) | reported, never forced | n/a |

`ui_meta` in `profile.yaml` is **never touched** — the gateway CAS-guards it per key, and this
plugin writes only the authored `description`.

`append` is how a member keeps its differences. It is deterministic, so it re-runs to a no-op
like everything else.

---

## Two ways it runs

- **Apply** (button) — copies staged skills and pushes the enabled layers, once.
- **Auto** (per group) — `tools/sync_once.py` re-runs the group via the `profile-pane
  auto-sync` cron job (every 15m, `no_agent`, silent when nothing moved).

**The manifest is the desired state.** An Apply records the skills it copied and their source;
the reconciler re-copies only those, only when the source has moved. A first run on an
untouched profile is a no-op.

---

## Guardrails

- **Fixed point.** Writes are idempotent — an unchanged file is not rewritten (no snapshot
  litter, no mtime churn).
- **Snapshot before every write.** SOUL and memory files have no CAS and are not atomic; the
  snapshot is the reverse.
- **Secret scan still blocks.** A refused copy is reported, never forced. Auto-sync is not a
  bypass around the gate manual Apply respects.
- **Manifest-bounded.** Nothing syncs that the pane did not already apply once.
- **Dry run is the default.** `dry_run` must be `false` to write.
- **No guessed source.** An absent anchor is refused, never defaulted to "the first profile."
- **The anchor is never written to itself.**

---

## Install

```bash
git clone https://github.com/taxbax/profile-pane.git ~/.hermes/plugins/profile-pane
```

Then enable it: **Settings → Plugins → profile-pane**. The Python half mounts once at gateway
start; the JS half hot-reloads from `desktop-plugins/`.

---

## Operating it

Open the pane, then:

- **ACTIVITY bar** (top) lights accent when work is staged — it never opens itself. Staged
  skills list, then **Preview** (writes nothing) and **Apply** (writes, records its own
  reverse).
- **SYNC POLICY** — group tabs with members, the layer toggles, and the detail for whichever
  layer has any.
- **SKILLS | PROFILES** flip — the library and the member list.

The library is **the machine, not a profile**: `/all-skills` returns the union of every
profile's deployed skills, marked against the focused profile — **bold + ✓** means already
there, plain means available. Each skill resolves to whichever profile actually has it.

Copies are **paired, never a cross-product** — a request carries `pairs: [{profile, skill,
op}]`, and `/plan` reads the same pairs, so a preview cannot report a different count than the
apply it guards.

The try-it flow is **Apply → Undo last** (ACTIVITY ▸ Commits). Undo names *what* it would
reverse. A **Trial** applies for real, then reverts — the only way to watch a subject behave
under a change without keeping it.

Layout:

```
ACTIVITY          staged N          (the bar LIGHTS UP; it never opens itself)
  [Staged (N)] [Commits (N)]
    Membership (n) · Policy (n) · staged skill rows
    [Preview] [Apply]

SYNC POLICY       (folds to one summary line; GROUP TABS head the panel)
  Group   [operators] [+ group]   rename · delete
  Members [★sys ×] [trad ×] + add
  1 · WHICH LAYERS MOVE   Auto · Skills · Memory · User · Profile · Secrets
  2 · DETAIL FOR THE ROWS THAT HAVE ANY   the Secrets key list, the Soul sections

SKILLS | PROFILES  (flip ⇅ and refresh ⟳ live in the ACTIVITY bar)
```

The layout encodes "**decide what moves, then tune how**": every layer toggle is in block 1;
every per-layer detail is in block 2, under the row it belongs to. `Apply` is enabled only when
something is actually pending.

---

## Architecture

The Python half is a thin FastAPI router; every rule lives in `panecore`. The desktop half is a
single JS file. ~5,800 lines across the tree, 33 routes, 120 titled controls.

```
profile-pane/
├── dashboard/
│   ├── plugin_api.py        # thin FastAPI router (33 routes)
│   └── panecore/
│       ├── apply.py         # the only module that writes copies — snapshot, secret scan, manifest
│       ├── soul.py          # anchor render + idempotent splice
│       ├── memory.py        # §-blocks + the profile description
│       ├── sync.py          # the reconciler (manifest = desired state)
│       ├── trial.py         # apply-for-real-then-revert
│       ├── groups.py        # the group / member model
│       ├── secrets.py       # advisory key-list surface
│       ├── models.py        # request / response shapes
│       └── paths.py         # profile + skill path resolution
├── desktop/
│   └── plugin.js            # the pane
├── tools/
│   ├── audit.mjs            # undefined-identifier + invariant + glyph audit
│   └── sync_once.py         # standalone reconcile entrypoint (the cron job)
├── tests/                   # pure-logic suite + the router half
├── skill/SKILL.md           # the operating skill, bundled with the plugin
├── DELIVERY.md              # full delivery notes, wiring, and honest limits
├── plugin.yaml
├── LICENSE
└── README.md
```

---

## Development

```bash
cd ~/.hermes/plugins/profile-pane
node --check desktop/plugin.js                        # syntax
node tools/audit.mjs desktop/plugin.js                # undefined idents + invariants + glyph colour
python3 -m pytest tests/ -q                           # pure-logic suite (system python)
~/.hermes/hermes-agent/venv/bin/python tests/test_crud.py   # the router half (needs FastAPI)
```

**Read the skip.** `test_crud.py` needs FastAPI, which lives only in the Hermes venv — under a
plain `python3 -m pytest` the entire module *skips*, and that one skipped line is 54 route
tests. Both commands are listed so a third of the suite is not silently dropped. The venv has
FastAPI but no pytest, so `test_crud.py` carries a small pytest-free runner and is invoked
directly.

Run the two commands and trust the printed numbers — the test count has gone stale before.

The Python half mounts once at gateway start (new routes 404 until the desktop app is quit and
reopened). The JS half hot-reloads from `desktop-plugins/` — after editing
`desktop/plugin.js`, mirror it over:

```bash
cp desktop/plugin.js ~/.hermes/desktop-plugins/profile-pane/plugin.js
```

---

## License

[MIT](LICENSE) © 2026 taxbax