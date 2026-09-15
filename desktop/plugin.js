// profile-pane — native Hermes desktop pane for cross-profile configuration.
//
// PARADIGMS BORROWED FROM THE MEETING PANEL (meeting-overlay/desktop/plugin.js).
// Everything below is deliberately the same shape as that pane, because it is the
// one surface in this app a user already knows how to drive:
//
//   1. toolbar      (fixed)              label · counts · flip · refresh · feedback (abs-positioned)
//   2. group bar    (fixed)              segmented chip row — one click switches group
//   3. policy card  (FIXED, never scrolls) the PRIMARY config: what this group syncs
//   4. LIBRARY box  (bordered, OWN scroll) search + skill chips — drag OR click to stage
//   5. AGENTS box   (bordered, OWN scroll) focus a target; expand a row to inspect it
//   6. staged bar   (fixed, capped)       the queue, newest last
//   7. action bar   (fixed, bottom edge)  Preview · Apply
//
// The two boxes are SEPARATE scroll owners with a shared border each — the meeting
// pane's structural move (its transcript and its records list never share a scroller).
// The ⇅ toggle swaps them, exactly like the meeting pane's deck flip, so whichever
// list you are working in can sit on top.
//
// The policy card is NOT inside either scroller — same reason the meeting pane holds
// its answer card out of the transcript: a primary surface that can scroll away is a
// primary surface the user loses.
//
// FRICTION RULE: drag is an ACCELERANT, never the only path. Click an agent to focus
// it, then single-click skills to stage them. Both routes reach the same queue.
//
// Rules (disk plugin): only @hermes/plugin-sdk / react / react/jsx-runtime resolve;
// NO JSX on disk (jsx/jsxs only); theme vars only; small type; stroke icons, no emoji.
//
// TOOLTIP COVERAGE (standing rule — the meeting panel's convention): EVERY control
// and every value readout carries a `title`. A control's title says what it DOES;
// a readout's title says what the number MEANS. No instructional prose is rendered
// inline — guidance lives in the tooltip. New controls must ship with their title.

import { PANES_AREA, haptic, useQuery, queryClient } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useRef, useState, useEffect } from 'react'

const rest = { fn: null }
const os = { fn: null }

const faint = 'var(--ui-text-quaternary)'
const tert = 'var(--ui-text-tertiary)'
const sec = 'var(--ui-text-secondary)'
const accent = 'var(--ui-accent)'
const stroke = 'var(--ui-stroke-secondary)'
const red = 'var(--ui-red, #e5484d)'
const warn = '#d19a66'
// Accent-filled buttons: text = panel background, so it reads as native Hermes
// contrast instead of a glowing white-on-green slab (meeting panel's trick).
const onAccent = 'var(--ui-background, var(--ui-bg-chrome))'

const labelStyle = {
  fontSize: '9px', fontWeight: 700, letterSpacing: '0.07em',
  textTransform: 'uppercase', color: faint, lineHeight: 1,
}

// Section chrome icons — the collapse chevrons and the filter glyphs — share ONE muted
// colour. These drifted: the Sync Policy chevron carried no colour wrapper, so it inherited
// currentColor from the header and rendered accent-green while Backups and Skills rendered
// muted. One named style makes the class impossible to drift again: a chrome icon either
// uses chromeIcon or it is visibly wrong in review.
const chromeIcon = { display: 'flex', flex: '0 0 auto', color: faint }

const SOUL_SCOPES = [
  { id: 'off', label: 'Off', hint: 'SOUL is not touched in this group.' },
  { id: 'block', label: 'Blocks', hint: 'Append chosen rule blocks under ownership markers; all other text untouched.' },
  { id: 'section', label: 'Sections', hint: 'Sync only the sections you tick; all other text untouched.' },
  { id: 'whole', label: 'Whole', hint: 'Members become byte-identical — per-bot role identity is OVERWRITTEN.' },
]
const PUSH_MODES = [
  { id: 'off', label: 'Off', hint: 'This layer is never written to a member.' },
  { id: 'push', label: 'Push', hint: 'The member becomes IDENTICAL to the anchor (snapshot first — reversible).' },
]

// Secrets has a third state the other layers do not: WHOLE vs GRANULAR. `push` copies
// every key the anchor has; `granular` copies only the ones you tick, which is the
// difference between "give every member my keyring" and "give this member what it needs".
const SECRET_MODES = [
  { id: 'off', label: 'Off', hint: 'No .env key is ever written to a member.' },
  { id: 'push', label: 'Push', hint: 'Copy EVERY key the anchor has into each member. Broad — a member gains keys that have nothing to do with it.' },
  { id: 'granular', label: 'Granular', hint: 'Copy ONLY the keys you tick, in the list that opens below. Merged, so a member keeps its own keys.' },
]
// memory/user are BLOCK files, so a member can keep its own extras while still being
// guaranteed the anchor's entries. `append` is that mode; `profile.yaml` has only a
// single scalar description, so it has nothing to append to and offers Off|Push.
const PUSH_APPEND_MODES = [
  { id: 'off', label: 'Off', hint: 'This layer is never written to a member.' },
  { id: 'push', label: 'Push', hint: 'The member becomes IDENTICAL to the anchor — its own entries are dropped (snapshot first).' },
  { id: 'append', label: 'Append', hint: 'The anchor’s entries are guaranteed present and the member KEEPS its own extras, trailing the anchor’s. This is how a member keeps its own differences.' },
]
// What each journal op means, in words — the Commits view reads the log back.
const Tab = (on, hot) => ({
  fontSize: '9.5px', fontWeight: on ? 700 : 500, height: '18px', padding: '0 7px',
  borderRadius: '9px', cursor: 'pointer', userSelect: 'none', WebkitUserSelect: 'none',
  border: '1px solid ' + (on ? accent : stroke),
  background: on ? 'color-mix(in srgb, var(--ui-accent) 14%, transparent)' : 'transparent',
  color: on ? accent : (hot ? accent : tert),
})

const OP_META = {
  apply_skill: ['copy', 'copied'], remove_skill: ['trash', 'removed'],
  restore_skill: ['check', 'restored'], reapply: ['refresh', 're-applied'],
  apply_soul: ['check', 'soul written'], push_layer: ['check', 'layer pushed'],
  memory_write: ['check', 'memory edited'], profile_entry_write: ['check', 'description set'],
  trial_revert: ['refresh', 'REVERTED'], backup_create: ['check', 'backup made'],
  backup_restore: ['refresh', 'restored from backup'], backup_delete: ['trash', 'backup deleted'],
  skill_rename: ['check', 'renamed'], rename_group: ['check', 'group renamed'],
  undo_restore: ['refresh', 'undone'], undo_delete: ['trash', 'undone (file removed)'],
}
const whenOf = (t) => {
  try { return new Date((t || 0) * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) }
  catch (e) { return '' }
}

const AUTO_MODES = [
  { id: 'off', label: 'Off', hint: 'Nothing runs on a schedule. Pressing Apply is the only time anything moves.' },
  { id: 'on', label: 'On', hint: 'A scheduled pass (every 15m) re-copies anything that drifted and re-pushes the enabled layers from the anchor.' },
]
const SKILL_MODES = [
  { id: 'manual', label: 'Manual', hint: 'Nothing moves unless you stage it and press Apply.' },
  { id: 'sync', label: 'Sync', hint: 'Mark this group as sync-managed. Apply still copies — Auto is what re-runs it.' },
]
// Each layer's consequence, stated rather than decided. The pane does not refuse any
// of these — it reports what the write does and where the reverse lives.
const LAYER_META = {
  memory: {
    label: 'Memory',
    hint: 'memories/MEMORY.md — this agent’s accumulated notes (the §-block file). Push copies the SOURCE profile’s notes over each member’s, so every member inherits every fact. That is real context weight: watch the “ln” budget on each row.',
    short: 'MEMORY.md · accumulated notes',
  },
  user: {
    label: 'User',
    hint: 'memories/USER.md — what the agent knows about you (§-block file). Push makes every member carry the identical profile of you.',
    short: 'USER.md · who you are',
  },
  models: {
    label: 'Models',
    hint: 'config.yaml’s provider/model SELECTION — the provider and the model name this agent runs on. NOT a credential: the API key lives in .env, which is the Secrets layer. Pushing this changes what every member RUNS ON, so a member whose provider has no key for the pushed model will fail at its next call. Only these keys move; each member’s other config (max_turns, personalities, its own base_url) is left alone.',
    short: 'config.yaml · which model it runs on',
  },
  profile: {
    label: 'Profile',
    hint: 'profile.yaml — the authored one-line description (agent lists, Bot Chat headers). Push copies it. The gateway’s own ui_meta is never touched.',
    short: 'profile.yaml · the description',
  },
}

// Lucide-style stroke icons — no truncation, no font dependency, no emoji.
const ICON = {
  refresh: ['M21 12a9 9 0 1 1-3-6.7', 'M21 3v6h-6'],
  folder: ['M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z'],
  plus: ['M12 5v14', 'M5 12h14'],
  grip: ['M9 6h.01', 'M9 12h.01', 'M9 18h.01', 'M15 6h.01', 'M15 12h.01', 'M15 18h.01'],
  check: ['M20 6 9 17l-5-5'],
  alert: ['M12 9v4', 'M12 17h.01', 'M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z'],
  trash: ['M3 6h18', 'M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2', 'M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6'],
  play: ['M6 3l14 9-14 9z'],
  search: ['M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z', 'M21 21l-4.3-4.3'],
  target: ['M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z', 'M12 18a6 6 0 1 0 0-12 6 6 0 0 0 0 12z', 'M12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4z'],
  flip: ['M7 4v13', 'M4 14l3 3 3-3', 'M17 20V7', 'M14 10l3-3 3 3'],
  chevron: ['M9 18l6-6-6-6'],
  down: ['M6 9l6 6 6-6'],
  reveal: ['M2 3h20a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H2a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z', 'M8 21h8', 'M12 17v4'],
}

function Icon({ name, size = 13, fill = false }) {
  const paths = ICON[name] || []
  return jsx('svg', {
    width: size, height: size, viewBox: '0 0 24 24',
    fill: fill ? 'currentColor' : 'none',
    stroke: fill ? 'none' : 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round',
    style: { flexShrink: 0, display: 'block' },
    children: paths.map((d, i) => jsx('path', { key: i, d })),
  })
}

// Segmented control — the meeting panel's Chip: equal flex, the active entry is
// unmistakable. `clearable` = clicking the ACTIVE entry clears it (the meeting pane's
// persisted-mode idiom: `toggleMode = k => setAssistMode(assistMode === k ? 'off' : k)`).
function Seg({ options, value, onPick, clearable }) {
  // A label must never be the thing that gives. `overflow:hidden` + `ellipsis` silently
  // turns "Sections" into "Sectio…", which reads as a different word — so the options
  // SHRINK to fit instead: more than three entries get a smaller face and tighter
  // padding, which is enough room for the longest label this pane uses.
  const tight = options.length > 3
  return jsx('div', {
    style: { display: 'flex', gap: tight ? '3px' : '4px' },
    children: options.map(o => {
      const selected = value === o.id
      const hint = clearable && selected ? `${o.hint || o.label} — click again to turn this OFF` : (o.hint || o.label)
      return jsx('button', {
        key: o.id, title: hint, 'aria-label': hint,
        onClick: () => { haptic('tap'); onPick(clearable && selected ? null : o.id) },
        style: {
          flex: 1, minWidth: 0, height: '20px', padding: tight ? '0 2px' : '0 5px', borderRadius: '5px',
          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '4px',
          cursor: 'pointer', userSelect: 'none', WebkitUserSelect: 'none',
          fontSize: tight ? '8.5px' : '9.5px', fontWeight: selected ? 700 : 500,
          border: '1px solid ' + (selected ? accent : stroke),
          background: selected ? 'color-mix(in srgb, var(--ui-accent) 14%, transparent)' : 'transparent',
          color: selected ? accent : tert,
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        },
        children: o.label,
      })
    }),
  })
}

// Prose-weight action (meeting panel's InlineAction) — no border, no background.
// `stop` prevents a row-level click (focus) from also firing when you hit the row's action.
const InlineAction = ({ label, onClick, hint, tone, stop }) =>
  jsx('span', {
    onClick: e => { if (stop) { e.stopPropagation(); e.preventDefault() } haptic('tap'); onClick(e) },
    title: hint || label,
    style: {
      fontSize: '10px', color: tone === 'red' ? red : faint, cursor: 'pointer',
      whiteSpace: 'nowrap', userSelect: 'none', WebkitUserSelect: 'none',
    }, children: label,
  })

// A labelled readout — the profile card's "what is this?" row.
const Fact = ({ label, value, hint, tone }) =>
  jsxs('span', {
    title: hint,
    style: { display: 'inline-flex', alignItems: 'baseline', gap: '3px', fontSize: '9px',
             padding: '1px 5px', borderRadius: '4px', border: '1px solid ' + stroke },
    children: [
      jsx('span', { style: { color: faint, textTransform: 'uppercase', letterSpacing: '.05em', fontSize: '8px' }, children: label }),
      jsx('span', { style: { color: tone === 'red' ? red : tone === 'warn' ? warn : sec, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }, children: value }),
    ],
  })

const Empty = ({ children, hint }) =>
  jsx('div', { title: hint, style: { fontSize: '10.5px', color: faint, fontStyle: 'italic', padding: '2px 0' }, children })

// A bordered box that OWNS its scroll — the meeting pane's structural unit.
// `style` merges over the defaults, so a box can be collapsed (flex: 0 0 auto) or
// carry an accent edge without forking the component.
const Box = ({ children, order, style }) =>
  jsx('div', {
    style: {
      order, flex: '1 1 0', minHeight: 0, display: 'flex', flexDirection: 'column',
      border: '1px solid ' + stroke, borderRadius: '7px', overflow: 'hidden',
      ...(style || {}),
    }, children,
  })

// A box's fixed header row.
const BoxHead = ({ children, style }) =>
  jsx('div', {
    style: { flex: '0 0 auto', display: 'flex', alignItems: 'center', gap: '5px', padding: '4px 6px', borderBottom: '1px solid ' + stroke, ...(style || {}) },
    children,
  })

// A box's scroll owner.
const BoxBody = ({ children, style }) =>
  jsx('div', {
    style: { flex: '1 1 0', minHeight: 0, overflowY: 'auto', padding: '5px 6px', ...style }, children,
  })

// ------------------------------------------------------------------------------ pane

