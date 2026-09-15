# profile-pane — delivery

Native Hermes desktop pane at `~/.hermes/plugins/profile-pane/`. Cross-agent
configuration: stage skills onto agents, and keep SOUL / memory / user / profile
layers matched to a group's **anchor** — on demand or on a schedule.

## The anchor model

The pane never asks you to author a SOUL. It asks **which member is canonical**, and
every layer copies FROM that anchor INTO the others.

| SOUL scope | what lands in each member |
|---|---|
| `whole` | the anchor's file, byte for byte — members become identical |
| `blocks` | the anchor's text spliced under ownership markers; each member's own text kept |
| `section` | only the `##` sections you tick, spliced the same way |

`blocks`/`section` splice into `<!-- profile-pane:shared:begin --> … end`. A re-apply
**replaces** that region, so it never grows a second copy — which is what makes a
schedule safe to leave running.

Anchors on this machine, by **line count** (not bytes — this block used to say "by size"
while printing lines, and named three profiles that do not exist: the real directories are
`schizsho-system-builder`, `schizsho-production` and `schizsho-showrunner`).
**Measured 2026-09-13, after the SOUL sync:**

`sys` 3412 · `media-control-bot` 1799 · `default` 1704 · `schizsho-visual-dev` 195 ·
`schizsho-story-room` 28 · `schizsho-system-builder` 23 · `schizsho-production` 22 ·
`schizsho-showrunner` 22 · `trad` 12.

Two of these moved and the block did not: `sys` was `1705` and `media-control-bot` was `92`
before each received a full SOUL. **A line count is a *measurement*, so it goes stale the
moment the file changes — re-measure it, never carry it forward.**

## Two ways it runs

- **Apply** (button) — copies staged skills and pushes the enabled layers, once.
- **Auto** (per group) — `tools/sync_once.py` re-runs the group on a schedule via the
  `profile-pane auto-sync` cron job (every 15m, `no_agent`, silent when nothing moved).

**The manifest is the desired state.** An Apply records the skills it copied and their
source; the reconciler re-copies only those, only when the source has moved. It has no
opinion about the rest of the machine — a first run on an untouched profile is a no-op.

## Guardrails

- **Fixed point.** Writes are idempotent; an unchanged file is not rewritten (no
  snapshot litter, no mtime churn). Verified: pass 1 = N changes, every later pass = 0.
  This needed two fixes to become true: byproducts (`__pycache__`, `.DS_Store`) were
  hashed as content, AND `copytree(dirs_exist_ok=True)` never removes extras — so the hash
  said "different" and the copy could never make it "same". One predicate now drives both
  the hash and the prune, and the reconcile prunes to match.
- **Snapshot before every write.** SOUL and the memory files have no CAS and are not
  atomic — the snapshot is the reverse. This is now true of the SCHEDULED path too: the
  reconcile retains a tree under `profile-pane/snapshots/` before it overwrites. Snapshots
  inherit the source's mode (a `0600` `.env` no longer becomes a `0644` copy of itself) and
  are pruned on a 72h window. Memory and description edits register their reverse in a
  trial, so `Undo last` covers them like everything else.
- **Secret scan still blocks.** A refused copy is reported, never forced. Auto-sync is
  not a bypass around the gate manual Apply respects.
- **Manifest-bounded.** Nothing is synced that the pane did not already apply once.
- **Dry run is the default in the API.** `dry_run` must be `false` to write.
- **No guessed source.** An absent anchor is refused, never defaulted to "the first
  profile" — that would silently copy a random agent's memory over the group.
- **The anchor is never written to itself.**

## Verified wiring

- **Memory block files** — `memories/MEMORY.md` and `USER.md`: separate entries split on a
  line containing exactly `§`. Measured on this machine: the root profile holds 62 / 28,
  `sys` holds 12 / 14. (This block used to claim 43 / 23, which no file produces.)
- **SOUL** — `profiles.configure{soul}` / direct file write; whole-file, **no CAS**.
- **`profile.yaml`** — only the authored `description`. The gateway's `ui_meta` carries
  per-key CAS and is **never touched** here.
- **Skills** — copy + manifest, never symlink. (`shutil.copytree`, `apply.py`.) The reason
  is that Hermes rejects symlinks in a profile distribution. This line used to cite
  `_reject_distribution_symlinks` as if it were code in the plugin — that symbol appears
  **nowhere** in it, so the citation proved nothing about what this code does.

## Verification

```
node --check desktop/plugin.js                                  ✅
node tools/audit.mjs desktop/plugin.js                           ✅ no undefined identifiers
python3 -m pytest tests/ -q          (system python, no fastapi) ✅ 132 passed, 1 SKIPPED
~/.hermes/hermes-agent/venv/bin/python tests/test_crud.py       ✅ 57 passed
                                                                   ─────────────
                                                                   189 tests
```

