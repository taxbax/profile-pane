#!/usr/bin/env node
/**
 * Undefined-identifier audit for the pane's plugin.js.
 *
 * WHY THIS EXISTS: `node --check` only parses. It cannot see that `canApply` was
 * referenced but never defined — that is a ReferenceError at RUNTIME, on a code path
 * that may only fire when the user clicks. Its sibling is the arity bug
 * (`jsxs(type, props, key)` — the 3rd arg is the KEY, so children silently vanish).
 * Both are invisible to the syntax checker, and both present to the user as
 * "the button does nothing".
 *
 * The scanner is character-level on purpose: templates, regex literals, strings and
 * comments must be understood, or the regex bodies and template text get read as
 * code and drown the real finding in false positives.
 *
 * Run:  node tools/audit.mjs [path/to/plugin.js]
 */

import fs from 'node:fs'

const file = process.argv[2] || new URL('../desktop/plugin.js', import.meta.url).pathname
const src = fs.readFileSync(file, 'utf8')

// ── character scanner: emits code verbatim, blanks everything that is not code ────
function scanString(s, i, out) {
  const q = s[i]; out.push(q); i++
  while (i < s.length && s[i] !== q && s[i] !== '\n') {
    if (s[i] === '\\') { out.push('  '); i += 2; continue }
    out.push(' '); i++
  }
  if (s[i] === q) { out.push(q); i++ }
  return i
}

function scanTemplate(s, i, out) {
  out.push('`'); i++                      // s[i] === '`'
  let depth = 0
  while (i < s.length) {
    const c = s[i]
    if (c === '\\') { out.push('  '); i += 2; continue }
    if (depth === 0 && c === '`') { out.push('`'); return i + 1 }
    if (c === '$' && s[i + 1] === '{') { depth++; out.push('  '); i += 2; continue }
    if (depth > 0) {
      if (c === '}') { depth--; out.push(' '); i++; continue }
      if (c === '`') { i = scanTemplate(s, i, out); continue }        // nested template
      if (c === '"' || c === "'") { i = scanString(s, i, out); continue }
      out.push(c); i++; continue
    }
    out.push(c === '\n' ? '\n' : ' '); i++                            // literal text
  }
  return i
}

function scanRegex(s, i, out) {
  out.push('/'); i++
  let inClass = false
  while (i < s.length && s[i] !== '\n') {
    if (s[i] === '\\') { out.push('  '); i += 2; continue }
    if (s[i] === '[') inClass = true
    else if (s[i] === ']') inClass = false
    else if (s[i] === '/' && !inClass) { out.push('/'); i++; break }
    out.push(' '); i++
  }
  while (i < s.length && /[a-z]/i.test(s[i])) { out.push(' '); i++ }   // flags
  return i
}

function blankNonCode(s) {
  const out = []
  let i = 0
  const lastMeaningful = () => { for (let k = out.length - 1; k >= 0; k--) if (!/\s/.test(out[k])) return out[k]; return '' }
  while (i < s.length) {
    const c = s[i]
    if (c === '/' && s[i + 1] === '/') { while (i < s.length && s[i] !== '\n') { out.push(' '); i++ } continue }
    if (c === '/' && s[i + 1] === '*') {
      out.push('  '); i += 2
      while (i < s.length && !(s[i] === '*' && s[i + 1] === '/')) { out.push(s[i] === '\n' ? '\n' : ' '); i++ }
      out.push('  '); i += 2; continue
    }
    if (c === '`') { i = scanTemplate(s, i, out); continue }
    if (c === '"' || c === "'") { i = scanString(s, i, out); continue }
    if (c === '/') {
      const p = lastMeaningful()
      if ('=(,:![{;?&|+-*%<>'.includes(p) || out.join('').trim() === '') { i = scanRegex(s, i, out); continue }
    }
    out.push(c); i++
  }
  return out.join('')
}

const code = blankNonCode(src)
const lineOf = (i) => code.slice(0, i).split('\n').length

// ── declared names ───────────────────────────────────────────────────────────────
const declared = new Set()
const add = (n) => { if (/^[A-Za-z_$][\w$]*$/.test(n)) declared.add(n) }
// braces/brackets become separators FIRST, so a destructured param list splits cleanly
// (splitting on commas first leaves the tail fragment glued to its closing brace).
const collectPattern = (str) => {
  for (const part of str.replace(/[{}[\]]/g, ',').split(',')) {
    const p = part.trim()
    if (p) add(p.replace(/^\.\.\./, '').split('=')[0].split(':').pop().trim())
  }
}