function Pane() {
  const [gid, setGid] = useState(null)
  const [focusAgent, setFocusAgent] = useState(null)
  const [expanded, setExpanded] = useState(null)   // agent row expanded into a card
  const [staged, setStaged] = useState([])
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState(null)
  const [q, setQ] = useState('')                   // skills filter
  const [aq, setAq] = useState('')                 // agents filter
  const [flipped, setFlipped] = useState(true)     // true = PROFILES on top (the default)
  const [addingMembers, setAddingMembers] = useState(false)
  const [editingDesc, setEditingDesc] = useState(null)   // profile whose description is open
  const [descDraft, setDescDraft] = useState('')
  const [trash, setTrash] = useState(null)      // { profile, items } for the open card
  const [driftFor, setDriftFor] = useState(null)   // profile whose drift list is open
  const [backupsOpen, setBackupsOpen] = useState(false)
  const [backupSecrets, setBackupSecrets] = useState(false)
  const [renamingSkill, setRenamingSkill] = useState(null)
  const [skillNameDraft, setSkillNameDraft] = useState('')
  const [libOpen, setLibOpen] = useState(false)   // library opens when you pick a profile
  const [stagedOpen, setStagedOpen] = useState(false)
  const [activityTab, setActivityTab] = useState('staged')   // 'staged' | 'commits'
  const [renaming, setRenaming] = useState(null)             // group id being renamed
  const [renameDraft, setRenameDraft] = useState('')
  // Policy edits are STAGED too: the controls show the pending value, and Apply is
  // what writes the group record. Nothing about a group changes before you commit it.
  const [pendingPolicy, setPendingPolicy] = useState(null)
  // Block edits STAGE here rather than writing on the spot. Everything else in this pane
  // stages and commits on Apply; a lone direct write was the one control that behaved
  // differently, which is exactly what made it feel like it "committed without staging".
  // Each entry: {layer, key, text|null, label}
  const [pendingBlocks, setPendingBlocks] = useState([])
  const [pendingMembers, setPendingMembers] = useState(null)
  const [armingDelete, setArmingDelete] = useState(false)
  const [newName, setNewName] = useState(null)     // null = idle; string = naming draft
  const [audit, setAudit] = useState(null)         // { profile, which } being audited
  const [auditBlocks, setAuditBlocks] = useState([])   // editable §-blocks
  const [auditDesc, setAuditDesc] = useState('')       // editable profile description
  const [auditErr, setAuditErr] = useState(null)
  const [lastSync, setLastSync] = useState(null)   // when this session last ran the pass
  const [policyOpen, setPolicyOpen] = useState(false)  // SYNC POLICY starts folded
  const noteTimer = useRef(null)

  const dragRef = useRef(null)
  const hoverRef = useRef(null)
  const rowRefs = useRef({})
  const justDragged = useRef(false)
  const [ghost, setGhost] = useState(null)
  const [hover, setHover] = useState(null)

  const { data } = useQuery({
    queryKey: ['profile-pane', 'profiles'],
    queryFn: () => rest.fn('/profiles'), refetchInterval: 5000,
  })
  const { data: gdata } = useQuery({
    queryKey: ['profile-pane', 'groups'],
    queryFn: () => rest.fn('/groups'), refetchInterval: 5000,
  })

  // EVERY skill on the machine — the union across profiles. The library shows this
  // whole set; a profile's own skills are MARKED, and the rest stay available to add.
  // Polled, so a skill written mid-session shows up without restarting the app.
  const [trial, setTrial] = useState(null)   // a live trial's id, if one is running
  // One Apply can create SEVERAL trials — the group/skill trial from /trial PLUS one per
  // staged block edit from /block. Undo must reverse all of them, in reverse order, or the
  // block edits survive a revert the user believed covered the whole Apply.
  const [applyTrials, setApplyTrials] = useState([])
  const { data: trialData } = useQuery({
    queryKey: ['profile-pane', 'trial'],
    queryFn: () => rest.fn('/trial/live'), refetchInterval: 5000,
  })
  const trialLive = !!(trial || (trialData && trialData.live))
  const { data: journalData } = useQuery({
    queryKey: ['profile-pane', 'journal'],
    queryFn: () => rest.fn('/journal?limit=80'), refetchInterval: 6000,
  })
  const commits = journalData?.entries || []
  // What Undo would reverse. The route returns the trial's label and one entry per path;
  // this is the list the Commits view renders so Undo is a decision, not a gamble.
  const whatUndo = trialData?.what || []
  const { data: backupData } = useQuery({
    queryKey: ['profile-pane', 'backups'],
    queryFn: () => rest.fn('/backups'), refetchInterval: 15000,
  })
  const backups = backupData?.items || []
  const { data: conflictData } = useQuery({
    queryKey: ['profile-pane', 'conflicts'],
    queryFn: () => rest.fn('/conflicts'), refetchInterval: 15000,
  })
  const conflicts = conflictData?.conflicts || []

  const { data: allData } = useQuery({
    queryKey: ['profile-pane', 'all-skills'],
    queryFn: () => rest.fn('/all-skills'), refetchInterval: 5000,
  })
  const allSkills = allData?.skills || []

  const profiles = data?.profiles || []
  // Declared HERE, not down beside the Skills box: `const` is hoisted but uninitialised,
  // so any earlier read throws a TDZ ReferenceError and blanks the render.
  const profilesView = profiles.filter(p => !aq || p.name.toLowerCase().includes(aq.toLowerCase()))
  const groups = gdata?.groups || []
  const group = groups.find(g => g.id === gid) || groups[0] || null
  const focusProfile = profiles.find(p => p.name === focusAgent) || null
  // Membership is STAGED exactly like the policy, because the group record is ONE
  // object and the two halves belong to one commit. Enrolling a profile is not
  // cosmetic: on a group with Auto on, the schedule starts writing the anchor's SOUL,
  // memory and .env keys into it — so it gets the same review as flipping a layer.
  const members = pendingMembers ?? (group?.members || [])
  const policy = pendingPolicy || group?.policy || {}
  const policyDirty = !!pendingPolicy
  const soulScope = policy.soul || 'off'
  const scopeHint = SOUL_SCOPES.find(s => s.id === soulScope)?.hint || ''
  // The non-skill layers this group pushes. Empty = Apply only moves skills.
  const layerPush = () => [
    ...['memory', 'user', 'profile'].filter(l => (policy[l] || 'off') === 'push'),
    ...((policy.soul || 'off') !== 'off' ? ['soul'] : []),
    ...(['push', 'granular'].includes(policy.secrets) ? ['secrets'] : []),
  ]
  // Apply is live when there is something staged OR the selected group pushes a layer.

  const { data: driftData } = useQuery({
    queryKey: ['profile-pane', 'drift'],
    queryFn: () => rest.fn('/drift'), refetchInterval: 15000,
  })
  const driftBy = driftData?.profiles || {}   // { profile: [drifted entries] }

  // The anchor's SOUL headings — the checklist the `section` scope picks from. The pane
  // never asks the user to type SOUL text; it asks which member is canonical.
  const { data: anchorSoul } = useQuery({
    queryKey: ['profile-pane', 'soul', policy.anchor],
    queryFn: () => rest.fn('/soul/' + policy.anchor),
    enabled: !!policy.anchor,
    retry: false, refetchInterval: 15000,
  })
  const anchorSections = anchorSoul?.sections || []

  // The anchor's .env KEY NAMES, for the picker. The endpoint returns names and whether
  // a value is present — never the value, so nothing here can render a secret.
  const { data: anchorSecrets, isError: secretsUnavailable } = useQuery({
    queryKey: ['profile-pane', 'secrets', policy.anchor],
    queryFn: () => rest.fn('/secrets/' + policy.anchor),
    // Fetch whenever there IS an anchor: the picker is how you decide what to move, so
    // it must be visible BEFORE the layer is switched on — not only after.
    enabled: !!policy.anchor,
    retry: false, refetchInterval: 15000,
  })
  const anchorKeys = anchorSecrets?.keys || []

  // ─ WHICH items sync, per layer ──────────────────────────────────────────────
    // Every layer answers the same two questions with the same two controls:
    //   mode  = Off | Push | Append      (HOW to apply)          — policy[layer]
    //   scope = All  | Pick              (WHICH items)           — policy[layer_scope]
    // One grammar for five layers. Previously soul and secrets had a picker and memory /
    // user / profile did not, so three layers could only be all-or-nothing.
    //
    // Items come from ONE route for every layer, so the checklist does not care which layer
    // it is drawing.
    //
    // INSPECT PROFILE — which profile's items you are looking at. It DEFAULTS to the group's
    // anchor, because that is what a group copies FROM. But it is NOT gated on the group: the
    // first version required an anchor, so with no group selected the list rendered
    // "No anchor" and nothing else — you could not see or edit your own durable facts
    // without creating a group first. Reading your own facts must not depend on a group
    // existing. Choosing a different profile here is INSPECTION ONLY — it changes what the
    // list shows, never what syncs, which is always the anchor.
    const [inspect, setInspect] = useState(null)
    const [pickOpen, setPickOpen] = useState(null)      // which layer's list is expanded
    // A focused layer OWNS the pane: the boxes below collapse to their headers so the
    // detail gets every pixel. Closing the layer restores them. This is the collapsible-
    // window bargain — nothing is hidden, everything is one click away.
    const boxesCollapsed = pickOpen !== null
    const [editBlock, setEditBlock] = useState(null)    // {key, text} — the block being edited
    const inspectProfile = inspect || policy.anchor || null
    const { data: itemData, isFetching: itemBusy } = useQuery({
      queryKey: ['profile-pane', 'items', pickOpen, inspectProfile],
      queryFn: () => rest.fn(`/items/${pickOpen}/${inspectProfile}`),
      // Fetches whenever a layer is OPEN — no anchor required. The anchor only decides the
      // DEFAULT profile, never whether the list can be read at all.
      enabled: !!pickOpen && !!inspectProfile,
      retry: false, refetchInterval: 20000,
    })
    const pickItems = itemData?.items || []

  const picksOf = (layer) => policy[`${layer}_picks`] || []
  const scopeOf = (layer) => policy[`${layer}_scope`] || 'all'
  // Saving a pick writes the WHOLE group record (POST /groups is a whole-record write), so
  // policy and membership commit together against one snapshot — same as every other edit.
  const setPicks = (layer, keys) => void saveGroup({
    policy: { ...policy, [`${layer}_scope`]: 'pick', [`${layer}_picks`]: keys },
  })
  // `setScope` is GONE. Every caller now goes through `setPicks`, which sets the scope to
  // 'pick' AND seeds the selection in one write — so no path lands in pick mode with an
  // empty list, which is what made one click look like a mass deselect.
  const togglePick = (layer, key) => {
    const cur = picksOf(layer)
    setPicks(layer, cur.includes(key) ? cur.filter(k => k !== key) : [...cur, key])
  }

  const runSync = async (dry) => {
    setBusy(true)
    try {
      const r = await rest.fn('/sync', { method: 'POST', body: { group: group.id, dry_run: dry } })
      const n = r.changes || 0
      flash(dry ? (n ? `dry run: ${n} change(s) waiting` : 'dry run: nothing to do')
                : (n ? `synced ${n} change(s)` : 'already in sync'), 'check', 5000)
      setLastSync(dry ? null : new Date().toLocaleTimeString())
      await invalidate()
    } catch (e) { flash('sync failed', 'check', 5000) } finally { setBusy(false) }
  }

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['profile-pane'] })
  // Transient feedback, mirroring the meeting panel's flash(): spinner in flight,
  // check when done. Absolutely positioned, so it never resizes the toolbar.
  const flash = (text, kind = 'spin', ms = 12000) => {
    setNote({ text, kind })
    if (noteTimer.current) clearTimeout(noteTimer.current)
    noteTimer.current = setTimeout(() => setNote(null), ms)
  }
  useEffect(() => () => { if (noteTimer.current) clearTimeout(noteTimer.current) }, [])

  // A POLICY patch is STAGED, not written: the panel shows the pending value and Apply
  // is what commits it. A structural patch (membership) writes immediately — that is
  // group data, and nothing propagates from it until an Apply anyway.
  const saveGroup = async (patch) => {
    if (!group) return
    if (patch.policy) {
      setPendingPolicy(prev => ({ ...(prev || group.policy || {}), ...patch.policy }))
      // No setStagedOpen here: staging LIGHTS the Activity bar, it does not open it.
      return
    }
    setBusy(true)
    try {
      await rest.fn('/groups', { method: 'POST', body: { ...group, ...patch, resolved: undefined } })
      await invalidate()
    } catch (e) { flash('save failed', 'check', 4000) } finally { setBusy(false) }
  }

  // Write a profile's description through the SAME writer the push layer uses —
  // snapshot first, only the description scalar, never a YAML round-trip.
  const saveDesc = async (name) => {
    setBusy(true)
    try {
      const r = await rest.fn('/profile-entry', { method: 'POST', body: { profile: name, description: descDraft } })
      flash(r.unchanged ? 'description unchanged' : `description saved → ${name}`, 'check', 4000)
      setEditingDesc(null)
      await invalidate()
    } catch (e) { flash('save failed', 'check', 4000) } finally { setBusy(false) }
  }

  // Rename a skill UNIVERSALLY. Its path is its identity, so this is machine-wide by
  // design — a per-profile rename would fork one skill into two.
  const doSkillRename = async () => {
    if (!renamingSkill) return
    setBusy(true)
    try {
      const r = await rest.fn('/skill-rename', { method: 'POST',
        body: { from: renamingSkill, to: skillNameDraft.trim() } })
      flash(r.ok ? `renamed in ${r.renamed} profile(s) — ${r.to}`
                 : `rename failed — ${r.error || 'see the journal'}`, 'check', 7000)
      if (r.ok) { setRenamingSkill(null); setQ('') }
      await invalidate()
    } catch (e) { flash('rename failed', 'check', 5000) } finally { setBusy(false) }
  }

  const doGroupRename = async () => {
    if (!group) return
    setBusy(true)
    try {
      const r = await rest.fn('/rename-group', { method: 'POST', body: { from: group.id, to: renameDraft } })
      if (r.ok) { setGid(r.to); setRenaming(null); flash(`renamed → ${r.to}`, 'check', 4000) }
      else flash(r.error || 'rename failed', 'check', 5000)
      await invalidate()
    } catch (e) { flash('rename failed', 'check', 4000) } finally { setBusy(false) }
  }

  // Re-apply ONE drifted skill, now. The scheduled pass does this invisibly on a timer;
  // this is the verb for when you can SEE the badge and do not want to wait for a tick.
  const reapplyOne = async (profile, skill) => {
    setBusy(true)
    try {
      const r = await rest.fn('/reapply', { method: 'POST', body: { profile, skill } })
      flash(r.ok ? `re-applied ${skill} → ${profile}` : `could not re-apply — ${r.error || r.reason}`,
            'check', 6000)
      await invalidate()
    } catch (e) { flash('re-apply failed', 'check', 5000) } finally { setBusy(false) }
  }

  const backupCreate = async (which) => {
    setBusy(true); flash('archiving…', 'spin', 120000)
    try {
      const body = which === 'all' ? { all: true, include_secrets: backupSecrets }
                                   : { profile: which, include_secrets: backupSecrets }
      const r = await rest.fn('/backups', { method: 'POST', body })
      const ok = (r.created || []).filter(x => x.ok).length
      const bad = (r.created || []).filter(x => !x.ok)
      flash(`${ok} archive(s) written${bad.length ? ` · ${bad.length} skipped` : ''}`
            + (backupSecrets ? ' — INCLUDING .env and auth.json' : ' — secrets excluded'), 'check', 9000)
      await invalidate()
    } catch (e) { flash('backup failed', 'check', 6000) } finally { setBusy(false) }
  }
  const backupRestore = async (file) => {
    setBusy(true); flash('restoring…', 'spin', 120000)
    try {
      const r = await rest.fn('/backups/restore', { method: 'POST', body: { file } })
      if (r.ok) { setTrial(r.trial); setActivityTab('commits'); setStagedOpen(true)
        flash(`restored ${r.profile} — ${r.ops} item(s). Undo last is in Commits.`, 'check', 10000) }
      else flash(r.error || 'restore failed', 'check', 7000)
      await invalidate()
    } catch (e) { flash('restore failed', 'check', 6000) } finally { setBusy(false) }
  }
  const backupDelete = async (file) => {
    setBusy(true)
    try {
      const r = await rest.fn('/backups/delete', { method: 'POST', body: { file } })
      flash(r.ok ? `deleted ${r.deleted}` : (r.error || 'delete failed'), 'check', 5000)
      await invalidate()
    } catch (e) { flash('delete failed', 'check', 4000) } finally { setBusy(false) }
  }

  const openTrash = async (name) => {
    if (trash && trash.profile === name) { setTrash(null); return }
    try {
      const r = await rest.fn('/trash/' + name)
      setTrash({ profile: name, items: r.items || [] })
    } catch (e) { flash('could not read the trash', 'check', 4000) }
  }
  const restoreSkill = async (name, key) => {
    setBusy(true)
    try {
      const r = await rest.fn('/restore-skill', { method: 'POST', body: { profile: name, trash: key } })
      flash(r.ok ? `restored ${r.skill} → ${name}` : `could not restore — ${r.error}`, 'check', 5000)
      await openTrash(name); await openTrash(name)     // refresh the list
      await invalidate()
    } catch (e) { flash('restore failed', 'check', 4000) } finally { setBusy(false) }
  }


  // NOTE: never window.prompt() here — Electron does not implement it, so it returns
  // undefined and the call silently no-ops. Naming is an INLINE input instead (the
  // meeting panel's record-rename idiom).
  const startNewGroup = () => { haptic('tap'); setNewName('') }
  const commitNewGroup = async () => {
    const id = (newName || '').trim().toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '')
    if (!id) { setNewName(null); return }
    if (groups.some(g => g.id === id)) { flash(`“${id}” already exists`, 'check', 4000); return }
    try {
      await rest.fn('/groups', { method: 'POST', body: { id, members: [], policy: {} } })
      setNewName(null); setGid(id); setStaged([]); setPreview(null); setArmingDelete(false)
      setPolicyOpen(true)        // a brand-new group wants configuring, so open the controls
      await invalidate()
      flash(`group “${id}” created`, 'check', 3200)
    } catch (e) { flash('could not create group', 'check', 4000) }
  }

  const delGroup = async () => {
    if (!group) return
    await rest.fn('/groups/' + group.id, { method: 'DELETE' })
    setArmingDelete(false); setGid(null); setStaged([]); setPreview(null)
    await invalidate()
  }

  // Stage the membership; the commit rides along with the policy, so a member can never
  // land in a group whose policy is still half-configured.
  const toggleMember = (name) => {
    const next = members.includes(name) ? members.filter(m => m !== name) : [...members, name]
    setPendingMembers(next)
  }
  const savedMembers = group?.members || []
  const memberDiff = {
    added: members.filter(m => !savedMembers.includes(m)),
    removed: savedMembers.filter(m => !members.includes(m)),
  }
  const memberChanged = memberDiff.added.length + memberDiff.removed.length

  // Staging is a TOGGLE by click (drag always adds — a deliberate drop is not a
  // deselect gesture). The staged state is ALSO rendered on the chip, so you can see
  // what is queued instead of inferring it from a count.
  const stagedKeys = new Set(staged.map(s => s.skill + '\u0000' + s.profile))
  const stagedBy = new Map(staged.map(s => [s.skill + '\u0000' + s.profile, s]))
  const toggleStage = (skill, agent, op = 'add') => {
    if (!agent) { flash('focus an agent first', 'check', 3500); return }
    const k = skill + '\u0000' + agent
    setStaged(prev => prev.some(s => s.skill + '\u0000' + s.profile === k)
      ? prev.filter(s => s.skill + '\u0000' + s.profile !== k)
      : [...prev, { skill, profile: agent, op }])
    setPreview(null)
  }
  const addStage = (skill, agent) => {
    setStaged(prev => prev.some(s => s.skill === skill && s.profile === agent)
      ? prev : [...prev, { skill, profile: agent, op: 'add' }])
    setPreview(null)
  }

  // Reveal in Finder — the SDK's ctx.os (the meeting pane uses it for its records).
  const reveal = (path) => {
    haptic('tap')
    if (os.fn && os.fn.revealPath) { os.fn.revealPath(path).catch(() => flash('reveal failed', 'check', 3000)); return }
    flash('reveal unavailable', 'check', 3000)
  }

  // ── pointer drag (the meeting panel's proven recipe): pointerdown, then
  // WINDOW-level move/up so the drag tracks after the cursor leaves the chip.
  // Drag state lives in a REF — pointermove outruns React renders, so state
  // would read stale and the grab would never track.
  const onChipDown = (e, skill) => {
    if (e.button !== 0) return
    e.preventDefault()
    dragRef.current = { skill, start: { x: e.clientX, y: e.clientY }, moved: false }
    const onMove = (ev) => {
      const d = dragRef.current
      if (!d) return
      if (!d.moved && Math.abs(ev.clientX - d.start.x) + Math.abs(ev.clientY - d.start.y) < 4) return
      d.moved = true
      setGhost({ x: ev.clientX, y: ev.clientY, label: d.skill.split('/').pop() })
      let over = null
      for (const [name, el] of Object.entries(rowRefs.current)) {
        if (!el) continue
        const r = el.getBoundingClientRect()
        if (ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom) { over = name; break }
      }
      hoverRef.current = over
      setHover(over)
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      const d = dragRef.current
      const over = hoverRef.current
      dragRef.current = null; hoverRef.current = null
      setGhost(null); setHover(null)
      if (d && d.moved) {
        justDragged.current = true
        setTimeout(() => { justDragged.current = false }, 0)
        if (over) { haptic('tap'); addStage(d.skill, over) }
      }
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  // APPLY and TEST are the SAME act: both run the preflight, then /trial — which
  // applies for real and records the reverse of every write. Both are therefore
  // undoable from Activity ▸ Commits. The labels encode INTENT (Test = you mean to
  // look; Apply = you mean to keep); the mechanism is one thing, not two.
  const runCommit = async (intent) => {
    if (applyDisabled) return
    setBusy(true); flash(intent === 'test' ? 'checking…' : 'applying…', 'spin', 150000)
    try {
      const plan = await preflight()
      if (plan.blocked) {
        flash(`${intent === 'test' ? 'test' : 'apply'} ABORTED — ${plan.reason}`, 'check', 9000)
        return
      }
      if ((plan.refusals || []).length) {
        // ABORT rather than half-apply. A partial run leaves the queue in a state you
        // did not choose; the offending item is named so you can drop it and re-apply.
        flash(`${intent === 'test' ? 'test' : 'apply'} ABORTED — ${plan.refusals.length} item(s) would fail: ${refusalSummary(plan.refusals)}`, 'check', 12000)
        return
      }
      const body = {
        profiles: stagedTargets(),
          // PAIRS, not two flat sets. The staged view names a profile per row, and the
          // server multiplies two flat lists — staging SkillA→X and then SkillB→Y would
          // send BOTH skills to BOTH profiles. This carries exactly what the UI displays.
          pairs: staged.map(s => ({ profile: s.profile, skill: s.skill, op: s.op || 'add' })),
        label: intent === 'test' ? 'pane test' : 'pane apply',
      }
      if (group) {
        body.group = group.id
          // Policy AND membership commit together, against one snapshot: the group
          // record is a single object, and a member must never land in a group whose
          // policy is still half-configured.
          if (pendingPolicy) body.commit_policy = { ...(group.policy || {}), ...pendingPolicy }
          if (memberChanged) body.commit_members = members
      }
      const r = await rest.fn('/trial', { method: 'POST', body })
      // The staged BLOCK edits ride the same commit — they are single-profile writes, so
      // they go through /block rather than the group trial, but they land in this same
      // action. One Apply, everything the bar showed pending.
      const blocksRes = await commitBlocks()
      // Track EVERY trial this Apply produced, so one Undo reverses all of it.
      setApplyTrials([r.trial, ...(blocksRes.trials || [])].filter(Boolean))
      setTrial(r.trial)
      setPendingPolicy(null)
      setPendingMembers(null)   // committed — stop shadowing the server's list
      setStaged([]); setPreview(null)
      if (!blocksRes.ok) { flash('apply partly done — group committed, a block write was refused', 'alert', 12000) }
      // Clear the pending-flag FIRST: the queue is emptied in this same batch, so the
      // auto-close effect would otherwise shut the panel on the very next render.
      wasPending.current = false
      setActivityTab('commits'); setStagedOpen(true)   // show what just happened
      flash(intent === 'test'
        ? `trial live · ${r.ops} file(s) changed — Undo last is in Commits`
        : `applied · ${r.ops} file(s) changed — Undo last is in Commits`,
        'check', 12000)
      await invalidate()
    } catch (e) { flash(`${intent} failed`, 'check', 6000) } finally { setBusy(false) }
  }

  const runApplyCommit = async () => runCommit('apply')

  // ── audit & edit: read a layer's entries, change them, write back ────────────
  const openAudit = async (profile, which) => {
    haptic('tap')
    setAudit({ profile, which }); setAuditBlocks([]); setAuditDesc(''); setAuditErr(null)
    try {
      if (which === 'profile') {
        const r = await rest.fn('/profile-entry/' + profile)
        setAuditDesc(r.description || '')
      } else {
        const r = await rest.fn(`/memory/${profile}?which=${which}`)
        setAuditBlocks((r.blocks || []).map(b => b.text))
      }
    } catch (e) { setAuditErr('could not read ' + which) }
  }
  const saveAudit = async () => {
    if (!audit) return
    const { profile, which } = audit
    setBusy(true)
    try {
      const url = which === 'profile' ? '/profile-entry' : '/memory'
      const body = which === 'profile'
        ? { profile, description: auditDesc }
        : { profile, which, blocks: auditBlocks }
      const r = await rest.fn(url, { method: 'POST', body })
      // The route already took a snapshot; keep the trial it registered so this edit is
      // undoable like every other write. It was the one act with no way back.
      if (r.trial) {
        setTrial(r.trial)
        flash(`saved ${which} → ${profile} — Undo last is in Commits`, 'check', 5000)
      } else {
        flash(r.ok ? `saved ${which} → ${profile} (no change)` : 'save failed', 'check', 3200)
      }
      await invalidate()
    } catch (e) { flash('save failed', 'check', 4000) } finally { setBusy(false) }
  }

  const stagedTargets = () => [...new Set(staged.map(s => s.profile))]
  const discardStaged = () => { setStaged([]); setPreview(null); setPendingPolicy(null); setPendingMembers(null); setPendingBlocks([]) }

  // ONE preflight, run automatically by BOTH Test and Apply.
  // It was a third button ("Preview") doing the same walk with no act behind it. A check
  // that lives beside the act it guards cannot be skipped, and cannot disagree with it.
  // Returns the plan; a `blocked` or any refusal means the caller must NOT proceed.
  const preflight = async () => {
    if (!staged.length) return { blocked: false, refusals: [], actions: [] }
    const plan = await rest.fn('/plan', { method: 'POST',
      // same pairing Apply sends — a preview that reports a different count than the
      // apply it guards is worse than no preview.
      body: { pairs: staged.map(s => ({ profile: s.profile, skill: s.skill, op: s.op || 'add' })),
              profiles: stagedTargets() } })
    setPreview(plan)
    return plan
  }

  const refusalSummary = (refusals) => (refusals || []).slice(0, 2)
    .map(b => `${b.skill || b.profile} (${b.reason})`).join(' · ')

  // CHECK — the same preflight on its own, for when you want to look before you leap.
  // It writes nothing and does not open a trial; it is a verb, not a third button.
  const runCheck = async () => {
    if (!staged.length) return
    setBusy(true); flash('checking…', 'spin', 30000)
    try {
      const plan = await preflight()
      flash(plan.blocked ? `blocked — ${plan.reason}`
                         : `${(plan.actions || []).length} ready · ${(plan.refusals || []).length} would fail`,
            'check', 6000)
    } catch (e) { flash('check failed', 'check', 4000) } finally { setBusy(false) }
  }

  // TEST — the real thing, with every write's reverse recorded. Press again (Revert) to
  // put the whole tree back. This is the only way to see a profile BEHAVE under a change.
  const runRevert = async () => {
    setBusy(true); flash('reverting…', 'spin', 60000)
    try {
      const r = await rest.fn('/trial/revert', { method: 'POST', body: { trial: trial || (trialData && trialData.trial) } })
      // Then reverse the block trials too, newest first. Each is single-use, and the order
      // matters: reversing oldest-first could restore a block the newer edit had replaced.
      let extra = 0
      const list = [...applyTrials].reverse().filter(t => t && t !== (trial || (trialData && trialData.trial)))
      for (const t of list) {
        try {
          const rr = await rest.fn('/trial/revert', { method: 'POST', body: { trial: t } })
          if (rr.ok) extra += (rr.count || 0)
        } catch (e) { /* a spent trial is not a failure — keep going */ }
      }
      setApplyTrials([])
      setTrial(null); setPendingPolicy(null)
      flash(r.ok ? `reverted ${(r.count || 0) + extra} file(s) — the tree is back` : 'revert hit failures — see the journal', 'check', 8000)
      await invalidate()
    } catch (e) { flash('revert failed', 'check', 6000) } finally { setBusy(false) }
  }

  // ── 1. toolbar (fixed) — feedback is ABSOLUTE so it never resizes the bar ──
  // ── the group tabs — they live INSIDE the SYNC POLICY header (see policyCard) ──
  // A group is a panel identity, so switching groups is a tab switch, not a separate
  // bar floating above the policy it belongs to.
  const groupTabs = jsxs('div', {
    style: { display: 'flex', alignItems: 'center', gap: '3px', flexWrap: 'wrap', minWidth: 0 },
    children: [
      ...groups.map(g => {
        const on = group && g.id === group.id
        return jsx('button', {
          key: g.id,
          title: on ? `${g.id} — ${(g.members || []).length} member(s). This panel is showing.` : `Switch to “${g.id}”`,
          onClick: e => {
            e.stopPropagation(); haptic('tap')
            setGid(g.id); setStaged([]); setPreview(null)
            setArmingDelete(false); setPendingPolicy(null); setPendingMembers(null); setPolicyOpen(true)
          },
          style: {
            fontSize: '9.5px', fontWeight: on ? 700 : 500, height: '18px', padding: '0 6px',
            borderRadius: '9px', cursor: 'pointer', userSelect: 'none', WebkitUserSelect: 'none',
            border: '1px solid ' + (on ? accent : stroke),
            background: on ? 'color-mix(in srgb, var(--ui-accent) 14%, transparent)' : 'transparent',
            color: on ? accent : tert, maxWidth: '110px', overflow: 'hidden',
            textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }, children: g.id,
        })
      }),
      newName === null
        ? jsx('button', {
            onClick: e => { e.stopPropagation(); startNewGroup() },
            title: 'Create a group — a named set of profiles that share a policy. Optional: you can stage straight onto a profile without one.',
            style: { background: 'transparent', border: '1px solid ' + stroke, borderRadius: '9px', color: accent, cursor: 'pointer', height: '18px', padding: '0 6px', display: 'flex', alignItems: 'center', gap: '2px', fontSize: '9.5px', flex: '0 0 auto' },
            children: [jsx(Icon, { name: 'plus', size: 9 }), 'group'],
          })
        : jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: '3px', minWidth: 0 }, children: [
            jsx('input', {
              value: newName, autoFocus: true, placeholder: 'group name',
              title: 'Name the new group — lowercase, hyphens are fine. Enter to create, Escape to cancel.',
              onInput: e => setNewName(e.target.value),
              onClick: e => e.stopPropagation(),
              onKeyDown: e => { if (e.key === 'Enter') void commitNewGroup(); if (e.key === 'Escape') setNewName(null) },
              style: { width: '92px', fontSize: '10px', height: '18px', padding: '0 4px', borderRadius: '4px', border: '1px solid ' + stroke, background: 'var(--ui-bg-chrome)', color: 'var(--ui-text-primary)' },
            }),
            jsx('button', { onClick: e => { e.stopPropagation(); void commitNewGroup() }, title: 'Create this group', style: { fontSize: '9.5px', cursor: 'pointer', color: 'var(--ui-text-primary)', background: 'transparent', border: 'none' }, children: 'ok' }),
            jsx('button', { onClick: e => { e.stopPropagation(); setNewName(null) }, title: 'Cancel', style: { fontSize: '9.5px', cursor: 'pointer', color: faint, background: 'transparent', border: 'none' }, children: '×' }),
          ]}),
      groups.length === 0 && newName === null
        ? jsx('span', { title: 'Groups are optional — with none, you can still stage skills straight onto a profile.', style: { fontSize: '9px', color: faint, fontStyle: 'italic' }, children: 'no groups' })
        : null,
    ],
  })

  // ── 3. policy card — ONE summary line collapsed; expands to the controls ──
  // Density rule for this card: the header always states the policy, so the controls
  // can stay folded. When open, every control gets exactly one line — or one CELL of
  // the two-column layer grid, so six controls cost three rows instead of six.
  const policySummary = [
    `${members.length} member${members.length === 1 ? '' : 's'}`,
    policy.anchor ? `anchor ${policy.anchor}` : 'no anchor',
    soulScope === 'off' ? 'soul off' : `soul ${soulScope}`,
    policy.auto === 'on' ? 'auto on' : null,
    layerPush().length ? layerPush().join('+') : null,
    policy.secrets === 'push' ? 'secrets ALL'
      : (policy.secrets === 'granular' ? `secrets ${(policy.secret_keys || []).length}` : null),
  ].filter(Boolean).join('  ·  ')

  // One cell of the layer grid: a fixed-width label so every Seg lines up vertically.
  // `span` makes a row take the whole grid width. A two-option toggle fits in a column;