**Read the skip.** `test_crud.py` needs FastAPI, which lives only in the Hermes venv — so
under a plain `python3 -m pytest` the entire module **skips**, and that one skipped line is
54 route tests. That is why both commands are listed: the first alone reports 132 and
silently drops a third of the suite. The venv has FastAPI but no pytest, so `test_crud.py`
carries a small pytest-free runner and is invoked directly.

This block has gone stale twice — first claiming `64 passed` (stale by 111), then `123/52`
(stale by 11). **Run the two commands and paste the real numbers; do not carry them forward.**

**33 routes · 120 titled controls · 0 undefined identifiers · 0 Electron dialogs ·
2 invariant classes hold** (state semantics + chrome-glyph colour).

`node tools/audit.mjs` now reports **three** things: undefined identifiers, `INVARIANTS`
(assertions about what a state signal *means*), and `CHROME_GLYPHS` (every chevron and filter
glyph carries an explicit colour). Both invariants exist because real bugs were invisible to
the other checks — `lit = pending > 0 || trialLive` kept the Activity bar green after every
Apply, and a bare `jsx(Icon, ...)` left the Sync Policy chevron accent-green while its siblings
were muted. **Each is proven both ways** — exit 0 on the fixed file, exit 1 on the bug.

**Read the exit code without a pipe.** `node tools/audit.mjs | tail` returns `tail`'s status,
not node's, so a failing check reads as `exit=0`.

End-to-end (sandboxed, real router): anchor sections read → `whole` copy byte-identical
→ `block` keeps the member's own text with exactly one region → re-apply idempotent →
`section` copies only the ticked heading → memory pushed from the anchor → no-anchor
refused → sync settles at 0 changes.

## The library is the machine, not a profile

`/all-skills` returns the **union** of every profile's deployed skills. The library shows
that whole set and marks it against the focused profile:

- **Bold + ✓** — already in that profile. Clicking is a no-op; nothing to add.
- **Plain** — available. One click stages the add.
- **The number on a bold chip** — how many profiles share that skill.

Each skill is resolved to whichever profile actually has it (`find_skill_source`). It used
to resolve every skill against one `source_root`, so a skill living only in another profile
had no `SKILL.md` under that root and the copy failed — exactly the bug the union library
would have exposed.

**Copies are PAIRED, never a cross-product.** A request carries `pairs: [{profile, skill,
op}]`. It used to carry two flat lists (`profiles: [...]`, `skills: [...]`) which the server
multiplied — so staging `SkillA → X` and then `SkillB → Y` copied BOTH skills to BOTH
profiles, four copies where the pane showed two. `/plan` reads the same pairs, so a preview
cannot report a different count than the apply it guards.

## Layer modes

| mode | memory / user (`§`-block files) | profile (`profile.yaml`) |
|---|---|---|
| `push` | member becomes identical to the anchor | member's description = the anchor's |
| `append` | the anchor's entries are guaranteed present, the member **keeps its own extras** (they trail the anchor's) | n/a — one scalar, nothing to append to |

`append` is how a member keeps its differences. It is deterministic, so it re-runs to a
no-op like everything else.

## Layout

```
ACTIVITY          staged N          (the bar LIGHTS UP — accent fill, reversed text —
  [Staged (N)] [Commits (N)]         it never opens itself)
    Membership (n) · Policy (n) · the staged skill rows
    [Preview] [Apply]

SYNC POLICY       (folds to one summary line; the GROUP TABS head the panel)
  Group   [operators] [+ group]   rename · delete
  Members [★sys ×] [trad ×] + add
  1 · WHICH LAYERS MOVE   Auto · Skills · Memory · User · Profile · Secrets
  2 · DETAIL FOR THE ROWS THAT HAVE ANY   the Secrets key list, the Soul sections

SKILLS | PROFILES  (flip ⇅ and refresh ⟳ live in the ACTIVITY bar)
```

Four orderings that are deliberate, because each was wrong before:
- **Profiles sits above Skills** by default; ⇅ lifts either.
- **Activity is topmost** and its buttons live inside it.
- **Staging LIGHTS the bar, it never opens it.** A panel that opens under your cursor
  while you are working elsewhere moves the thing you were about to click.
- **The layout encodes "decide what moves, then tune how."** Every layer toggle is in
  block 1; every per-layer detail is in block 2, under the row it belongs to.

`Preview` runs the same preflight `Apply` does and **writes nothing**; `Apply` writes and
records its own reverse, so `Apply → Undo last` (Activity ▸ Commits) is the try-it flow.
`Apply` is enabled only when something is actually pending.

## Polling

**Every** query polls — all ten of them: profiles, groups, trial, all-skills 5s · journal
6s · backups, conflicts, drift, soul, secrets 15s. `soul` and `secrets` were the only two
with no interval, so a new `##` heading or a new `.env` key in the anchor never appeared.
Only the **Python** half needs a restart, and only when routes change.

## Honest limits

- **Never applied to a live profile.** Every end-to-end run has been sandboxed.
- **`auto` ships OFF on every group.** The switch exists; no group is set to on. Turning
  it on is a per-group decision you make in the pane.
- **The scheduled job is silent by design** — it reports only when it actually changed
  something.