for (const m of code.matchAll(/(?:^|[^\w$.])(?:const|let|var)\s+([A-Za-z_$][\w$]*)/g)) add(m[1])
for (const m of code.matchAll(/function\s*\*?\s*([A-Za-z_$][\w$]*)/g)) add(m[1])
for (const m of code.matchAll(/import\s+([\s\S]*?)\s+from/g)) {
  for (const part of m[1].replace(/[{}]/g, ',').split(',')) add(part.trim().split(/\s+as\s+/).pop().trim())
}
for (const m of code.matchAll(/(?:^|[^\w$.])(?:const|let|var)\s+([^\n;]*)/g)) collectPattern(m[1])
for (const m of code.matchAll(/function\s*[A-Za-z_$\w]*\s*\(([\s\S]*?)\)\s*\{/g)) collectPattern(m[1])
for (const m of code.matchAll(/\(([^()]*)\)\s*=>/g)) collectPattern(m[1])
for (const m of code.matchAll(/(?:^|[^\w$.])([A-Za-z_$][\w$]*)\s*=>/g)) add(m[1])
for (const m of code.matchAll(/catch\s*\(\s*([A-Za-z_$][\w$]*)/g)) add(m[1])

// ── object-method shorthand `name(...) {` is a KEY, not a used identifier ─────────
const methodKeys = new Set()
for (const m of code.matchAll(/([A-Za-z_$][\w$]*)\s*\([^()]*\)\s*\{/g)) {
  const before = code.slice(0, m.index).replace(/\s+$/, '')
  if ((before.endsWith('{') || before.endsWith(',') || before.endsWith(';') || before === '')
      && !declared.has(m[1])) methodKeys.add(m[1])
}

// ── object keys / labels `name:` are not uses either ─────────────────────────────
const asKey = code.replace(/([?.:])?\b[A-Za-z_$][\w$]*\s*:/g, (m, p) => (p ? m : ' '.repeat(m.length)))

const used = new Map()
for (const m of asKey.matchAll(/(?:^|[^\w$.])([A-Za-z_$][\w$]*)/g)) {
  const name = m[1]
  const at = m.index + m[0].length - name.length
  if (methodKeys.has(name)) continue
  if (!used.has(name)) used.set(name, lineOf(at))
}

const KNOWN = new Set([
  'true','false','null','undefined','NaN','Infinity','this','new','typeof','in','of','instanceof',
  'return','if','else','for','while','do','switch','case','default','break','continue','try','catch',
  'finally','throw','class','extends','super','function','const','let','var','import','export','from',
  'await','async','yield','delete','void','key','id','ref',
  'Math','JSON','Object','Array','String','Number','Boolean','Set','Map','WeakMap','Promise','Symbol',
  'Error','Date','RegExp','window','document','console','globalThis','fetch','setTimeout','clearTimeout',
  'setInterval','clearInterval','requestAnimationFrame','URL','URLSearchParams','Blob','FormData',
  'TextEncoder','structuredClone','queueMicrotask','AbortController','navigator','localStorage','Event',
  'CustomEvent','performance','crypto','atob','btoa','isNaN','parseInt','parseFloat','encodeURIComponent',
  'React','jsx','jsxs','Fragment','useState','useEffect','useRef','useMemo','useCallback','useReducer',
  'useQuery','useQueryClient','render','api','ctx','plugin','PANES_AREA','module','exports','require',
  'process','__dirname','arguments',
])

const unknown = [...used.entries()]
  .filter(([n]) => !declared.has(n) && !KNOWN.has(n))
  .sort((a, b) => a[1] - b[1])

// ── display-state invariants ──────────────────────────────────────────────────────
// Regressions of this shape are invisible to both other checks: the identifier scan sees
// two declared names (fine), and the Python suite never touches a render expression. They
// are caught only by asserting what a state signal MEANS.
//
// A state that says "there is work to do" must not be derived from a variable that means
// "the work is done". `lit = pending > 0 || trialLive` left the Activity bar green forever
// after every Apply: `pending` correctly fell to 0, but a live trial means the work is
// ALREADY DONE and undoable — the opposite of queued.
const INVARIANTS = [
  {
    name: 'the Activity bar lights for QUEUED work only',
    re: /const\s+lit\s*=\s*([^\n]+)/,
    forbid: /\btrialLive\b/,
    why: 'a live trial means the work is DONE — including it keeps the bar green after Apply',
    instead: 'the undo signal belongs to Tab("commits", trialLive) and the UNDO AVAILABLE row',
  },
]

const broken = []
for (const inv of INVARIANTS) {
  const m = code.match(inv.re)
  if (!m) { broken.push({ ...inv, found: '(expression not found)' }); continue }
  if (inv.forbid.test(m[1])) broken.push({ ...inv, found: m[1].trim() })
}

// ── chrome glyphs carry an explicit colour ────────────────────────────────────────
// A bare `jsx(Icon, ...)` inherits `currentColor` from whatever header wraps it. That is
// how the Sync Policy chevron rendered accent-green while its siblings — Backups and
// Skills, both wrapped in a muted style — rendered grey. Four glyphs, three colours, and
// nothing to catch it. Each of these must sit inside a `chromeIcon` wrapper.
const CHROME_GLYPHS = ['policyOpen', 'backupsOpen', 'libOpen', "name: 'search'"]
const codeLines = code.split('\n')
const srcLines = src.split('\n')          // the REAL text: `code` has strings/comments blanked,
                                          // so quoting a diagnostic from it shows spaces where the
                                          // identifiers were. Search the blanked copy, print the real one.
for (const glyph of CHROME_GLYPHS) {
  const at = codeLines.findIndex((l) => l.includes(glyph) && l.includes('jsx(Icon'))
  if (at === -1) continue                    // glyph not present; not a failure
  const wrapped = codeLines[at].includes('chromeIcon')
    || (at > 0 && codeLines[at - 1].includes('chromeIcon'))
  if (!wrapped) {
    broken.push({
      name: `chrome glyph \`${glyph}\` carries no explicit colour`,
      found: `line ${at + 1}: ${(srcLines[at] || '').trim().slice(0, 110)}`,
      why: 'a bare Icon inherits currentColor from its header and drifts to whatever colour that header is',
      instead: "wrap it: jsx('span', { style: chromeIcon, children: jsx(Icon, {...}) })",
    })
  }
}

// ── EVALUATION: does the module actually RUN? ─────────────────────────────────────
// `node --check` is NOT sufficient and gave a FALSE PASS on a fatal error: a `const`
// declaration spliced into a JSX children array is a SyntaxError that kills the pane, and
// --check reported ✅ both before and after the fix. `--check` does not evaluate a module
// with ESM imports; a component that throws at load never registers, so the pane simply
// VANISHES from the UI with no on-screen error.
//
// This stubs the module surface and evaluates the real body. It is the only check that
// catches "the pane is gone".
// AUTHORITATIVE check: copy to .mjs and let Node parse with the REAL ESM grammar.
// `node --check plugin.js` is NOT reliable here: a .js file with `import` statements is
// parsed as CommonJS, which bails before it can see a bracket error, and reports success.
// Renaming to .mjs forces the module grammar. This is the check that would have caught
// the missing `})` that deleted the pane.
import { copyFileSync, unlinkSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
const TMP = '/tmp/' + Math.random().toString(36).slice(2) + '.mjs'
let esmError = null
try {
  copyFileSync(file, TMP)
  const r = spawnSync(process.execPath, ['--check', TMP], { encoding: 'utf8' })
  if (r.status !== 0) esmError = (r.stderr || '').split('\n').slice(0, 6).join('\n')
} catch (e) { esmError = String(e) }
try { unlinkSync(TMP) } catch {}

const STUB = `
const React={createElement:()=>null,Fragment:'f'};
const useState=(v)=>[v,()=>{}]; const useEffect=()=>{}; const useRef=(v)=>({current:v});
const useMemo=(f)=>f(); const useCallback=(f)=>f; const useReducer=(v)=>[v,()=>{}];
const useQuery=()=>({data:null,isFetching:false,isError:false,error:null});
const useQueryClient=()=>({invalidateQueries:()=>{}});
const jsx=()=>null; const jsxs=()=>null; const PANES_AREA='a';
const plugin={register:()=>{}}; const api={}; const ctx={};
`
let evalError = null
try {
  const body = src
    .replace(/^\s*import\s+[\s\S]*?from\s+['"][^'"]+['"];?\s*$/gm, '')
    .replace(/^\s*import\s+['"][^'"]+['"];?\s*$/gm, '')
    .replace(/^\s*export\s+default\s+/gm, 'const __default = ')
    .replace(/^\s*export\s+/gm, '')
  new Function(STUB + '\n' + body)
} catch (e) {
  evalError = e
}

console.log(`file: ${file}`)
console.log(`declared: ${declared.size}   used: ${used.size}   method-keys skipped: ${methodKeys.size}`)
if (unknown.length === 0) {
  console.log('✅ no undefined identifiers')
} else {
  console.log(`🔴 ${unknown.length} identifier(s) used but never declared (ReferenceError at runtime):`)
  for (const [n, line] of unknown) console.log(`   line ${line}: ${n}`)
  process.exitCode = 1
}

if (esmError) {
  console.log('\u{1F534} ESM PARSE FAILED \u2014 the pane will not load:' + '\n' + esmError)
  process.exitCode = 1
} else {
  console.log('✅ ESM parse clean (module grammar)')
}

if (evalError) {
  console.log(`🔴 THE MODULE DOES NOT EVALUATE — the pane will not register: ${evalError.constructor.name}: ${evalError.message}`)
  process.exitCode = 1
} else {
  console.log('✅ module evaluates (the pane will register)')
}

if (broken.length === 0) {
  console.log(`✅ ${INVARIANTS.length} state-semantics invariant(s) + ${CHROME_GLYPHS.length} chrome-glyph check(s) hold`)
} else {
  console.log(`🔴 ${broken.length} display-state invariant(s) broken:`)
  for (const b of broken) console.log(`   ${b.name}\n     found:   ${b.found}\n     why not: ${b.why}\n     instead: ${b.instead}`)
  process.exitCode = 1
}