// a four-option one does not — its labels get clipped, and a clipped label is worse
// than a taller panel.
// `layerCell` (a 2-column Seg grid cell) is REMOVED. It gave each option label ~40px and
// the labels died — `push`→`us`, `append`→`pe`, `Granular`→`Granula`, `Whole`→`Whol`. Its
// replacement is `layerRow` below, on the records-list standard: one full-width line per
// layer stating its whole state in words, with the focused layer's detail given the width
// instead of a squeezed cell.

  // ── PER-ROW DETAILS ─────────────────────────────────────────────────────────────
  // Each renders INSIDE the grid, in the row directly beneath the row it belongs to.
  // Moved to the bottom of the panel it reads as a separate setting; here it reads as
  // what it is — the detail of one row. `gridColumn: '1 / -1'` spans both columns.

  // ── THE PICK TOGGLE — its LABEL IS THE STATE ────────────────────────────────────
  // "pick 5/21" is not a button you press to find out where you are; reading it tells you
  // the layer is narrowed, without opening anything. A separate status light beside a
  // control is the smell this avoids — the control and the check cannot disagree.
  const pickToggle = (layer) => {
    const on = scopeOf(layer) === 'pick'
    const n = picksOf(layer).length
    const total = itemData?.layer === layer ? itemData.count : null
    return jsx(InlineAction, {
      label: on ? `pick ${n}${total != null ? `/${total}` : ''}` : 'pick',
      onClick: () => {
        haptic('tap')
        if (!on) {
          // Enter pick mode SEEDED with everything. Arriving empty flashed a full list of
          // UNTICKED boxes — the display said "nothing syncs" the moment you came from
          // "everything syncs". The list now opens showing what is actually true, and you
          // narrow it by unticking.
          const snap = itemData?.layer === layer ? pickItems : null
          setPicks(layer, snap && snap.length ? snap.map(i => i.key) : picksOf(layer))
        }
        setPickOpen(o => (o === layer ? null : layer))
      },
      hint: on
        ? `${layer} is narrowed to ${n} item(s). Click to ${pickOpen === layer ? 'collapse' : 'edit'} the list.`
        : `Choose WHICH ${layer} items sync, instead of all of them.`,
    })
  }

  const pickItemsHead = (layer, unit) => jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: '5px', flexWrap: 'wrap' }, children: [
    jsx('span', { title: `Each row is one ${unit} of ${inspectProfile}, read from disk just now.`, style: { ...labelStyle, color: accent, flex: '0 0 auto' }, children: `Pick ${unit}s from ${inspectProfile || '—'}` }),
    jsx('span', { style: { flex: '1 1 0' } }),
    // WHOSE facts these are. Defaults to the anchor (what a group copies from) but is NOT
    // gated on it: reading your own durable facts must not require a group to exist.
    // Clicking cycles through the profiles so one verb covers every case — the alternative
    // is a dropdown, which is more chrome for the same single choice.
    inspectProfile
      ? jsx(InlineAction, {
          label: 'next profile',
          onClick: () => {
            haptic('tap')
            const names = profiles.map(p => p.name)
            const at = names.indexOf(inspectProfile)
            setInspect(names[(at + 1) % Math.max(names.length, 1)] || null)
          },
          hint: `Switch which profile's ${unit}s this list shows. Inspection only — it never changes what syncs, which is always the anchor, ${policy.anchor || 'unset'}.`,
        })
      : null,
    jsx(InlineAction, { label: 'all', onClick: () => setPicks(layer, pickItems.map(i => i.key)), hint: `Select every ${unit}. Same effect as scope All, but frozen to exactly this list.` }),
    jsx(InlineAction, { label: 'none', onClick: () => setPicks(layer, []), hint: 'Select nothing. The layer runs and writes an empty set.' }),
    // NO `close` here. The layer header in `layerDetail` already owns the close, and this
    // was the second one — the reason memory/user read as a different layout from soul and
    // secrets. One header, one close, every layer.
  ]})

  const pickDetail = (layer) => {
    if (pickOpen !== layer) return null
    const chosen = picksOf(layer)
    const on = scopeOf(layer) === 'pick'
    const unit = itemData?.unit || 'item'
    const editable = layer === 'memory' || layer === 'user'
    const body = !inspectProfile
      ? jsx(Empty, { hint: 'Pick a profile above to read its items.', children: 'No profile selected.' })
      : (itemBusy && !pickItems.length)
        ? jsx('div', { style: { fontSize: '9px', color: faint, fontStyle: 'italic' }, children: `reading ${unit}s…` })
        : (itemData && itemData.ok === false)
          ? jsx('div', { title: itemData.error, style: { fontSize: '9.5px', color: red }, children: itemData.error })
          : pickItems.length === 0
            ? jsx(Empty, { hint: `${inspectProfile} has no ${unit}s in this file.`, children: `No ${unit}s.` })
            : jsx('div', { style: { flex: '1 1 auto', minHeight: 0, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '2px' }, children:
                pickItems.map((it) => jsxs('div', {
                  key: it.key,
                  style: { display: 'flex', alignItems: 'flex-start', gap: '5px', padding: '1px 0' },
                  children: [
                    jsx('input', {
                      type: 'checkbox', checked: !on || chosen.includes(it.key),
                      // LIVE, always. It used to be `disabled: !on`, which froze every box
                      // whenever the scope was off/push/append — the exact state you are in
                      // when you want to start choosing. Ticking one now ENTERS pick mode,
                      // so the control that narrows the set is also the control that opens it.
                      title: on
                        ? 'Tick to sync this item; untick to leave it behind'
                        : `Everything syncs while the scope is not “pick”. Unticking one here keeps the rest and starts a pick list without it.`,
                      // Under any non-pick scope EVERY box reads checked, because everything
                      // syncs. Clicking one must therefore start from that state and change
                      // exactly one — not enter pick mode empty, which is what made a single
                      // click look like it deselected the whole list.
                      onChange: () => {
                        if (on) { togglePick(layer, it.key); return }
                        const all = pickItems.map(i => i.key)
                        setPicks(layer, all.includes(it.key) ? all.filter(k => k !== it.key) : [...all, it.key])
                      },
                      style: { margin: '1px 0 0 0', accentColor: 'var(--ui-accent)', flex: '0 0 auto', cursor: 'pointer' },
                    }),
                    editBlock && editBlock.layer === layer && editBlock.key === it.key
                      ? jsxs('div', { style: { flex: '1 1 0', minWidth: 0, display: 'flex', flexDirection: 'column', gap: '3px' }, children: [
                          editBlock.loading
                            ? jsx('div', { style: { fontSize: '9px', color: faint, fontStyle: 'italic' }, children: 'loading the full text…' })
                            : editBlock.text == null
                              ? jsx('div', { title: editBlock.err, style: { fontSize: '9px', color: red }, children: editBlock.err || 'could not load' })
                              : jsx('textarea', {
                                  value: editBlock.text, rows: 4, autoFocus: true,
                                  title: `Editing this ${unit} on ${inspectProfile}. The FULL text is loaded, not the preview. Saving snapshots the file first, so Undo last reverses it.`,
                                  onInput: e => setEditBlock(b => ({ ...b, text: e.target.value })),
                                  style: { width: '100%', boxSizing: 'border-box', fontSize: '9.5px', fontFamily: 'inherit', padding: '3px 5px', borderRadius: '4px', border: '1px solid ' + stroke, background: 'var(--ui-bg-chrome)', color: 'var(--ui-text-primary)', resize: 'vertical' },
                                }),
                          jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: '5px', flexWrap: 'wrap' }, children: [
                            jsx(InlineAction, { label: editBlock.saving ? 'staging…' : 'stage', onClick: () => void saveBlock(editBlock),
                              hint: 'Stage this edit. It joins the STAGED bar and Apply writes it — same as every other change in this pane. An emptied box is refused; use delete instead. Reversible from Activity ▸ Commits.' }),
                            jsx(InlineAction, { label: 'delete', onClick: () => void saveBlock({ ...editBlock, text: null, arm: false }),
                              hint: `Remove this ${unit} from ${inspectProfile}. The server supports it and this is the button it was telling you to use. Reversible from Activity ▸ Commits.`, tone: 'red' }),
                            jsx(InlineAction, { label: 'cancel', onClick: () => setEditBlock(null), hint: 'Discard this edit' }),
                            editBlock.err && editBlock.text != null ? jsx('span', { style: { fontSize: '9px', color: red, flex: '1 1 0' }, children: editBlock.err }) : null,
                          ]}),
                        ]})
                      : jsx('span', {
                          title: editable
                            ? `${it.chars} chars — click to edit this ${unit} on ${inspectProfile}`
                            : `${it.chars} chars — ${it.preview}`,
                          onClick: editable ? () => { haptic('tap'); void loadBlock(layer, it) } : undefined,
                          style: { flex: '1 1 0', minWidth: 0, fontSize: '9.5px', color: tert, cursor: editable ? 'pointer' : 'default', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
                          children: it.label || it.preview,
                        }),
                    jsx('span', { style: { flex: '0 0 auto', fontSize: '8.5px', color: faint, fontVariantNumeric: 'tabular-nums' }, children: it.chars ? String(it.chars) : '' }),
                  ],
                })) })
    return jsxs('div', {
      style: { gridColumn: '1 / -1', border: '1px solid ' + stroke, borderRadius: '5px', padding: '5px', display: 'flex', flexDirection: 'column', gap: '4px' },
      children: [pickItemsHead(layer, unit), body],
    })
  }

  // ── editing a block ─────────────────────────────────────────────────────────────
  // The list shows a PREVIEW (capped at 160 chars so a poll does not stream the whole
  // file), so the editor must NOT seed from it — saving a preview would truncate the block
  // it meant to edit. Clicking fetches the FULL text first; the editor opens only once it
  // has the real thing.
  const loadBlock = async (layer, item) => {
    setEditBlock({ layer, key: item.key, text: null, err: null, loading: true })
    try {
      const which = layer === 'user' ? 'user' : 'memory'
      const r = await rest.fn(`/memory/${inspectProfile}?which=${which}`)
      const hit = (r.blocks || []).find(b => b.key === item.key)
      if (!hit) {
        setEditBlock({ layer, key: item.key, text: null, loading: false,
                       err: 'that block changed since the list was read — close and reopen' })
        return
      }
      setEditBlock({ layer, key: item.key, text: hit.text, err: null, loading: false })
    } catch (e) {
      setEditBlock({ layer, key: item.key, text: null, loading: false, err: String(e && e.message || e) })
    }
  }

  const saveBlock = async (eb) => {
    // STAGES, does not write. The commit happens on Apply with everything else, so the
    // STAGED bar is always the honest answer to "what is about to change".
    if (!eb || !eb.key) return
    const deleting = eb.text == null
    if (!deleting && !eb.text.trim()) {
      setEditBlock(b => ({ ...b, err: 'a block cannot be emptied — use delete' })); return
    }
    const label = eb.text == null
      ? `delete a ${eb.layer} block`
      : `edit a ${eb.layer} block`
    setPendingBlocks(prev => [
      ...prev.filter(p => !(p.layer === eb.layer && p.key === eb.key)),
      { layer: eb.layer, key: eb.key, text: eb.text, label, profile: inspectProfile },
    ])
    setEditBlock(null)
    setStagedOpen(true)
    flash(`staged: ${label} on ${inspectProfile} — Apply writes it`, 'check', 5000)
  }

  // Writes every staged block edit. Called from Apply, after the group commit — so a
  // block edit and a policy change land in one action, which is what staging promised.
  const commitBlocks = async () => {
    const q = pendingBlocks
    if (!q.length) return { ok: true, trials: [] }
    const trials = []
    for (const p of q) {
      const r = await rest.fn('/block', { method: 'POST', body: {
        profile: p.profile, which: p.layer === 'user' ? 'user' : 'memory',
        key: p.key, text: p.text,
      } })
      if (!r.ok) { flash(`block write refused: ${r.error || 'error'}`, 'alert', 8000); return { ok: false, trials } }
      // EVERY block write records its OWN trial. Collecting the ids is what lets one Undo
      // reverse the whole Apply; without it the block edits were invisible to the revert
      // and survived it, which is the opposite of what "Undo last" promises.
      if (r.trial) trials.push(r.trial)
    }
    setPendingBlocks([])
    return { ok: true, trials }
  }

  const secretsDetail = policy.secrets !== 'granular' ? null : () => jsxs('div', {
    style: { gridColumn: '1 / -1', display: 'flex', flexDirection: 'column', gap: '3px', border: '1px solid ' + accent, borderRadius: '5px', padding: '5px' },
    children: [
      jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: '5px', minWidth: 0 }, children: [
        jsx('span', { title: 'Pick WHICH .env keys move. Values are copied; they are never displayed anywhere in this pane.', style: { ...labelStyle, color: accent, flex: '0 0 auto' }, children: policy.anchor ? `Keys from ${policy.anchor} (${(policy.secret_keys || []).length} of ${anchorKeys.length})` : 'Secrets — no anchor' }),
        jsx('span', { style: { flex: '1 1 0' } }),
        anchorKeys.length ? jsx(InlineAction, { label: 'all', onClick: () => void saveGroup({ policy: { ...policy, secret_keys: anchorKeys.map(k => k.key) } }), hint: 'Tick every key the anchor has. This gives each member the anchor\u2019s full key set — including keys that have nothing to do with that member.' }) : null,
        anchorKeys.length ? jsx(InlineAction, { label: 'none', onClick: () => void saveGroup({ policy: { ...policy, secret_keys: [] } }), hint: 'Untick every key. Granular then moves nothing.' }) : null,
      ]}),
      !policy.anchor
        ? jsx('span', { title: 'These keys are copied FROM a member, so the group needs an anchor first. Click a member\u2019s name in the Members row above.', style: { fontSize: '9.5px', color: warn }, children: 'pick an anchor above — the keys are copied from its .env' })
        : secretsUnavailable
          ? jsx('span', { title: 'The running gateway predates this route. Hermes only picks up new plugin routes on restart: quit and reopen the app, then reopen this pane.', style: { fontSize: '9.5px', color: warn }, children: '⚠ the /secrets route is not live — restart Hermes, then reopen this pane' })
          : anchorKeys.length === 0
            ? jsx('span', { title: `\u201c${policy.anchor}\u201d has no .env file, or it holds no assignments.`, style: { fontSize: '9.5px', color: faint, fontStyle: 'italic' }, children: 'the anchor has no .env keys' })
            : jsx('div', { style: { flex: '1 1 auto', minHeight: 0, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '1px' }, children: anchorKeys.map(k => {
                const on = (policy.secret_keys || []).includes(k.key)
                return jsx('label', {
                  key: k.key,
                  title: `${k.key} — ${k.has_value ? 'has a value' : 'declared but EMPTY'}. ${on ? 'Will be copied to each member on Apply.' : 'Tick to include it.'} The value itself is never shown, here or anywhere.`,
                  style: { display: 'flex', alignItems: 'center', gap: '5px', fontSize: '9.5px', cursor: 'pointer', color: on ? accent : tert, fontWeight: on ? 600 : 400 },
                  children: [
                    jsx('input', { type: 'checkbox', checked: on,
                      onChange: () => { const cur = policy.secret_keys || []; void saveGroup({ policy: { ...policy, secret_keys: on ? cur.filter(x => x !== k.key) : [...cur, k.key] } }) },
                      style: { margin: 0, accentColor: 'var(--ui-accent)' } }),
                    jsx('span', { style: { fontFamily: 'ui-monospace, SFMono-Regular, monospace', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: k.key }),
                    k.has_value ? null : jsx('span', { title: 'This key is declared but its value is empty — copying it moves nothing useful.', style: { color: warn, fontSize: '8.5px', flex: '0 0 auto' }, children: 'empty' }),
                  ],
                })
              }) }),
    ],
  })

  const soulDetail = soulScope === 'off' ? null : () => jsxs('div', {
    style: { gridColumn: '1 / -1', display: 'flex', flexDirection: 'column', gap: '3px' },
    children: [
      soulScope === 'section' ? jsxs('div', {
        style: { display: 'flex', flexDirection: 'column', gap: '3px', border: '1px solid ' + stroke, borderRadius: '5px', padding: '5px' },
        children: [
          jsx('div', { title: 'Sections are read from the anchor\u2019s SOUL.md and copied into every member, spliced under ownership markers so each member\u2019s own text stays put.', style: { ...labelStyle }, children: `Sections from ${policy.anchor || 'no anchor'}` }),
          !policy.anchor
            ? jsx('span', { title: 'Pick an anchor above — the sections come from its SOUL.md.', style: { fontSize: '9.5px', color: faint, fontStyle: 'italic' }, children: 'pick an anchor first' })
            : anchorSections.length === 0
              ? jsx('span', { title: `\u201c${policy.anchor}\u201d has no ## headings in its SOUL.md, so there is nothing to tick. Use Blocks or Whole instead.`, style: { fontSize: '9.5px', color: faint, fontStyle: 'italic' }, children: 'the anchor has no ## headings — use Blocks or Whole' })
              : jsx('div', { style: { flex: '1 1 auto', minHeight: 0, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '2px' }, children: anchorSections.map(s => {
                  const on = (policy.soul_sections || []).includes(s.title)
                  return jsx('label', {
                    key: s.title,
                    title: `${s.title} — ${s.lines} line(s) in the anchor. ${on ? 'Ticked: this section is copied. Click to remove it.' : 'Unticked: click to include this section in the sync.'}`,
                    style: { display: 'flex', alignItems: 'center', gap: '5px', fontSize: '10px', cursor: 'pointer', color: on ? accent : tert, fontWeight: on ? 600 : 400 },
                    children: [
                      jsx('input', { type: 'checkbox', checked: on,
                        onChange: () => { const cur = policy.soul_sections || []; void saveGroup({ policy: { ...policy, soul_sections: on ? cur.filter(t => t !== s.title) : [...cur, s.title] } }) },
                        style: { margin: 0, accentColor: 'var(--ui-accent)' } }),
                      s.title,
                      jsx('span', { style: { color: faint, fontSize: '9px' }, children: `${s.lines}L` }),
                    ],
                  })
                }) }),
        ],
      }) : null,
      jsx('div', {
        title: soulScope === 'whole'
          ? 'Whole-file: every member\u2019s SOUL.md becomes byte-identical. This OVERWRITES per-bot role identity (e.g. the trader\u2019s short role SOUL). A snapshot is taken first, so it is reversible.'
          : (soulScope === 'block' ? 'Block mode: the anchor\u2019s rules are spliced inside ownership markers. Everything outside the markers stays byte-identical.'
            : 'Section mode: only the sections you tick are written; every other section is untouched.'),
        style: { fontSize: '9.5px', lineHeight: 1.35, color: soulScope === 'whole' ? red : faint },
        children: (soulScope === 'whole' ? '\u26a0 ' : '') + scopeHint,
      }),
    ],
  })

  // ══════════════════════════════════════════════════════════════════════════════
  //  SYNC POLICY — the layer list
  //
  //  Rebuilt to the RECORDS-LIST standard the meeting pane uses, because that is the
  //  app's established row grammar and Sync Policy was the one surface not speaking it:
  //
  //     [icon]  [name — flexes, ellipsis]  [state — muted text]  [text verbs]
  //
  //  What this replaces: a 2-column Seg grid. Each cell gave the label 46px and the Seg
  //  whatever was left, so in a narrow panel the option labels DIED — `push`→`us`,
  //  `append`→`pe`, `Granular`→`Granula`, `Whole`→`Whol`. A control whose label is
  //  unreadable is not a control.
  //
  //  Three rules this follows:
  //    * STATE IS TEXT, NOT COLOUR. `append · 5 of 21` reads; eight green "Off" pills do
  //      not — that was the wall of green, and it carried no information. The only accent
  //      is the layer you have FOCUSED.
  //    * ESSENTIAL, NOT MINIMAL. A row shows its whole state so you can read the policy
  //      without opening anything; opening one gives it the full width and real room.
  //    * FOCUS HAS AN ESCAPE. Opening a layer replaces the detail region; the same row
  //      closes it, and the header always shows the way out.
  // ══════════════════════════════════════════════════════════════════════════════

  const LAYER_ORDER = ['soul', 'memory', 'user', 'profile', 'models', 'secrets']

  // One line of state per layer, in words, for the row. Reads as a sentence, not a badge.
  const layerState = (id) => {
    if (id === 'soul') {
      const s = policy.soul || 'off'
      if (s === 'off') return 'off'
      if (s === 'section') return `sections · ${(policy.soul_sections || []).length}`
      return s                                            // whole | blocks
    }
    if (id === 'secrets') {
      const s = policy.secrets || 'off'
      if (s === 'off') return 'off'
      if (s === 'granular') return `keys · ${(policy.secret_keys || []).length}`
      return 'the keyring'
    }
    const mode = policy[id] || 'off'
    if (mode === 'off') return 'off'
    const scope = scopeOf(id)
    if (scope === 'pick') return `${mode} · ${picksOf(id).length} picked`
    if (scopeOf(id) === 'all' && itemData?.layer === id) return `${mode} · all ${itemData.count}`
    return mode
  }

  // The layer's own control glyph, so the list reads by icon the way the meeting pane's
  // rows read by their verbs. Every one is titled — the standing tooltip rule.
  const LAYER_ICON = {
    soul: 'target', memory: 'folder', user: 'target',
    profile: 'reveal', models: 'flip', secrets: 'search',
  }

  const layerRow = (id) => {
    const meta = LAYER_META[id] || { label: id }
    const on = (policy[id] || 'off') !== 'off'
    const focused = pickOpen === id
    return jsxs('div', {
      key: id,
      title: meta.hint,
      style: {
        display: 'flex', alignItems: 'center', gap: '6px',
        fontSize: '11px', padding: '4px 5px', borderRadius: '5px', cursor: 'pointer',
        // NO GREEN HERE. Before, the whole list was tinted and the accent meant nothing.
        // The open row is marked by a raised background and a rule; colour is not spent.
        background: focused ? 'color-mix(in srgb, var(--ui-text-primary) 7%, transparent)' : 'transparent',
        border: '1px solid ' + (focused ? stroke : 'transparent'),
      },
      onClick: () => { haptic('tap'); setPickOpen(o => (o === id ? null : id)); setEditBlock(null) },
      children: [
        jsx('span', {
          title: on ? `${meta.label} is ON — every Apply moves it.` : `${meta.label} is off — nothing moves.`,
          style: { display: 'flex', flex: '0 0 auto', color: focused ? 'var(--ui-text-primary)' : (on ? sec : faint) },
          children: jsx(Icon, { name: LAYER_ICON[id] || 'folder', size: 12 }),
        }),
        jsx('span', {
          style: { flex: '0 1 auto', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                   fontWeight: on ? 600 : 400, color: on ? sec : faint },
          children: meta.label,
        }),
        jsx('span', {
          title: `${meta.label}: ${layerState(id)}`,
          style: { flex: '1 1 0', minWidth: 0, textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis',
                   whiteSpace: 'nowrap', fontSize: '10px', color: faint, fontVariantNumeric: 'tabular-nums' },
          children: layerState(id),
        }),
        jsx('span', {
          style: { flex: '0 0 auto', color: focused ? 'var(--ui-text-primary)' : faint, display: 'flex' },
          children: jsx(Icon, { name: focused ? 'down' : 'chevron', size: 11 }),
        }),
      ],
    })
  }

  //  The FOCUSED layer's detail — rendered UNDER ITS OWN ROW, not in a separate region.
  //  An accordion: the row you opened grows downward, and the rows below slide down.
  //  A plain rule and an indent tie it to the row above — the attachment reads without colour.
  const layerDetail = (id) => jsxs('div', {
    style: {
      margin: '1px 0 3px 14px', paddingLeft: '8px',
      borderLeft: '2px solid ' + stroke,
      borderRadius: '0 5px 5px 0',
      display: 'flex', flexDirection: 'column', minHeight: 0, flex: '1 1 auto',
    },
    children: [
jsxs('div', { style: {
                marginTop: '4px', borderTop: '1px solid ' + stroke, paddingTop: '5px',
                // This wrapper MUST be a bounded flex column. It was a plain block div, so the
                // scrollable list inside it had nothing to scroll WITHIN — the list grew past
                // the wrapper and painted straight over the layer rows below (User, Profile,
                // Models, Secrets), which is why their icons looked squashed under it.
                flex: '1 1 auto', minHeight: 0, display: 'flex', flexDirection: 'column',
              }, children: [
              jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap', marginBottom: '4px' }, children: [
                jsx('span', { title: LAYER_META[id]?.hint, style: { ...labelStyle, color: 'var(--ui-text-primary)', flex: '0 0 auto' }, children: LAYER_META[id]?.label || id }),
                jsx('div', { style: { flex: '0 1 auto', minWidth: '96px' }, children: jsx(Seg, {
                  options: id === 'soul' ? SOUL_SCOPES : (id === 'secrets' ? SECRET_MODES : (id === 'profile' || id === 'models' ? PUSH_MODES : PUSH_APPEND_MODES)),
                  clearable: true, value: policy[id] || 'off',
                  onPick: v => void saveGroup({ policy: { ...policy, [id]: v || 'off' } }),
                }) }),
                id !== 'soul' && id !== 'secrets' ? pickToggle(id) : null,
                jsx('span', { style: { flex: '1 1 0' } }),
                jsx(InlineAction, { label: 'close', onClick: () => { setPickOpen(null); setEditBlock(null) }, hint: 'Collapse this layer and return to the list' }),
              ]}),
              id === 'soul' ? (soulDetail ? soulDetail() : null)
                : id === 'secrets' ? (secretsDetail ? secretsDetail() : null)
                : pickDetail(id),
      ]}),
    ],
  })

  const policyCard = group ? jsxs('div', {
    // RESPONSIVE, both ways. Idle it HUGS its six rows and the boxes take the height; with
    // a layer open it claims the pane and the boxes fold. The two states TRADE the space
    // instead of one hoarding it — that is what killed the dead area under the list.
    style: { flex: boxesCollapsed ? '1 1 auto' : '0 0 auto', minHeight: 0, borderBottom: '1px solid ' + stroke, display: 'flex', flexDirection: 'column' },
    children: [
      // ── the header: always visible, states the policy, toggles the controls ──
      // The header IS the group switcher: each group is a tab, `+ group` is a tab that
      // is not there yet, and the whole panel below belongs to whichever tab is active.
      jsxs('div', {
        style: { display: 'flex', alignItems: 'center', gap: '5px', padding: '5px 9px', flexWrap: 'wrap' },
        children: [
          jsx('span', {
            title: policyOpen
              ? 'Collapse the policy controls.'
              : 'Expand the policy controls — members, the anchor every layer copies from, and which layers this group moves.',
            onClick: () => { haptic('tap'); setPolicyOpen(o => !o) },
            style: { display: 'flex', alignItems: 'center', gap: '4px', cursor: 'pointer', userSelect: 'none', WebkitUserSelect: 'none', flex: '0 0 auto' },
            children: [
              jsx('span', { style: chromeIcon, children: jsx(Icon, { name: policyOpen ? 'down' : 'chevron', size: 12 }) }),
              jsx('span', { style: { ...labelStyle, color: sec }, children: 'Sync Policy' }),
            ],
          }),
          jsx('span', { style: { flex: '1 1 0' } }),
          policyDirty
            ? jsx('span', { title: `Staged policy edit(s): ${Object.keys(pendingPolicy).join(' · ')}. Apply writes them; discard on the STAGED bar throws them away.`, style: { fontSize: '9px', color: 'var(--ui-text-primary)', fontWeight: 700, flex: '0 0 auto' }, children: '• staged' })
            : null,
          renaming === (group && group.id)
            ? jsxs('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '3px', flex: '0 0 auto' }, children: [
                jsx('input', {
                  value: renameDraft, autoFocus: true,
                  title: 'The new group name. Enter to rename, Escape to cancel. Members and policy move with it; no profile is touched.',
                  onClick: e => e.stopPropagation(),
                  onInput: e => setRenameDraft(e.target.value),
                  onKeyDown: e => { if (e.key === 'Enter') void doGroupRename(); if (e.key === 'Escape') setRenaming(null) },
                  style: { width: '104px', fontSize: '9.5px', height: '18px', padding: '0 4px', borderRadius: '4px', border: '1px solid ' + stroke, background: 'var(--ui-bg-chrome)', color: 'var(--ui-text-primary)' },
                }),
                jsx('button', { onClick: () => void doGroupRename(), title: 'Rename this group', style: { fontSize: '9.5px', cursor: 'pointer', color: 'var(--ui-text-primary)', background: 'transparent', border: 'none' }, children: 'ok' }),
                jsx('button', { onClick: () => setRenaming(null), title: 'Cancel', style: { fontSize: '9.5px', cursor: 'pointer', color: faint, background: 'transparent', border: 'none' }, children: '×' }),
              ]})
            : (group
                ? jsx(InlineAction, { label: 'rename', onClick: () => { setRenaming(group.id); setRenameDraft(group.id) }, hint: 'Rename this group. Members and policy move with it; no profile is touched.', stop: true })
                : null),
          armingDelete
            ? jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: '4px' }, children: [
                jsx('span', { title: 'This deletes the GROUP record only — no profile and no skill is touched.', style: { fontSize: '9px', color: faint }, children: 'delete group?' }),
                jsx(InlineAction, { label: 'yes', onClick: delGroup, hint: 'Delete this group record. Profiles and skills are untouched.', tone: 'red', stop: true }),
                jsx(InlineAction, { label: 'no', onClick: () => setArmingDelete(false), hint: 'Keep the group', stop: true }),
              ] })
            : jsx(InlineAction, { label: 'delete', onClick: () => setArmingDelete(true), hint: 'Remove this group. Two clicks — it only deletes the group record, never a profile.', stop: true }),
        ],
      }),
      policyOpen ? jsxs('div', {
        style: {
          padding: '0 9px 8px', display: 'flex', flexDirection: 'column', gap: '5px',
          // `minHeight: 0` + `overflow: hidden` are what BOUND this column. Without them a
          // flex child defaults to minHeight:auto and grows past the parent, so a long block
          // list painted straight over BACKUPS and PROFILES instead of scrolling inside.
          flex: '1 1 auto', minHeight: 0, overflow: 'hidden',
        },
        children: [
          // The group tabs live IN the panel, not on the header: this panel belongs to
          // whichever group is active, so switching groups belongs here with it.
          jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: '4px', flexWrap: 'wrap' }, children: [
            jsx('span', { title: 'Each tab is a group — a named set of profiles with its own policy. The panel below belongs to the active one.', style: { ...labelStyle, color: sec, flex: '0 0 auto' }, children: 'Group' }),
            groupTabs,
          ]}),
          jsx('div', {
            title: `${policySummary}${policy.auto === 'on' ? ' — a scheduled pass keeps this group matched to its anchor' : ''}`,
            style: { fontSize: '9.5px', color: faint, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
            children: policySummary,
          }),
          // Two groups can hold the same profile — nothing in Hermes decides which wins,
          // so a disagreement is surfaced rather than left to whichever ran last.
          ...conflicts.filter(cf => members.includes(cf.profile)).map(cf => jsxs('div', {
            key: cf.profile,
            title: `“${cf.profile}” is also in ${cf.groups.filter(g => g !== group.id).join(', ')}, with different settings:\n`
                   + Object.entries(cf.clashes).map(([k, v]) => `  ${k}: ` + Object.entries(v).map(([g, val]) => `${g}=${val}`).join(' vs ')).join('\n')
                   + '\nHermes has no notion of which group owns a profile: the LAST apply wins, and with Auto on both, they race every 15 minutes. Take the profile out of one of them.',
            style: { display: 'flex', alignItems: 'flex-start', gap: '4px', fontSize: '9.5px', color: red, lineHeight: 1.35 },
            children: [jsx(Icon, { name: 'alert', size: 11 }), jsx('span', { children: `${cf.profile} is in ${cf.groups.length} groups that disagree` })],
          })),
          jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: '4px', flexWrap: 'wrap' }, children: [
            // ONE element per member: the NAME sets/clears the anchor, the × removes.
            // Merging these two rows is the point — "who is in this group" and "who is
            // the source" are the same question asked twice.
            jsx('span', { title: 'Group membership and the anchor in one place. Click a NAME to make that profile the anchor (click the anchor again to clear it); click × to drop it from the group. Every enabled layer copies FROM the anchor INTO the others.', style: { ...labelStyle, color: sec, flex: '0 0 40px' }, children: 'Members' }),
            ...members.map(m => {
              const isA = policy.anchor === m
              const isNew = !savedMembers.includes(m)     // staged, not yet committed
              return jsxs('span', {
                key: m,
                title: isNew ? `${m} is a STAGED member — it joins the group when you press Apply, not on this click.` : undefined,
                style: {
                  display: 'inline-flex', alignItems: 'stretch', borderRadius: '4px', flex: '0 0 auto',
                  // a staged member is dashed so it reads as pending at a glance
                  border: '1px ' + (isNew ? 'dashed' : 'solid') + ' ' + stroke,
                  background: isA ? 'color-mix(in srgb, var(--ui-text-primary) 10%, transparent)'
                    : 'transparent',
                  overflow: 'hidden',
                },
                children: [
                  jsx('span', {
                    title: isA
                      ? `${m} is the ANCHOR — every enabled layer copies FROM it. Click to clear.`
                      : `${m} is a member. Click to make it the anchor: its SOUL and any enabled layer become the source every other member is brought to match.`,
                    onClick: e => { e.stopPropagation(); haptic('tap'); void saveGroup({ policy: { ...policy, anchor: isA ? null : m } }) },
                    style: { padding: '1px 5px', cursor: 'pointer', fontSize: '9.5px', color: isA ? 'var(--ui-text-primary)' : tert, fontWeight: isA ? 700 : 500, userSelect: 'none', WebkitUserSelect: 'none' },
                    children: isA ? ['★ ', m] : m,
                  }),
                  jsx('span', {
                    title: `Remove ${m} from this group. The profile and its skills are untouched — only membership changes.`,
                    onClick: e => { e.stopPropagation(); haptic('tap'); toggleMember(m) },
                    style: { padding: '1px 4px', cursor: 'pointer', fontSize: '9.5px', color: faint, borderLeft: '1px solid ' + stroke, userSelect: 'none', WebkitUserSelect: 'none' },
                    children: '×',
                  }),
                ],
              })
            }),
            members.length === 0
              ? jsx('span', { title: 'No members yet. Add one here, or with the “+ add” chip on a profile row below.', style: { fontSize: '9.5px', color: faint, fontStyle: 'italic' }, children: 'none yet —' })
              : null,
            addingMembers
              ? jsxs('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '3px', flexWrap: 'wrap' }, children: [
                  ...profilesView.filter(p => !members.includes(p.name)).map(p => jsx('span', {
                    key: p.name,
                    title: `Add ${p.name} to this group`,
                    onClick: e => { e.stopPropagation(); haptic('tap'); toggleMember(p.name) },
                    style: { fontSize: '9px', padding: '1px 5px', borderRadius: '4px', border: '1px dashed ' + stroke, color: tert, cursor: 'pointer' },
                    children: p.name,
                  })),
                  jsx(InlineAction, { label: 'done', onClick: e => { e.stopPropagation(); setAddingMembers(false) }, hint: 'Stop adding members', stop: true }),
                ]})
              : jsx(InlineAction, { label: '+ add', onClick: e => { e.stopPropagation(); setAddingMembers(true) }, hint: 'Add a profile to this group', stop: true }),
            policy.anchor && anchorSoul
              // Labelled and separated. It used to render a bare `SOUL 1711L` right after
              // the `+ add` button, which read as if it belonged to the button.
              ? jsx('span', {
                  title: `The anchor’s SOUL.md — ${anchorSoul.lines} line(s), ${anchorSections.length} heading(s). This is the file every member is matched to.`,
                  style: { fontSize: '9px', color: faint, flex: '0 0 auto', paddingLeft: '6px', marginLeft: '2px', borderLeft: '1px solid ' + stroke },
                  children: `anchor ${policy.anchor} · SOUL ${anchorSoul.lines}L`,
                })
              : null,
          ]}),
          // ── 1. WHICH LAYERS MOVE: the on/off toggles, two columns.
          //    Detail for any one layer belongs UNDER this, never between the
          //    toggles — you decide what moves before you tune how. ──
        jsxs('div', { style: { flex: '1 1 auto', minHeight: 0, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '1px' }, children: [
            // The header states the whole policy's shape in one line, the way the meeting
            // pane's summary line does — the list below then only has to say what is ON.
            jsx('div', { title: 'The six things a group can move. Open one to change how it moves and which items it takes.', style: { ...labelStyle, marginBottom: '2px' }, children: 'What moves' }),
            // Accordion: a focused row grows its detail immediately below itself,
            // so the panel opens where you clicked instead of in a far-off region.
            ...LAYER_ORDER.flatMap(id => pickOpen === id ? [layerRow(id), layerDetail(id)] : [layerRow(id)]),
          ]}),

          // ── 2. DETAIL FOR THE ROWS THAT HAVE ANY — the Secrets key picker and the
          //    Soul section checklist. Each is a detail OF a row in the grid above, so
          //    neither reads as a separate setting. ──
          // Secrets: WHICH keys move. Names only — the pane is never given a value, so
          // it cannot show one, log one, or put one in a screenshot.
          
          policy.auto === 'on' ? jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: '7px', flexWrap: 'wrap' }, children: [
            jsx(InlineAction, { label: 'dry run', onClick: () => void runSync(true), hint: 'Report what the next scheduled pass would change. Writes nothing.' }),
            jsx(InlineAction, { label: 'sync now', onClick: () => void runSync(false), hint: 'Run the pass immediately — exactly what the schedule runs. This writes.' }),
            lastSync ? jsx('span', { title: 'When this session last ran the pass by hand.', style: { fontSize: '9px', color: faint }, children: `ran ${lastSync}` }) : null,
          ]}) : null,
          group.resolved && group.resolved.missing && group.resolved.missing.length
            ? jsxs('div', { title: `These members no longer exist on disk: ${group.resolved.missing.join(', ')}. They are skipped, not recreated.`,
                style: { display: 'flex', alignItems: 'center', gap: '4px', fontSize: '9.5px', color: red },
                children: [jsx(Icon, { name: 'alert', size: 11 }), `${group.resolved.missing.length} member(s) not found`] })
            : null,
        ],
      }) : null,
    ],
  }) : jsxs('div', {
    title: 'No group selected. A group is a named set of agents that share config — create one with “+ group” above, or just focus an agent below and stage skills straight onto it.',
    style: { flex: '0 0 auto', borderBottom: '1px solid ' + stroke, padding: '7px 9px', display: 'flex', alignItems: 'center', gap: '6px' },
    children: [
      jsx('span', { title: 'No group selected, so there is no policy to configure. A group is optional — you can stage skills straight onto an agent.', style: { ...labelStyle }, children: 'Policy' }),
      jsx('span', { style: { fontSize: '10px', color: faint }, children: 'no group selected' }),
      jsx('span', { style: { flex: '1 1 0' } }),
      jsx(InlineAction, { label: '+ group', onClick: startNewGroup, hint: 'Create a group — a named set of agents that share config. Optional: you can stage straight onto agents without one.' }),
    ],
  })

  // ── BACKUPS — a collapsed block under SYNC POLICY ──
  const backupsSection = jsxs('div', {
    style: { flex: '0 0 auto', borderBottom: '1px solid ' + stroke, display: 'flex', flexDirection: 'column' },
    children: [
      jsxs('div', {
        title: backupsOpen
          ? 'Collapse backups.'
          : 'Expand backups — snapshot a profile, then restore or delete what you have. A restore is itself undoable.',
        onClick: () => { haptic('tap'); setBackupsOpen(o => !o) },
        style: { display: 'flex', alignItems: 'center', gap: '5px', padding: '5px 9px', cursor: 'pointer', userSelect: 'none', WebkitUserSelect: 'none' },
        children: [
          jsx('span', { style: chromeIcon, children: jsx(Icon, { name: backupsOpen ? 'down' : 'chevron', size: 12 }) }),
          jsx('span', { title: 'Archives of a profile’s configuration: SOUL.md, AGENTS.md, profile.yaml, memories/, skills/ and .profile-pane/. Kept under ~/.hermes/profile-pane/backups/.', style: { ...labelStyle, color: sec, flex: '0 0 auto' }, children: 'Backups' }),
          jsx('span', { title: `${backups.length} archive(s) on disk`, style: { fontSize: '9px', color: faint, flex: '0 0 auto' }, children: backups.length }),
          jsx('span', { style: { flex: '1 1 0' } }),
          jsx(InlineAction, { label: focusAgent ? `back up ${focusAgent}` : 'back up', onClick: e => { e.stopPropagation(); void backupCreate(focusAgent || (group && members[0])) }, hint: focusAgent ? `Archive ${focusAgent}’s configuration now` : 'Focus a profile first, or use “back up all”.', stop: true }),
          jsx(InlineAction, { label: 'back up all', onClick: e => { e.stopPropagation(); void backupCreate('all') }, hint: 'One archive per profile — so a restore stays per-profile and cannot half-succeed across the machine.', stop: true }),
        ],
      }),
      backupsOpen ? jsxs('div', { style: { padding: '0 9px 7px', display: 'flex', flexDirection: 'column', gap: '4px' }, children: [
        jsxs('label', {
          title: 'OFF by default. A backup is a SECOND copy of a secret, and making one silently is not the pane’s call. Tick this only when you actually want .env and auth.json inside the archive.',
          onClick: e => e.stopPropagation(),
          style: { display: 'flex', alignItems: 'center', gap: '5px', fontSize: '9.5px', color: backupSecrets ? red : faint, cursor: 'pointer' },
          children: [
            jsx('input', { type: 'checkbox', checked: backupSecrets, onChange: () => setBackupSecrets(v => !v), style: { margin: 0, accentColor: 'var(--ui-accent)' } }),
            backupSecrets ? 'INCLUDING .env and auth.json' : 'secrets excluded (.env, auth.json)',
          ],
        }),
        backups.length === 0
          ? jsx(Empty, { hint: 'A backup archives SOUL.md, AGENTS.md, profile.yaml, memories/, skills/ and .profile-pane/ for one profile.', children: 'No archives yet — “back up all” makes one per profile.' })
          : jsx('div', { style: { maxHeight: '132px', overflowY: 'auto', display: 'flex', flexDirection: 'column' }, children: backups.map(b => jsxs('div', {
              key: b.file,
              style: { display: 'flex', alignItems: 'center', gap: '5px', fontSize: '9.5px', padding: '1px 0' },
              children: [
                jsx('span', { title: `${b.file} — ${(b.bytes / 1024).toFixed(1)} KB`, style: { flex: '1 1 0', minWidth: 0, color: sec, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: b.profile }),
                jsx('span', { style: { flex: '0 0 auto', color: faint, fontVariantNumeric: 'tabular-nums' }, children: `${b.at.slice(4, 6)}/${b.at.slice(6, 8)} ${b.at.slice(9, 11)}:${b.at.slice(11, 13)}` }),
                jsx('span', { style: { flex: '0 0 auto', color: faint, fontVariantNumeric: 'tabular-nums' }, children: `${(b.bytes / 1024).toFixed(0)}K` }),
                jsx(InlineAction, { label: 'restore', onClick: () => void backupRestore(b.file), hint: `Write ${b.file} back over ${b.profile}. The current state is snapshotted first, so this restore is itself undoable from Commits.` }),
                jsx(InlineAction, { label: 'delete', onClick: () => void backupDelete(b.file), hint: `Delete ${b.file} from disk`, tone: 'red' }),
              ],
            })) }),
      ]}) : null,
    ],
  })

  // ── SKILLS box — the WHOLE machine's skills, marked against the focused profile ──
  // The library is the UNION of every profile's deployed skills, not one profile's
  // inventory: what the focused profile already HAS is marked, and everything else is
  // available to add. Clicking an available skill stages it; clicking an installed one
  // is a no-op, because there is nothing to add. It opens when you pick a profile.
  const lib = allSkills.filter(s => !q || s.rel.toLowerCase().includes(q.toLowerCase()))
  const installed = new Set((focusProfile?.skills || []).map(s => s.rel))
  const avail = focusAgent ? lib.filter(s => !installed.has(s.rel)) : lib

  const libraryBox = jsxs(Box, {
    style: boxesCollapsed ? { flex: '0 0 auto' } : undefined,
    order: flipped ? 2 : 1,
    style: libOpen ? undefined : { flex: '0 0 auto' },
    children: [
      jsxs(BoxHead, {
        // The WHOLE bar toggles — the caret is an affordance, not the only hit target.
        title: libOpen
          ? 'Collapse the skills list. Click anywhere on this bar.'
          : 'Expand the skills list. Click anywhere on this bar — it also opens on its own when you pick a profile.',
        onClick: () => { haptic('tap'); setLibOpen(o => !o) },
        style: { cursor: 'pointer', userSelect: 'none', WebkitUserSelect: 'none' },
        children: [
        jsx('span', {
          style: chromeIcon,
          children: jsx(Icon, { name: libOpen ? 'down' : 'chevron', size: 12 }),
        }),
        jsx('span', {
          title: focusAgent
            ? `Every skill on this machine, compared against ${focusAgent}. Bold rows are already in it; the rest are available to add.`
            : `Every skill on this machine (${lib.length}), unioned across ${allData?.profile_count || 0} profiles. Click a profile row to mark which of these it already has.`,
          style: labelStyle, children: `Skills (${lib.length})`,
        }),
        focusAgent
          ? jsx('span', { title: `${avail.length} of these are NOT in ${focusAgent} yet`, style: { fontSize: '9px', color: accent, fontWeight: 700 }, children: `${avail.length} to add` })
          : jsx('span', { title: 'Click a profile row in the Profiles box to choose what you are adding to.', style: { fontSize: '9px', color: faint, fontStyle: 'italic' }, children: 'pick a profile ↓' }),
        jsx('span', { style: { flex: '1 1 0' } }),
        // rename targets a single skill, so it appears once the filter narrows to one
        libOpen && lib.length === 1 && !renamingSkill
          ? jsx(InlineAction, {
              label: 'rename',
              onClick: e => { e.stopPropagation(); haptic('tap'); setRenamingSkill(lib[0].rel); setSkillNameDraft(lib[0].name) },
              hint: `Rename “${lib[0].name}” EVERYWHERE — every profile holding it gets the new name. A skill's path is its identity, so a per-profile rename would fork it into two.`,
              stop: true,
            })
          : null,
        libOpen && renamingSkill
          ? jsxs('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '3px', flex: '0 0 auto' }, onClick: e => e.stopPropagation(), children: [
              jsx('input', {
                value: skillNameDraft, autoFocus: true,
                title: 'The new NAME only — its category folder stays where it is. Enter to rename everywhere, Escape to cancel.',
                onInput: e => setSkillNameDraft(e.target.value),
                onKeyDown: e => {
                  if (e.key === 'Enter') void doSkillRename()
                  if (e.key === 'Escape') setRenamingSkill(null)
                },
                style: { width: '104px', fontSize: '9.5px', height: '18px', padding: '0 4px', borderRadius: '4px', border: '1px solid ' + stroke, background: 'var(--ui-bg-chrome)', color: 'var(--ui-text-primary)' },
              }),
              jsx('button', { onClick: () => void doSkillRename(), title: 'Rename it in every profile that holds it', style: { fontSize: '9.5px', cursor: 'pointer', color: 'var(--ui-text-primary)', background: 'transparent', border: 'none' }, children: 'ok' }),
              jsx('button', { onClick: () => setRenamingSkill(null), title: 'Cancel', style: { fontSize: '9.5px', cursor: 'pointer', color: faint, background: 'transparent', border: 'none' }, children: '×' }),
            ]})
          : null,
        focusAgent && libOpen && avail.length ? jsx(InlineAction, {
          label: `stage ${avail.length}`,
          stop: true,
          onClick: () => {
            setStaged(prev => {
              const add = avail.map(s => ({ skill: s.rel, profile: focusAgent }))
              const seen = new Set(prev.map(s => s.skill + '\u0000' + s.profile))
              return [...prev, ...add.filter(a => !seen.has(a.skill + '\u0000' + a.profile))]
            })
            setPreview(null)   // lit bar, not an opened one
            flash(`staged ${avail.length} → ${focusAgent}`, 'check', 3500)
          },
          hint: `Stage every skill ${focusAgent} does NOT already have (${avail.length}). Then Apply.`,
        }) : null,
      ]}),
      libOpen ? jsxs('div', {
        // This wrapper MUST carry the flex chain: a plain <div> inside a flex column
        // sizes to content, so the BoxBody inside it has no height to scroll within and
        // the list simply clips. `flex:1 1 0; minHeight:0` is what makes it scroll.
        style: { flex: '1 1 0', minHeight: 0, display: 'flex', flexDirection: 'column' },
        children: [
        jsx('div', {
          style: { flex: '0 0 auto', display: boxesCollapsed ? 'none' : 'flex', alignItems: 'center', gap: '4px', borderBottom: '1px solid ' + stroke, padding: '3px 6px' },
          children: [
            jsx('span', { style: chromeIcon, children: jsx(Icon, { name: 'search', size: 11 }) }),
            jsx('input', {
              autoFocus: false, placeholder: 'filter skills',
              title: 'Filter the skills by name. Only matches are shown — and “stage N” respects the filter.',
              onInput: e => setQ(e.target.value),
              style: { flex: '1 1 0', minWidth: 0, fontSize: '10px', background: 'transparent', border: 'none', outline: 'none', color: 'var(--ui-text-primary)' },
            }),
            q ? jsx(InlineAction, { label: 'clear', onClick: () => setQ(''), hint: 'Clear the skill filter' }) : null,
          ]
        }),
        jsx(BoxBody, { style: boxesCollapsed ? { display: 'none' } : undefined,
          children: lib.length === 0
            ? jsx(Empty, { hint: 'Skills are read from every profile’s deployed tree, so this should never be empty unless the pane backend is not answering.', children: q ? 'No skill matches that filter.' : 'No skills found on this machine.' })
            : jsxs('div', { style: { display: 'flex', flexWrap: 'wrap', gap: '3px' }, children: [
                ...lib.slice(0, 160).map(s => {
                  const has = !!focusAgent && installed.has(s.rel)
                  const on = !!focusAgent && stagedKeys.has(s.rel + '\u0000' + focusAgent)
                  const removing = on && (stagedBy.get(s.rel + '\u0000' + focusAgent) || {}).op === 'remove'
                  return jsx('span', {
                    key: s.rel,
                    onPointerDown: e => onChipDown(e, s.rel),
                    onClick: () => {
                      if (!focusAgent) { flash('click a profile row first', 'check', 3200); return }
                      if (!justDragged.current && !dragRef.current) {
                        haptic('tap'); toggleStage(s.rel, focusAgent, has ? 'remove' : 'add')
                      }
                    },
                    title: has
                      ? (on ? `${s.rel} is installed in ${focusAgent} and STAGED FOR REMOVAL. Click to cancel.`
                            : `${s.rel} — already in ${focusAgent}. Click to stage a REMOVAL (moved to the profile's trash, restorable).`)
                      : (on ? `Staged to add → ${focusAgent}. Click to remove it from the queue.`
                            : (!focusAgent ? `${s.rel} — click a profile row first, or drag this onto one`
                                           : `Click to stage ${s.rel} → ${focusAgent}   ·   or drag onto any profile`)),
                    style: {
                      fontSize: '9.5px', padding: '1px 6px', borderRadius: '4px',
                      border: '1px solid ' + (on || has ? accent : stroke),
                      background: on ? 'color-mix(in srgb, var(--ui-accent) 16%, transparent)'
                                : has ? 'color-mix(in srgb, var(--ui-accent) 8%, transparent)' : 'transparent',
                      color: on ? accent : has ? sec : (focusAgent ? sec : tert),
                      fontWeight: on || has ? 700 : 400,
                      cursor: has ? 'default' : (focusAgent ? 'copy' : 'grab'),
                      touchAction: 'none', userSelect: 'none', WebkitUserSelect: 'none',
                      display: 'inline-flex', alignItems: 'center', gap: '3px',
                      opacity: focusAgent ? 1 : 0.72,
                    },
                    children: [
                      jsx(Icon, { name: removing ? 'trash' : on || has ? 'check' : 'grip', size: 9 }),
                      removing ? jsx('span', { style: { textDecoration: 'line-through' }, children: s.name }) : s.name,
                      has ? jsx('span', { title: `Present in ${s.in.length} profile(s): ${s.in.join(', ')}`, style: { color: faint, fontSize: '8.5px' }, children: s.in.length }) : null,
                    ],
                  })
                }),
                lib.length > 160 ? jsx('span', { title: 'Filter to narrow the list', style: { fontSize: '9.5px', color: faint }, children: `+${lib.length - 160} more — filter to narrow` }) : null,
              ]}),
        }),
      ]}) : null,
    ]
  })

  const profilesBox = jsxs(Box, {
    style: boxesCollapsed ? { flex: '0 0 auto' } : undefined,
    order: flipped ? 1 : 2,
    children: [
      jsxs(BoxHead, { style: { paddingLeft: '3px' }, children: [
        jsx('span', { title: `Every Hermes profile on this machine (${profilesView.length} shown). A filled row is a member of the current group; the target icon marks the click-to-stage focus. Click a row to focus it and open the library marked against it.`, style: { ...labelStyle, color: accent }, children: `Profiles (${profilesView.length})` }),
        jsx('span', { style: { flex: '1 1 0' } }),
        focusAgent
          ? jsx(InlineAction, { label: `focus: ${focusAgent}`, onClick: () => setFocusAgent(null), hint: `Stop sending skill clicks to ${focusAgent}` })
          : jsx('span', { title: 'Click a profile row to focus it — then skill clicks stage onto that profile, and the library marks what it already has', style: { fontSize: '9px', color: faint }, children: 'click a row to focus' }),
      ]}),
      jsx('div', {
        style: { flex: '0 0 auto', display: boxesCollapsed ? 'none' : 'flex', alignItems: 'center', gap: '4px', borderBottom: '1px solid ' + stroke, padding: '3px 6px' },
        children: [
          jsx('span', { style: chromeIcon, children: jsx(Icon, { name: 'search', size: 11 }) }),
          jsx('input', {
            value: aq, placeholder: 'filter profiles',
            title: 'Filter profiles by name.',
            onInput: e => setAq(e.target.value),
            style: { flex: '1 1 0', minWidth: 0, fontSize: '10px', background: 'transparent', border: 'none', outline: 'none', color: 'var(--ui-text-primary)' },
          }),
          aq ? jsx(InlineAction, { label: 'clear', onClick: () => setAq(''), hint: 'Clear the profile filter' }) : null,
        ]
      }),
      jsx(BoxBody, { style: boxesCollapsed ? { display: 'none' } : undefined,
        children: profilesView.length === 0
          ? jsx(Empty, { hint: 'Profiles live at ~/.hermes/profiles/<name>/. If none appear at all, the pane backend is not answering — the Python half mounts at gateway startup, so restart the app.', children: aq ? 'No agent matches that filter.' : 'No agents returned — is the pane backend running?' })
          : profilesView.map(p => {
              const isMember = members.includes(p.name)
              const count = staged.filter(s => s.profile === p.name).length
              const isFocus = focusAgent === p.name
              const isOpen = expanded === p.name
              const over = hover === p.name
              const level = p.budget?.level
              return jsxs('div', { key: p.name, children: [
                jsxs('div', {
                  ref: el => { rowRefs.current[p.name] = el },
                  onClick: () => { haptic('tap'); setFocusAgent(isFocus ? null : p.name); setLibOpen(true) },
                  title: isFocus ? 'Focused — skill clicks land here. Click again to clear focus.' : `Focus ${p.name} so skill clicks stage here`,
                  style: {
                    display: 'flex', alignItems: 'center', gap: '5px',
                    padding: '3px 6px', marginBottom: '2px', borderRadius: '5px',
                    cursor: 'pointer', userSelect: 'none', WebkitUserSelect: 'none',
                    border: '1px solid ' + (over ? accent : isFocus ? accent : isMember ? 'color-mix(in srgb, var(--ui-accent) 30%, transparent)' : 'transparent'),
                    background: over ? 'color-mix(in srgb, var(--ui-accent) 12%, transparent)'
                      : isFocus ? 'color-mix(in srgb, var(--ui-accent) 8%, transparent)' : 'transparent',
                    transition: 'background 90ms linear, border-color 90ms linear',
                  },
                  children: [
                    jsx('span', {
                      title: isOpen ? 'Collapse this agent' : 'Expand to see this agent’s skills and actions',
                      onClick: e => { e.stopPropagation(); haptic('tap'); setExpanded(isOpen ? null : p.name) },
                      style: { display: 'flex', flex: '0 0 auto', color: faint, cursor: 'pointer' },
                      children: jsx(Icon, { name: isOpen ? 'down' : 'chevron', size: 12 }),
                    }),
                    isFocus
                      ? jsx('span', { title: 'Focused — skill clicks land on this agent. Click the row again to clear.', style: { display: 'flex' }, children: jsx(Icon, { name: 'target', size: 12 }) })
                      : jsx('span', { title: 'Click this row to focus it, so skill clicks stage here', style: { width: '12px' } }),
                    jsx('span', { style: { fontSize: '10.5px', fontWeight: isMember ? 600 : 400, color: isMember ? 'var(--ui-text-primary)' : tert, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: p.name }),
                    jsx('span', { title: `${(p.skills || []).length} skills installed in this agent (read from disk, not from a declared list)`,
                      style: { fontSize: '9px', color: faint, fontVariantNumeric: 'tabular-nums', flex: '0 0 auto' }, children: `${(p.skills || []).length}` }),
                    // Membership sits LEFT of the spacer so it can never be pushed off
                    // the row edge by the budget/drift badges. It is a bordered chip, not
                    // faint text — the previous version rendered in `faint` at the far
                    // right, which read as "there is no add button".
                    jsx('span', {
                      title: isMember
                        ? `In this group — click to remove ${p.name}. The agent and its files are untouched.`
                        : `Add ${p.name} to this group. Membership only decides who this group pushes to; nothing moves until you Apply.`,
                      onClick: e => { e.stopPropagation(); e.preventDefault(); haptic('tap'); toggleMember(p.name) },
                      style: {
                        fontSize: '9px', fontWeight: 600, padding: '1px 5px', borderRadius: '4px',
                        display: 'inline-flex', alignItems: 'center', gap: '3px', flex: '0 0 auto',
                        cursor: 'pointer', userSelect: 'none', WebkitUserSelect: 'none',
                        border: '1px solid ' + (isMember ? accent : stroke),
                        background: isMember ? 'color-mix(in srgb, var(--ui-accent) 16%, transparent)' : 'transparent',
                        color: isMember ? accent : tert,
                      },
                      children: isMember
                        ? [jsx(Icon, { name: 'check', size: 9 }), 'member']
                        : [jsx(Icon, { name: 'plus', size: 9 }), 'add'],
                    }),
                    jsx('span', { style: { flex: '1 1 0' } }),
                    jsx('span', {
                      title: `always-on context: soul ${p.budget?.parts?.soul || 0} + memory ${p.budget?.parts?.memory || 0} + agents ${p.budget?.parts?.agents || 0} = ${p.budget?.lines || 0} lines. Over 200 is a dilution risk.`,
                      style: { fontSize: '9px', fontVariantNumeric: 'tabular-nums', flex: '0 0 auto',
                        color: level === 'alert' ? red : level === 'warn' ? warn : faint },
                      children: `${p.budget?.lines || 0}ln`,
                    }),
                    count ? jsx('span', { title: `${count} skill(s) staged for this agent — press Apply to copy them`,
                      style: { fontSize: '9px', color: accent, border: '1px solid ' + accent, borderRadius: '4px', padding: '0 4px', flex: '0 0 auto' }, children: `+${count}` }) : null,
                    (driftBy[p.name] || []).length
                      ? jsx('span', {
                          title: `${driftBy[p.name].length} skill(s) have drifted since this pane copied them: `
                            + driftBy[p.name].map(e => `${e.skill} — ${e.state}`).join(' · ')
                            + '. Click to list them and re-apply one.',
                          onClick: e => { e.stopPropagation(); haptic('tap'); setDriftFor(driftFor === p.name ? null : p.name) },
                          style: { fontSize: '9px', color: warn, border: '1px solid ' + warn, borderRadius: '4px', padding: '0 4px', flex: '0 0 auto', cursor: 'pointer' },
                          children: `drift ${driftBy[p.name].length}${driftFor === p.name ? ' ▾' : ' ▸'}`,
                        })
                      : null,
                  ],
                }),

                // ── the drift list: one row per drifted skill, each re-appliable on its own
                driftFor === p.name ? jsxs('div', { style: { margin: '0 0 4px 0', padding: '4px 7px', borderRadius: '5px', background: 'color-mix(in srgb, #c9a227 10%, transparent)' }, children: [
                  jsx('div', { title: 'A skill drifts when the source it was copied from has moved since, or the copy here was edited. Re-applying copies the source over this profile again — snapshot first.', style: { ...labelStyle, color: warn, marginBottom: '2px' }, children: `Drifted (${driftBy[p.name].length})` }),
                  ...driftBy[p.name].map((e, i) => jsxs('div', {
                    key: i,
                    style: { display: 'flex', alignItems: 'center', gap: '5px', fontSize: '9.5px' },
                    children: [
                      jsx('span', { title: `${e.skill} — ${e.state}`, style: { flex: '1 1 0', minWidth: 0, color: tert, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: e.skill.split('/').pop() }),
                      jsx('span', { title: e.state === 'Upstream-moved' ? 'The SOURCE moved — re-applying brings the new version here.' : (e.state === 'Local-diverged' ? 'This copy was edited locally — re-applying OVERWRITES those edits (snapshot first).' : 'Both sides moved since the copy.'), style: { flex: '0 0 auto', color: warn, fontSize: '9px' }, children: e.state }),
                      jsx(InlineAction, { label: 're-apply', onClick: () => void reapplyOne(p.name, e.skill), hint: `Copy the source over this profile’s copy now, instead of waiting for the scheduled pass. Snapshot first.` }),
                    ],
                  })),
                ]}) : null,

                isOpen ? jsxs('div', {
                  title: `${p.name} — expanded. Actions here operate on this agent alone.`,
                  style: { margin: '0 0 4px 0', borderRadius: '5px', padding: '5px 7px 6px', display: 'flex', flexDirection: 'column', gap: '5px', background: 'color-mix(in srgb, var(--ui-text-primary) 5%, transparent)' },
                  children: [
                    jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: '7px', flexWrap: 'wrap' }, children: [
                      jsx(InlineAction, { label: 'reveal', onClick: () => reveal(p.path + '/skills'), hint: 'Reveal this agent’s skills folder in Finder' }),
                      jsx(InlineAction, { label: 'focus', onClick: () => { haptic('tap'); setFocusAgent(isFocus ? null : p.name); setLibOpen(true) }, hint: isFocus ? 'Clear the click-to-stage focus' : `Send skill clicks to ${p.name} and open the library marked against it` }),
                      jsx(InlineAction, { label: isMember ? 'remove member' : 'make member', onClick: () => toggleMember(p.name), hint: isMember ? `Remove ${p.name} from group “${group ? group.id : '—'}”` : `Add ${p.name} to group “${group ? group.id : '—'}”`, tone: isMember ? 'red' : undefined }),
                      jsx(InlineAction, { label: `${count} staged`, onClick: () => { haptic('tap'); setStaged(s => s.filter(x => x.profile !== p.name)); setPreview(null) }, hint: count ? `Clear the ${count} staged skill(s) targeting this agent` : 'Nothing staged for this agent yet' }),
                    ]}),
                    jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: '7px', flexWrap: 'wrap' }, children: [
                      jsx('span', { title: 'Read and edit the files this pane can push for this agent. Every save snapshots first.', style: labelStyle, children: 'Audit' }),
                      ...['memory', 'user', 'profile'].map(w => jsx(InlineAction, {
                        key: w, label: LAYER_META[w].label,
                        onClick: () => void openAudit(p.name, w),
                        hint: `Open ${LAYER_META[w].short} for ${p.name} — read, edit, save`,
                      })),
                    ]}),
                    jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: '7px', flexWrap: 'wrap' }, children: [
                      jsx(InlineAction, {
                        label: trash && trash.profile === p.name ? 'hide trash' : 'trash',
                        onClick: () => void openTrash(p.name),
                        hint: `Removed skills for ${p.name} — kept under .profile-pane/trash/, and restorable at any time.`,
                      }),
                      trash && trash.profile === p.name && trash.items.length === 0
                        ? jsx('span', { style: { fontSize: '9px', color: faint, fontStyle: 'italic' }, children: 'nothing removed' })
                        : null,
                    ]}),
                    trash && trash.profile === p.name && trash.items.length
                      ? jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: '2px' }, children: [
                          jsx('div', { title: 'A removal MOVES the skill here rather than deleting it, so the reverse is a rename back.', style: { ...labelStyle }, children: `Trash (${trash.items.length})` }),
                          ...trash.items.map(t => jsxs('div', {
                            key: t.trash,
                            style: { display: 'flex', alignItems: 'center', gap: '5px', fontSize: '9.5px' },
                            children: [
                              jsx('span', { title: `${t.skill} — ${t.files} file(s), removed ${t.at}`, style: { flex: '1 1 0', minWidth: 0, color: tert, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: t.skill }),
                              jsx('span', { style: { color: faint, flex: '0 0 auto' }, children: t.at }),
                              jsx(InlineAction, { label: 'restore', onClick: () => void restoreSkill(p.name, t.trash), hint: `Put ${t.skill} back into ${p.name}` }),
                            ],
                          })),
                        ]})
                      : null,
                    audit && audit.profile === p.name ? jsxs('div', {
                      style: { border: '1px solid ' + stroke, borderRadius: '5px', padding: '5px', display: 'flex', flexDirection: 'column', gap: '4px' },
                      children: [
                        jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: '5px' }, children: [
                          jsx('span', { title: LAYER_META[audit.which].hint, style: labelStyle, children: `${LAYER_META[audit.which].label} · ${audit.profile}` }),
                          jsx('span', { style: { flex: '1 1 0' } }),
                          jsx(InlineAction, { label: 'save', onClick: () => void saveAudit(), hint: 'Write this back. The current file is snapshotted first, so the change is reversible.' }),
                          jsx(InlineAction, { label: 'close', onClick: () => setAudit(null), hint: 'Close without saving' }),
                        ]}),
                        auditErr ? jsx('div', { style: { fontSize: '9.5px', color: red }, children: auditErr }) : null,
                        audit.which === 'profile'
                          ? jsx('textarea', {
                              value: auditDesc, rows: 3,
                              title: 'The authored description for this agent — what shows in agent lists and Bot Chat headers.',
                              onInput: e => setAuditDesc(e.target.value),
                              style: { flex: '1 1 0', minWidth: 0, fontSize: '10px', fontFamily: 'inherit', padding: '3px 5px', borderRadius: '4px', border: '1px solid ' + stroke, background: 'var(--ui-bg-chrome)', color: 'var(--ui-text-primary)', resize: 'vertical' },
                            })
                          : jsxs('div', { style: { maxHeight: '170px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '3px' }, children: [
                              auditBlocks.length === 0
                                ? jsx(Empty, { hint: 'An empty file. “+ block” adds the first entry.', children: 'No entries.' })
                                : null,
                              ...auditBlocks.map((b, i) => jsxs('div', { key: i, style: { display: 'flex', gap: '4px', alignItems: 'flex-start' }, children: [
                                jsx('textarea', {
                                  value: b, rows: 2,
                                  title: `Block ${i + 1} of ${auditBlocks.length}. One fact per block — blocks are separated by a “§” line when saved.`,
                                  onInput: e => setAuditBlocks(prev => prev.map((x, j) => j === i ? e.target.value : x)),
                                  style: { flex: '1 1 0', minWidth: 0, fontSize: '10px', fontFamily: 'inherit', padding: '3px 5px', borderRadius: '4px', border: '1px solid ' + stroke, background: 'var(--ui-bg-chrome)', color: 'var(--ui-text-primary)', resize: 'vertical' },
                                }),
                                jsx(InlineAction, { label: '×', onClick: () => setAuditBlocks(prev => prev.filter((_, j) => j !== i)), hint: 'Delete this block', tone: 'red' }),
                              ]})),
                              jsx(InlineAction, { label: '+ block', onClick: () => setAuditBlocks(prev => [...prev, '']), hint: 'Add an empty block at the end' }),
                            ]}),
                      ],
                    }) : null,
                    // WHAT THIS PROFILE IS. Its skills already appear in the SKILLS box
                    // marked against it, so listing them again here was pure duplication.
                    // This answers the question the list cannot: what IS this thing?
                    jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: '3px' }, children: [
                      jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: '5px' }, children: [
                        jsx('span', { title: 'The authored description in profile.yaml — the line shown in agent lists and Bot Chat headers.', style: { ...labelStyle }, children: 'Description' }),
                        jsx('span', { style: { flex: '1 1 0' } }),
                        editingDesc === p.name
                          ? null
                          : jsx(InlineAction, {
                              label: p.description ? 'edit' : 'write one',
                              onClick: () => { setEditingDesc(p.name); setDescDraft(p.description || '') },
                              hint: `Write ${p.name}’s description. Stored in profile.yaml; the gateway's own ui_meta is never touched.`,
                            }),
                      ]}),
                      editingDesc === p.name
                        ? jsxs('div', { style: { display: 'flex', gap: '5px', alignItems: 'stretch' }, children: [
                            jsx('textarea', {
                              value: descDraft, rows: 2, autoFocus: true,
                              title: 'One or two lines. Enter makes a newline — press save when done; Escape cancels.',
                              onInput: e => setDescDraft(e.target.value),
                              onKeyDown: e => { if (e.key === 'Escape') setEditingDesc(null) },
                              style: { flex: '1 1 0', minWidth: 0, fontSize: '10px', lineHeight: 1.4, fontFamily: 'inherit', padding: '3px 5px', borderRadius: '4px', border: '1px solid ' + accent, background: 'var(--ui-bg-chrome)', color: 'var(--ui-text-primary)', resize: 'vertical' },
                            }),
                            jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: '2px', justifyContent: 'center' }, children: [
                              jsx(InlineAction, { label: 'save', onClick: () => void saveDesc(p.name), hint: 'Write it to profile.yaml. A snapshot is taken first, so it is reversible.' }),
                              jsx(InlineAction, { label: 'cancel', onClick: () => setEditingDesc(null), hint: 'Discard this edit' }),
                            ]}),
                          ]})
                        : jsx('div', {
                            title: p.description ? `${p.name}: ${p.description}` : `“${p.name}” carries no description. Click “write one”.`,
                            style: { fontSize: '10px', lineHeight: 1.4, color: p.description ? 'var(--ui-text-primary)' : faint, fontStyle: p.description ? 'normal' : 'italic' },
                            children: p.description || 'no description set',
                          }),
                      jsx('div', { style: { display: 'flex', flexWrap: 'wrap', gap: '3px', marginTop: '2px' }, children: [
                        jsx(Fact, { label: 'Skills', value: (p.skills || []).length, hint: `${(p.skills || []).length} skills installed in this profile, read from disk. They are listed in the SKILLS box, marked against this profile.` }),
                        jsx(Fact, { label: 'SOUL', value: `${p.soul_lines || 0}L`, hint: `SOUL.md is ${p.soul_lines || 0} lines${p.soul_has_region ? ', and carries a pane-managed region (this profile has been synced before)' : ''}.`, tone: (p.soul_lines || 0) > 400 ? 'warn' : undefined }),
                        jsx(Fact, { label: 'Sections', value: p.soul_sections || 0, hint: `${p.soul_sections || 0} ## headings in this profile's SOUL.md — the sections the 'Sections' scope can copy from it if you make it an anchor.` }),
                        jsx(Fact, { label: 'Memory', value: `${p.memory_blocks || 0}§`, hint: `${p.memory_blocks || 0} §-blocks in memories/MEMORY.md — this profile's accumulated notes.` }),
                        jsx(Fact, { label: 'User', value: `${p.user_blocks || 0}§`, hint: `${p.user_blocks || 0} §-blocks in memories/USER.md — what this profile knows about you.` }),
                        jsx(Fact, { label: 'Always-on', value: `${p.budget?.lines || 0}ln`, hint: `always-on context: soul ${p.budget?.parts?.soul || 0} + memory ${p.budget?.parts?.memory || 0} + agents ${p.budget?.parts?.agents || 0} = ${p.budget?.lines || 0} lines. Over 200 is a dilution risk.`, tone: p.budget?.level === 'alert' ? 'red' : p.budget?.level === 'warn' ? 'warn' : undefined }),
                      ]}),
                    ]}),
                  ],
                }) : null,
              ]})
            })
          })
    ]
  })

  // ── 1. STAGED — the topmost block ──
  // It owns its verbs (swap, refresh) and its two buttons, and it FOLDS: empty means one
  // quiet line, and the moment there is something pending it opens itself. Committing is
  // the last thing you do, so it is the first thing you see.
  const pendingPolicyCount = pendingPolicy
    ? Object.keys(pendingPolicy).filter(k => JSON.stringify(pendingPolicy[k]) !== JSON.stringify((group?.policy || {})[k])).length
    : 0
  const pending = staged.length + pendingPolicyCount + memberChanged + pendingBlocks.length
  const applyFilled = staged.length > 0 || pendingPolicyCount > 0 || pendingBlocks.length > 0
  const applyDisabled = busy || (pending === 0 && !(group && (layerPush().length > 0 || soulScope !== 'off')))

  const wasPending = useRef(false)
  useEffect(() => {
    // It closes itself when the queue empties — an empty panel is dead weight — but it
    // never OPENS itself. Staging lights the bar; opening is the user's move.
    if (pending === 0 && wasPending.current) setStagedOpen(false)
    wasPending.current = pending > 0
  }, [pending])

  // ── what is ACTUALLY staged, spelled out ──────────────────────────────────────
  // A chip reading "policy ×3" tells you a count. This tells you the changes.
  const POLICY_LABEL = { soul: 'Soul', soul_sections: 'Sections', skills: 'Skills',
                         memory: 'Memory', user: 'User', profile: 'Description',
                         auto: 'Auto', anchor: 'Anchor' }
  const showVal = (v) => {
    if (v === null || v === undefined || v === 'off' || v === '') return 'off'
    if (Array.isArray(v)) return v.length ? `${v.length} ticked` : 'none ticked'
    return String(v)
  }
  const policyChanges = pendingPolicy
    ? Object.keys(pendingPolicy)
        .filter(k => JSON.stringify(pendingPolicy[k]) !== JSON.stringify((group?.policy || {})[k]))
        .map(k => ({ key: k, label: POLICY_LABEL[k] || k,
                     from: showVal((group?.policy || {})[k]), to: showVal(pendingPolicy[k]) }))
    : []
  // skills grouped by the profile they are going to
  const byTarget = []
  for (const s of staged) {
    const op = s.op || 'add'
    let bucket = byTarget.find(b => b.target === s.profile && b.op === op)
    if (!bucket) { bucket = { target: s.profile, op, skills: [] }; byTarget.push(bucket) }
    if (!bucket.skills.includes(s.skill)) bucket.skills.push(s.skill)
  }
  const layerWork = group
    ? [...(soulScope !== 'off' ? [[`Soul (${soulScope})`, `from ${policy.anchor || 'no anchor'} → ${members.length - 1} member(s)`]] : []),
       ...layerPush().map(l => [LAYER_META[l]?.label || l, `${policy[l]} · from ${policy.anchor || 'no anchor'}`])]
    : []

  // The bar LIGHTS UP when there is work queued — it does not open itself. Opening is
  // your move; the light is the notification. A panel that opens under your cursor while
  // you are working elsewhere moves the thing you were about to click.
  //
  // `lit` is pending ONLY — never `trialLive`. A live trial means the work is already
  // DONE and an undo is available, which is the opposite of queued: including it left the
  // bar green forever after every Apply, still claiming there was something to do. The
  // undo signal has its own home — `Tab('commits', trialLive)` rings the Commits tab, and
  // the Commits view opens on its own UNDO AVAILABLE row.
  const lit = pending > 0
  const BAR_H = '26px'   // pinned so the fill cannot change the bar's geometry
  const barText = lit ? 'var(--ui-bg-chrome)' : sec
  const barDim = lit ? 'var(--ui-bg-chrome)' : faint

  const stagedSection = jsxs('div', {
    style: { flex: '0 0 auto', borderBottom: '1px solid ' + stroke, display: 'flex', flexDirection: 'column' },
    children: [
      jsxs('div', {
        title: stagedOpen
          ? 'Collapse the queue.'
          : (lit ? `${pending} item(s) waiting — click to open. Nothing is written until you press Apply.` : 'Everything this pane has done. Click to open.'),
        onClick: () => { haptic('tap'); setStagedOpen(o => !o) },
        style: {
          display: 'flex', alignItems: 'center', gap: '5px',
          // The height is PINNED so the lit state cannot be a different size from the
          // unlit one — the fill changes colour, never geometry.
          height: BAR_H, padding: '0 9px', boxSizing: 'border-box',
          cursor: 'pointer', userSelect: 'none', WebkitUserSelect: 'none',
          // lit = a solid accent fill with the text reversed into the surface colour,
          // so the bar reads as a state at a glance without opening anything.
          background: lit ? accent : 'transparent',
          color: barText,
        },
        children: [
          jsx('span', { style: { display: 'flex', flex: '0 0 auto', color: barDim }, children: jsx(Icon, { name: stagedOpen ? 'down' : 'chevron', size: 12 }) }),
          jsx('span', {
            title: 'Everything this pane is about to do, and everything it has done. Two views of the same thing.',
            style: { ...labelStyle, color: barText, flex: '0 0 auto' },
            children: 'Activity',
          }),
          jsx('span', {
            title: `Showing: ${activityTab === 'staged' ? 'Staged' : 'Commits'}. Switch views inside the block.`,
            style: { fontSize: '9px', color: barDim, flex: '0 0 auto', fontWeight: lit ? 600 : 400 },
            children: activityTab === 'staged' ? `staged ${pending}` : `commits ${commits.length}`,
          }),
          focusAgent && staged.length && !group
            ? jsx('span', { title: `These stage onto ${focusAgent} — no group is selected, so Apply targets the profiles you staged onto.`, style: { fontSize: '9px', color: faint }, children: `→ ${focusAgent}` })
            : null,
          group && layerPush().length
            ? jsx('span', { title: `Apply will also push ${layerPush().join(' + ')} from the anchor, ${policy.anchor || '—'}`, style: { fontSize: '9px', color: faint }, children: `+ ${layerPush().join(' + ')}` })
            : null,
          preview
            ? jsx('span', {
                title: preview.blocked ? preview.reason : 'Result of the last Preview. Preview runs the SAME preflight as Apply and writes nothing.',
                style: { fontSize: '9px', color: preview.blocked ? red : faint, fontVariantNumeric: 'tabular-nums' },
                children: preview.blocked ? `blocked — ${preview.reason}` : `${(preview.actions || []).length} copy · ${(preview.refusals || []).length} refused`,
              })
            : null,
          jsx('span', { style: { flex: '1 1 0' } }),
          note
            ? jsxs('span', {
                title: note.kind === 'spin' ? `${note.text} — working` : `${note.text} — done`,
                style: { display: 'flex', alignItems: 'center', gap: '4px', fontSize: '9px', color: sec, whiteSpace: 'nowrap', flex: '0 0 auto' },
                children: [
                  note.kind === 'spin'
                    ? jsx('span', { style: { width: '8px', height: '8px', borderRadius: '50%', flex: '0 0 auto', border: '1.5px solid ' + stroke, borderTopColor: accent, animation: 'paneSpin 0.7s linear infinite' } })
                    : jsx(Icon, { name: 'check', size: 10 }),
                  note.text,
                ] })
            : null,
          jsx('button', {
            onClick: e => { e.stopPropagation(); haptic('tap'); setFlipped(f => !f) },
            title: flipped ? 'Lift the SKILLS box to the top' : 'Lift the PROFILES box to the top — swaps the two lists without losing your place',
            // On the fill, a stroked box reads as extra mass and buries the glyph, so the
            // border drops out and the icon takes the reversed colour instead.
            style: { background: 'transparent', border: '1px solid ' + (lit ? 'transparent' : stroke), borderRadius: '5px', color: lit ? barText : tert, cursor: 'pointer', padding: '1px 4px', display: 'flex', flex: '0 0 auto' },
            children: jsx(Icon, { name: 'flip', size: 12 }),
          }),
          jsx('button', {
            onClick: e => { e.stopPropagation(); haptic('tap'); void invalidate() },
            title: 'Re-read profiles, groups, skills and drift from disk right now. The pane also polls every 5s.',
            style: { background: 'transparent', border: '1px solid ' + (lit ? 'transparent' : stroke), borderRadius: '5px', color: lit ? barText : tert, cursor: 'pointer', padding: '1px 4px', display: 'flex', flex: '0 0 auto' },
            children: jsx(Icon, { name: 'refresh', size: 12 }),
          }),
          staged.length
            ? jsx(InlineAction, { label: 'check', onClick: e => { e.stopPropagation(); void runCheck() }, hint: 'Run the preflight and report what Test/Apply would do or refuse — writes nothing, opens no trial.', stop: true })
            : null,
          pending
            ? jsx(InlineAction, { label: 'discard', onClick: e => { e.stopPropagation(); discardStaged() }, hint: 'Empty the queue AND throw away the staged policy edits. Nothing has been written yet.', tone: 'red', stop: true })
            : null,
        ],
      }),
      stagedOpen ? jsxs('div', {
        // Padding ABOVE as well as below: the lit bar's accent fill sits directly on
        // top of this row, and with no gap the green ran straight into the buttons.
        // Centered because the two tabs are peers, not a left-aligned list.
        style: { display: 'flex', gap: '3px', padding: '7px 9px 6px',
                 justifyContent: 'center' },
        children: [
        jsx('button', {
          onClick: () => { haptic('tap'); setActivityTab('staged') },
          title: `${pending} item(s) queued${pendingPolicyCount ? ` (${pendingPolicyCount} of them policy edits)` : ''}${pendingBlocks.length ? ` (${pendingBlocks.length} of them durable-fact edits)` : ''}. Nothing is written until you press Apply.`,
          style: Tab(activityTab === 'staged', pending > 0),
          children: `Staged${pending ? ` (${pending})` : ''}`,
        }),
        jsx('button', {
          onClick: () => { haptic('tap'); setActivityTab('commits') },
          title: `${commits.length} recent commit(s) — every write this pane has made, newest first. UNDO LAST is here${trialLive ? ' — undo available right now' : ''}.`,
          style: Tab(activityTab === 'commits', trialLive),
          children: `Commits${commits.length ? ` (${commits.length})` : ''}`,
        }),
      ]}) : null,

      // ── COMMITS — what actually happened, read back from the journal ──
      // ── WHAT UNDO WOULD PUT BACK ─────────────────────────────────────────────
      // This row used to read "UNDO AVAILABLE · 3 file(s) · [undo last]" — a count, not a
      // description. You were asked to press Undo with no way to see what it would
      // reverse. The route now returns the label and every op; this renders them, so the
      // decision is made against the actual list rather than a number.
      stagedOpen && activityTab === 'commits' ? jsxs('div', { style: { padding: '0 9px 7px', display: 'flex', flexDirection: 'column', gap: '4px' }, children: [
        trialLive
          ? jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: '3px' }, children: [
              jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: '6px' }, children: [
                jsx('span', { title: 'A trial is live. Every write it made was recorded with its reverse, so one call puts the whole tree back.', style: { fontSize: '9.5px', color: accent, fontWeight: 700, flex: '0 0 auto' }, children: 'UNDO AVAILABLE' }),
                trialData?.label
                  ? jsx('span', { title: `What started this act: "${trialData.label}"`, style: { fontSize: '9px', color: sec, flex: '1 1 auto', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: trialData.label })
                  : jsx('span', { style: { flex: '1 1 0' } }),
                jsx(InlineAction, { label: 'undo last', onClick: () => void runRevert(),
                  hint: `Put all ${trialData?.total || trialData?.ops || 0} path(s) listed below back exactly as they were. Single-use — once undone, it cannot be undone again.` }),
              ]}),
              whatUndo.length === 0
                ? jsx('div', { title: 'The trial recorded no paths — the act made no writes.', style: { fontSize: '9px', color: faint, fontStyle: 'italic' }, children: 'no paths recorded' })
                : jsx('div', { title: 'Every path Undo will reverse, in the order it was written.', style: { maxHeight: '118px', overflowY: 'auto', display: 'flex', flexDirection: 'column' },
                    children: [
                      ...whatUndo.map((w, i) => jsxs('div', {
                        key: i,
                        title: `${w.kind || 'path'}: ${w.path}\nUndo ${
                          w.existed ? 'restores this from the snapshot' : 'REMOVES it (it did not exist before)'}`,
                        style: { display: 'flex', alignItems: 'center', gap: '5px', fontSize: '9px', padding: '1px 0', color: tert },
                        children: [
                          jsx('span', { style: { display: 'flex', flex: '0 0 auto', color: w.existed ? accent : '#d98a8a' }, children: jsx(Icon, { name: w.existed ? 'refresh' : 'trash', size: 9 }) }),
                          jsx('span', { style: { flex: '1 1 auto', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', direction: 'rtl', textAlign: 'left' }, children: w.path }),
                          jsx('span', { style: { flex: '0 0 auto', color: faint, fontSize: '8.5px', textTransform: 'uppercase', letterSpacing: '.04em' }, children: w.existed ? 'restore' : 'remove' }),
                        ],
                      })),
                      (trialData?.total || 0) > whatUndo.length
                        ? jsx('div', { title: `${trialData.total} paths in total — the rest are reversed too.`, style: { fontSize: '8.5px', color: faint, fontStyle: 'italic', padding: '1px 0' }, children: `+ ${trialData.total - whatUndo.length} more` })
                        : null,
                    ] }),
            ]})
          : jsx('div', { title: 'Nothing is pending an undo — the last act either made no writes or has already been reverted. The journal below is history: it is not undoable.', style: { fontSize: '9.5px', color: faint, fontStyle: 'italic' }, children: 'nothing to undo' }),
        commits.length === 0
          ? jsx(Empty, { hint: 'Every write this pane makes is journalled the moment it lands.', children: 'No commits yet.' })
          : jsx('div', { style: { maxHeight: '146px', overflowY: 'auto', display: 'flex', flexDirection: 'column' }, children: commits.map((e, i) => {
              const meta = OP_META[e.op] || ['check', e.op]
              const who = e.profile || e.to || e.from || ''
              return jsxs('div', {
                key: i,
                title: `${new Date((e.at || 0) * 1000).toLocaleString()} — ${e.op}`
                       + (e.skill ? `\nskill: ${e.skill}` : '') + (e.file ? `\nfile: ${e.file}` : '')
                       + (e.snapshot ? `\nreverse: ${e.snapshot}` : '') + (e.reason ? `\n${e.reason}` : ''),
                style: { display: 'flex', alignItems: 'center', gap: '5px', fontSize: '9.5px', padding: '1px 0' },
                children: [
                  jsx('span', { style: { display: 'flex', flex: '0 0 auto', color: e.op === 'trial_revert' ? accent : faint }, children: jsx(Icon, { name: meta[0], size: 10 }) }),
                  jsx('span', { style: { flex: '0 0 74px', color: sec, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: meta[1] }),
                  jsx('span', { style: { flex: '1 1 0', minWidth: 0, color: tert, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: who }),
                  jsx('span', { style: { flex: '0 0 auto', color: faint, fontVariantNumeric: 'tabular-nums' }, children: whenOf(e.at) }),
                ],
              })
            }) }),
      ]}) : null,

      stagedOpen && activityTab !== 'commits' ? jsxs('div', { style: { padding: '0 9px 7px', display: 'flex', flexDirection: 'column', gap: '5px' }, children: [
        // ── policy: one line per changed setting, old → new ──
        memberChanged ? jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: '1px' }, children: [
          jsx('span', { title: 'Who is in this group, as staged. Apply writes the membership; discard reverts the panel. No profile is touched either way.', style: { ...labelStyle, color: accent }, children: `Membership (${memberChanged})` }),
          ...memberDiff.added.map(m => jsxs('div', {
            key: 'add-' + m,
            title: `ADD ${m} to this group. On Apply it becomes a member — and if any layer is enabled, the anchor’s SOUL, memory and keys start being written into it. It is not a member until you commit.`,
            style: { display: 'flex', alignItems: 'center', gap: '5px', fontSize: '9.5px' },
            children: [
              jsx('span', { style: { flex: '0 0 62px', color: sec }, children: 'join' }),
              jsx('span', { style: { color: faint }, children: '+' }),
              jsx('span', { style: { color: accent, fontWeight: 700 }, children: m }),
            ],
          })),
          ...memberDiff.removed.map(m => jsxs('div', {
            key: 'del-' + m,
            title: `REMOVE ${m} from this group. Its files are untouched — it simply stops being synced.`,
            style: { display: 'flex', alignItems: 'center', gap: '5px', fontSize: '9.5px' },
            children: [
              jsx('span', { style: { flex: '0 0 62px', color: sec }, children: 'leave' }),
              jsx('span', { style: { color: faint }, children: '−' }),
              jsx('span', { style: { color: faint, textDecoration: 'line-through' }, children: m }),
            ],
          })),
        ]}) : null,

        policyChanges.length ? jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: '1px' }, children: [
          jsx('span', { title: 'These group settings differ from what is saved. Apply writes them.', style: { ...labelStyle, color: accent }, children: `Policy (${policyChanges.length})` }),
          ...policyChanges.map(c => jsxs('div', {
            key: c.key,
            title: `${c.label}: ${c.from} → ${c.to}. Apply writes this to the group record; discard reverts the panel.`,
            style: { display: 'flex', alignItems: 'center', gap: '5px', fontSize: '9.5px' },
            children: [
              jsx('span', { style: { flex: '0 0 62px', color: sec }, children: c.label }),
              jsx('span', { style: { color: faint, textDecoration: 'line-through' }, children: c.from }),
              jsx('span', { style: { color: faint }, children: '→' }),
              jsx('span', { style: { color: accent, fontWeight: 700 }, children: c.to }),
            ],
          })),
        ]}) : null,

        // ── skills: grouped by the profile they land on ──
        byTarget.length ? jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: '3px' }, children: [
          jsx('span', { title: 'Each skill is copied to exactly the profile named here — not to the whole group.', style: { ...labelStyle, color: accent }, children: `Skills (${staged.length})` }),
          ...byTarget.map(b => jsxs('div', {
            key: b.target + b.op,
            style: { display: 'flex', alignItems: 'flex-start', gap: '5px', flexWrap: 'wrap' },
            children: [
              jsx('span', {
                title: b.op === 'remove'
                  ? `${b.skills.length} skill(s) will be REMOVED from ${b.target} — moved to its trash, restorable from the profile card.`
                  : `${b.skills.length} skill(s) will be copied into ${b.target}.`,
                style: { flex: '0 0 62px', fontSize: '9.5px', color: b.op === 'remove' ? red : sec },
                children: `${b.op === 'remove' ? '− ' : '+ '}${b.target}`,
              }),
              ...b.skills.map(rel => jsx('span', {
                key: rel,
                title: b.op === 'remove'
                  ? `${rel} will be moved out of ${b.target} into its trash. Click to cancel.`
                  : `${rel} will be copied into ${b.target}. Click to drop it from the queue.`,
                onClick: () => { haptic('tap'); setStaged(prev => prev.filter(x => !(x.skill === rel && x.profile === b.target && (x.op || 'add') === b.op))); setPreview(null) },
                style: { fontSize: '9px', padding: '1px 5px', borderRadius: '4px', border: '1px solid ' + stroke, color: b.op === 'remove' ? red : tert, cursor: 'pointer', display: 'inline-flex', gap: '3px' },
                children: rel.split('/').pop(),
              })),
            ],
          })),
        ]}) : null,

        // ── the layers that ride the same Apply ──
        layerWork.length ? jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: '1px' }, children: [
          jsx('span', { title: 'Where the anchor’s SOUL and any enabled layer are copied. This part is group-wide.', style: { ...labelStyle, color: sec }, children: `From the anchor (${policy.anchor || '—'})` }),
          ...layerWork.map(([k, v]) => jsxs('div', { key: k, style: { display: 'flex', alignItems: 'center', gap: '5px', fontSize: '9.5px' }, children: [
            jsx('span', { style: { flex: '0 0 62px', color: sec }, children: k }),
            jsx('span', { style: { color: faint }, children: v }),
          ]})),
        ]}) : null,

        // The staged DURABLE-FACT edits, listed like everything else. They were counted in
        // the badge but never shown here, so the panel said "3 queued" while displaying two
        // — the bar was not the honest answer it claimed to be.
        ...pendingBlocks.map(p => jsxs('div', {
          key: `blk-${p.layer}-${p.key}`,
          style: { display: 'flex', alignItems: 'center', gap: '5px', fontSize: '9.5px', minWidth: 0 },
          children: [
            jsx(Icon, { name: 'file', size: 10 }),
            jsx('span', { title: `${p.text == null ? 'Delete' : 'Edit'} this block in ${p.profile}'s ${p.layer} file`, style: { flex: '1 1 0', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: sec }, children: `${p.label} · ${p.profile}` }),
            jsx(InlineAction, { label: 'drop', onClick: () => setPendingBlocks(prev => prev.filter(x => !(x.layer === p.layer && x.key === p.key))), hint: 'Remove this fact edit from the queue. The file is untouched.' }),
          ],
        })),
        jsxs('div', { style: { display: 'flex', gap: '4px' }, children: [
          jsx('button', {
            // This button used to SAY "writes nothing at all" and CALL the real apply —
            // the worst possible pairing. It is now exactly what it says: a dry run.
            // The test-and-revert flow is Apply + Undo last (below), because Apply
            // records a trial too.
            onClick: runCheck, disabled: busy || pending === 0,
            title: 'PREVIEW — resolves every target and runs the SAME preflight Apply uses (per-skill source, path guard, secret scan), then reports what WOULD happen. WRITES NOTHING. To try it for real and undo it, press Apply then Undo last in Commits.',
            style: {
              flex: '1 1 0', fontSize: '9.5px', fontWeight: 600, height: '20px', padding: '0 4px', borderRadius: '5px',
              cursor: (busy || pending === 0) ? 'default' : 'pointer', opacity: (busy || pending === 0) ? 0.45 : 1,
              border: '1px solid ' + stroke, background: 'transparent', color: 'var(--ui-text-primary)',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '4px',
              userSelect: 'none', WebkitUserSelect: 'none',
            },
            children: [jsx(Icon, { name: 'play', size: 9 }), 'Preview'],
          }),
          jsx('button', {
            onClick: runApplyCommit, disabled: applyDisabled,
            title: !applyDisabled
              ? 'APPLY — commits the staged policy, then copies each staged skill and pushes the enabled layers, all through the same preflight Preview uses. Every write is RECORDED WITH ITS REVERSE, so Undo last (Activity ▸ Commits) puts the whole thing back. Apply IS the test.'
              : 'APPLY — nothing is staged and no layer is enabled, so there is nothing to do.',
            style: {
              flex: '1 1 0', fontSize: '9.5px', fontWeight: 700, height: '20px', padding: '0 4px', borderRadius: '5px',
              cursor: applyDisabled ? 'default' : 'pointer', opacity: applyDisabled ? 0.45 : 1,
              border: '1px solid ' + (applyFilled ? 'transparent' : stroke),
              background: applyFilled ? accent : 'transparent',
              color: applyFilled ? onAccent : faint,
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '4px',
              userSelect: 'none', WebkitUserSelect: 'none',
            },
            children: [jsx(Icon, { name: 'check', size: 9 }), 'Apply'],
          }),
        ]}),
      ]}) : null,
    ]
  })

  // keyframes injected once (the meeting panel's idiom)
  useEffect(() => {
    if (document.getElementById('profile-pane-css')) return
    const st = document.createElement('style')
    st.id = 'profile-pane-css'
    st.textContent = '@keyframes paneSpin { to { transform: rotate(360deg) } }'
    document.head.appendChild(st)
    return () => { const el = document.getElementById('profile-pane-css'); if (el) el.remove() }
  }, [])

  // the two boxes share the body; each owns its scroll; ⇅ swaps their order
  // The box stack. Collapsed while a layer detail is open: the boxes fall to their
  // headers and the POLICY CARD takes the whole pane — that is the point of the
  // collapsible windows. Closing the detail restores the stack exactly as it was.
  const body = jsx('div', {
    style: { flex: boxesCollapsed ? '0 0 auto' : '1 1 0', minHeight: 0, display: 'flex', flexDirection: 'column', gap: boxesCollapsed ? '3px' : '6px', padding: boxesCollapsed ? '4px 9px' : '7px 9px' },
    children: [libraryBox, profilesBox],
  })

  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', fontSize: '12px' },
    children: [stagedSection, policyCard, backupsSection, body,
      ghost ? jsx('div', {
        style: { position: 'fixed', left: ghost.x + 12, top: ghost.y + 10, pointerEvents: 'none', zIndex: 9999,
          padding: '2px 7px', borderRadius: '5px', fontSize: '10px', whiteSpace: 'nowrap',
          background: 'var(--ui-bg-chrome)', border: '1px solid ' + accent, color: accent, boxShadow: '0 4px 14px rgba(0,0,0,0.35)' },
        children: ghost.label,
      }) : null],
  })
}

export default {
  id: 'profile-pane',
  name: 'Profile Pane',
  description: 'Drag or click skills onto agents; sync SOUL / memory / user-profile across groups.',
  defaultEnabled: true,
  register(ctx) {
    rest.fn = ctx.rest
    os.fn = ctx.os || null
    const disposePane = ctx.register({
      id: 'pane', area: PANES_AREA, title: 'Profiles',
      render: () => jsx(Pane, {}),
      data: { placement: 'right', width: '360px' },
    })
    ctx.onDispose(() => { disposePane(); rest.fn = null; os.fn = null })
  },
}
