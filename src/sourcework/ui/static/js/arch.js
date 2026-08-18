// The mesh, drawn.
//
// SourceWork is eight agents talking A2A, and the UI said so with one pill
// reading `8/8`. That is enough to know the mesh is up and nothing at all about
// what it is: which agent reads what, who hands what to whom, where a run
// actually spends its minutes.
//
// So: the topology as a diagram, the health on top of it, and — while a run is
// going — the agent currently working, glowing, with the edges into it moving.
//
// The topology is hardcoded, deliberately. It is not configuration: it is
// `orchestrator/pipeline.py`'s sequence, and the day that changes is a day
// somebody edits this file too. What *is* live is health (`/api/mesh`), the
// active agent (a run's own event stream) and a finished run's timings.

import { el, clear, mount } from './dom.js';
import { api } from './api.js';
import * as Icons from './icons.js';
import { meshName } from './railway.js';

// Authored at this size and scaled to fit; hand-placed because nine nodes in a
// fixed pipeline do not need a layout engine, and a layout engine would move
// them every time it was asked.
const VIEW_W = 1180;
const VIEW_H = 560;

const LANES = [
  { x: 16, w: 440, label: '① DISCOVER · INGEST' },
  { x: 480, w: 250, label: '② ANALYSE' },
  { x: 754, w: 410, label: '③ WRITE · REVIEW · PUBLISH' },
];

// x/y/w/h are the card; `agent` is the name the mesh reports, which is also the
// icon key and the id the run stream names.
const NODES = [
  { id: 'orchestrator', x: 36, y: 248, w: 170, h: 64, title: 'Orchestrator', sub: 'runs the sequence',
    desc: 'Drives one run end to end: discovery, routing, ingest, analysis, drafting, review and publishing. It is the only agent that talks to all the others.',
    inp: 'PRDRequest', out: 'PRDResult' },
  { id: 'confluence', x: 236, y: 118, w: 200, h: 58, title: 'Confluence', sub: 'search · read · publish',
    desc: 'The only component holding Atlassian credentials. Searches by CQL, reads pages and attachments, and publishes the finished PRD idempotently.',
    inp: 'CQL query · page id', out: 'Evidence · published URL' },
  { id: 'ingestion', x: 236, y: 210, w: 200, h: 58, title: 'Ingestion', sub: 'documents → evidence',
    desc: 'Parses PDF, DOCX, PPTX, XLSX, CSV, HTML and Markdown into evidence, each item carrying the locator that lets a reader find it again.',
    inp: 'file:// or https:// input', out: 'Evidence[]' },
  { id: 'vision', x: 236, y: 294, w: 200, h: 58, title: 'Vision', sub: 'screenshots · diagrams',
    desc: 'Reads images — wireframes, screenshots, whiteboard photos — into evidence. Needs a vision-capable backend; without one the run says so rather than skipping quietly.',
    inp: 'image input', out: 'Evidence[]' },
  { id: 'transcript', x: 236, y: 378, w: 200, h: 58, title: 'Transcript', sub: 'meetings → evidence',
    desc: 'Parses VTT and SRT transcripts, keeping the speaker and the timestamp so a requirement can cite who said it and when.',
    inp: 'VTT · SRT', out: 'Evidence[] (with speaker)' },
  { id: 'requirements', x: 500, y: 248, w: 210, h: 64, title: 'Analyst', sub: 'evidence → requirements',
    desc: 'Clusters and de-duplicates evidence into requirements, assigns MoSCoW priority, records conflicts and open questions. Every citation it emits is validated in code; invented ids are dropped.',
    inp: 'Evidence[]', out: 'RequirementSet' },
  { id: 'writer', x: 774, y: 150, w: 180, h: 60, title: 'Writer', sub: 'requirements → PRD',
    desc: 'Writes the narrative and renders both artifacts — Markdown and Confluence storage XHTML. It cannot touch requirements: the analyst owns those.',
    inp: 'RequirementSet · Evidence[]', out: 'PRDDocument · markdown · xhtml' },
  { id: 'critic', x: 774, y: 350, w: 180, h: 60, title: 'Critic', sub: 'traceability · quality · review',
    desc: 'Runs the deterministic checks first — citations, quality rules (ISO/IEC/IEEE 29148, INCOSE) — then an adversarial model review. The verdict is computed, not asked for.',
    inp: 'PRDDocument', out: 'ReviewReport' },
  { id: 'publish', x: 990, y: 248, w: 160, h: 64, title: 'Published', sub: 'the PRD, delivered',
    desc: 'The finished document: downloaded as Markdown, JSON or Confluence XHTML, published to a Confluence space, or packed into an audit bundle with a digest per member.',
    inp: 'PRDDocument · ReviewReport', out: 'a page somebody can read' },
];

// `label` rides the edge as a pill; `loop` is the revision cycle, which is the
// one edge in the mesh that goes backwards.
const EDGES = [
  { from: 'orchestrator', to: 'confluence' },
  { from: 'orchestrator', to: 'ingestion' },
  { from: 'orchestrator', to: 'vision' },
  { from: 'orchestrator', to: 'transcript' },
  { from: 'confluence', to: 'requirements' },
  { from: 'ingestion', to: 'requirements', label: 'evidence' },
  { from: 'vision', to: 'requirements' },
  { from: 'transcript', to: 'requirements' },
  { from: 'requirements', to: 'writer', label: 'requirements' },
  { from: 'writer', to: 'critic', label: 'draft' },
  { from: 'critic', to: 'writer', label: 'revise', loop: true },
  { from: 'critic', to: 'publish', label: 'approved' },
];

const byId = (id) => NODES.find((n) => n.id === id);
const esc = (text) => String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// A polyline as a path, with the corners rounded. Square corners on an
// orthogonal run read as a mistake; a fillet reads as routing.
function round(points, r = 10) {
  if (points.length < 3) return `M${points.map((p) => `${p.x},${p.y}`).join(' L')}`;
  let d = `M${points[0].x},${points[0].y}`;
  for (let i = 1; i < points.length - 1; i += 1) {
    const [prev, here, next] = [points[i - 1], points[i], points[i + 1]];
    const inLen = Math.hypot(here.x - prev.x, here.y - prev.y);
    const outLen = Math.hypot(next.x - here.x, next.y - here.y);
    const ri = Math.min(r, inLen / 2, outLen / 2);
    const a = { x: here.x + (prev.x - here.x) * (ri / (inLen || 1)), y: here.y + (prev.y - here.y) * (ri / (inLen || 1)) };
    const b = { x: here.x + (next.x - here.x) * (ri / (outLen || 1)), y: here.y + (next.y - here.y) * (ri / (outLen || 1)) };
    d += ` L${a.x},${a.y} Q${here.x},${here.y} ${b.x},${b.y}`;
  }
  const last = points[points.length - 1];
  return `${d} L${last.x},${last.y}`;
}

// Left edge to left edge, elbowed through the midpoint. Enough for a pipeline
// that flows one way; the loop edge is the exception and bows instead.
function routeFor(edge) {
  const a = byId(edge.from);
  const b = byId(edge.to);
  const start = { x: a.x + a.w, y: a.y + a.h / 2 };
  const end = { x: b.x, y: b.y + b.h / 2 };
  if (edge.loop) {
    // Bowed under the pair rather than orthogonal: a right-angled arrow going
    // backwards through the same channel as the forward one reads as a wire
    // crossing, not as a cycle.
    // Bowed out to the left of the pair rather than orthogonal: a right-angled
    // arrow going back up the same channel as the forward one reads as a wire
    // crossing, not as a cycle.
    const bow = 58;
    const x = Math.min(a.x, b.x);
    const topY = Math.min(a.y, b.y);
    const bottomY = Math.max(a.y + a.h, b.y + b.h);
    // `a` is the critic (below), `b` the writer (above): up the left side.
    return {
      d: `M${a.x + 20},${a.y} Q${x - bow},${(topY + bottomY) / 2} ${b.x + 20},${b.y + b.h}`,
      mid: { x: x - bow / 2 - 4, y: (topY + bottomY) / 2 },
    };
  }
  // Stacked in the same column - writer above critic - so the edge goes down,
  // not out and back. Routing those two left-to-right sent the arrow backwards
  // across the whole lane to re-enter from the left.
  const sameColumn = Math.abs((a.x + a.w / 2) - (b.x + b.w / 2)) < (a.w + b.w) / 4;
  if (sameColumn) {
    const down = b.y > a.y;
    const from = { x: a.x + a.w / 2, y: down ? a.y + a.h : a.y };
    const to = { x: b.x + b.w / 2, y: down ? b.y : b.y + b.h };
    return { d: round([from, to]), mid: { x: from.x, y: (from.y + to.y) / 2 } };
  }

  const midX = (start.x + end.x) / 2;
  const points = start.y === end.y
    ? [start, end]
    : [start, { x: midX, y: start.y }, { x: midX, y: end.y }, end];
  return { d: round(points), mid: { x: midX, y: (start.y + end.y) / 2 } };
}

function nodeMarkup(node) {
  const cx = node.x + node.w / 2;
  const cy = node.y + node.h / 2;
  // Icon and title centred as one unit with a fixed gap, so a wrong text-width
  // estimate can only decentre the pair slightly - never overlap it.
  const unit = node.title.length * 8.5 + 26;
  const startX = cx - unit / 2;
  return `<g class="arch-node" id="arch-${node.id}" tabindex="0" role="button"
    aria-label="${esc(node.title)}: ${esc(node.sub)}">
    <rect class="n-card" x="${node.x}" y="${node.y}" width="${node.w}" height="${node.h}" rx="12"/>
    <rect class="n-glaze" x="${node.x}" y="${node.y}" width="${node.w}" height="${node.h}" rx="12"/>
    <rect class="n-accent" x="${node.x + 1}" y="${node.y + 10}" width="4" height="${node.h - 20}" rx="2"/>
    ${Icons.nested(node.id, startX, cy - 19, 18)}
    <text class="n-title" x="${startX + 26}" y="${cy - 4}">${esc(node.title)}</text>
    <text class="n-sub" x="${cx}" y="${cy + 16}" text-anchor="middle">${esc(node.sub)}</text>
  </g>`;
}

function diagram() {
  let svg = `<svg viewBox="0 0 ${VIEW_W} ${VIEW_H}" xmlns="http://www.w3.org/2000/svg" role="img"
    aria-label="The SourceWork agent mesh">
    <defs>
      <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
        orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z"/></marker>
      <pattern id="dotgrid" width="26" height="26" patternUnits="userSpaceOnUse">
        <circle cx="1.3" cy="1.3" r="1.1"/></pattern>
    </defs>
    <rect class="arch-bg" width="${VIEW_W}" height="${VIEW_H}" fill="url(#dotgrid)"/>`;

  for (const lane of LANES) {
    svg += `<g class="arch-lane">
      <rect x="${lane.x}" y="60" width="${lane.w}" height="${VIEW_H - 100}" rx="14"/>
      <circle cx="${lane.x + 22}" cy="86" r="4"/>
      <text x="${lane.x + 36}" y="90">${esc(lane.label)}</text>
    </g>`;
  }

  for (const edge of EDGES) {
    const { d, mid } = routeFor(edge);
    svg += `<path class="arch-edge${edge.loop ? ' loop' : ''}" id="edge-${edge.from}--${edge.to}"
      d="${d}" marker-end="url(#arr)"/>`;
    if (edge.label) {
      const w = edge.label.length * 6.4 + 14;
      svg += `<g class="arch-pill"><rect x="${mid.x - w / 2}" y="${mid.y - 9}" width="${w}" height="18" rx="9"/>
        <text x="${mid.x}" y="${mid.y + 4}" text-anchor="middle">${esc(edge.label)}</text></g>`;
    }
  }

  for (const node of NODES) svg += nodeMarkup(node);
  return `${svg}</svg>`;
}

// ---------------------------------------------------------------------------
// The page
// ---------------------------------------------------------------------------

const canvas = document.getElementById('arch-canvas');
const panel = document.getElementById('arch-panel');
const view = document.getElementById('arch-view');
const collapse = document.getElementById('arch-collapse');

let selected = null;
let mesh = null;
let glowing = null;

function defaultPanel() {
  mount(clear(panel),
    el('h2', {}, 'How it works'),
    el('p', {},
      'Eight agents on one A2A mesh. Documents, transcripts, images and Confluence pages '
      + 'become evidence; evidence becomes requirements; requirements become a PRD that a '
      + 'reader can audit line by line.'),
    el('p', { class: 'arch-hint' },
      'Click any node for what it does. While a run is going, the working agent glows and '
      + 'the edges into it move.'),
    meshPanel(),
  );
}

function meshPanel() {
  if (!mesh) return el('p', { class: 'arch-hint' }, 'Checking which agents answer…');
  const up = Object.keys(mesh.agents ?? {}).length;
  const total = Object.keys(mesh.registry ?? {}).length;
  const down = mesh.unreachable ?? [];
  return el('div', {},
    el('div', { class: 'io' },
      el('b', {}, 'mesh'),
      el('code', {}, `${up}/${total} answering`)),
    down.length
      ? el('p', { class: 'arch-hint ink-err' }, `Not answering: ${down.join(', ')}`)
      : null,
  );
}

function selectNode(id) {
  const node = byId(id);
  if (!node) return;
  if (selected) document.getElementById(`arch-${selected}`)?.classList.remove('selected');
  selected = id;
  document.getElementById(`arch-${id}`)?.classList.add('selected');

  const skills = mesh?.agents?.[id] ?? null;
  const url = mesh?.registry?.[id] ?? null;
  mount(clear(panel),
    el('h2', {}, node.title),
    el('div', { class: 'role' }, node.sub),
    el('p', {}, node.desc),
    node.inp ? el('div', { class: 'io' }, el('b', {}, 'consumes'), el('code', {}, node.inp)) : null,
    node.out ? el('div', { class: 'io' }, el('b', {}, 'produces'), el('code', {}, node.out)) : null,
    // The skills are what the agent card actually advertises, read live - so a
    // skill added to an agent shows up here without this file being touched.
    skills?.length
      ? el('div', { class: 'io' }, el('b', {}, 'skills'), el('code', {}, skills.join(', ')))
      : null,
    url
      ? el('p', { class: 'arch-hint' },
          el('a', { href: `${url.replace(/\/$/, '')}/docs`, target: '_blank', rel: 'noopener' },
            'Open its API docs ↗'))
      : null,
    mesh && !skills && byId(id).id !== 'publish'
      ? el('p', { class: 'arch-hint ink-err' }, 'This agent is not answering right now.')
      : null,
  );
}

function paintHealth() {
  if (!mesh) return;
  const down = new Set(mesh.unreachable ?? []);
  for (const node of NODES) {
    const element = document.getElementById(`arch-${node.id}`);
    if (element) element.classList.toggle('down', down.has(node.id));
  }
}

// The active agent, from the same source the run view uses: the orchestrator
// tags a specialist's lines `[analyst]`, and its own stage lines name the stage.
const ACTIVE = [
  [/^\[?analyst\]?|^Normalising requirements/, 'requirements'],
  [/^\[?writer\]?|^Drafting|^Revising/, 'writer'],
  [/^\[?critic\]?|^Reviewing|^Review: /, 'critic'],
  [/^Ingesting|evidence item\(s\) from /, 'ingestion'],
  [/^CQL |^Publishing to/, 'confluence'],
  [/^Mesh online|^Finished/, 'orchestrator'],
];

function agentFor(message) {
  for (const [pattern, id] of ACTIVE) if (pattern.test(message ?? '')) return id;
  return null;
}

function setGlow(id) {
  if (id === glowing) return;
  if (glowing) {
    document.getElementById(`arch-${glowing}`)?.classList.remove('glow');
    document.querySelectorAll('.arch-edge.flow').forEach((p) => p.classList.remove('flow'));
  }
  glowing = id;
  if (!id) return;
  document.getElementById(`arch-${id}`)?.classList.add('glow');
  // Every edge that touches the working agent, so the diagram shows where the
  // data is moving rather than only where it stopped.
  for (const edge of EDGES) {
    if (edge.from !== id && edge.to !== id) continue;
    document.getElementById(`edge-${edge.from}--${edge.to}`)?.classList.add('flow');
  }
}

async function followLiveRun() {
  let runs = [];
  try {
    runs = await api.listRuns();
  } catch {
    return;   // the diagram is worth having with no history at all
  }
  const active = runs.find((r) => r.status === 'running' || r.status === 'queued');
  if (!active) { setGlow(null); return; }

  panel.dataset.run = active.id;
  await api.streamRun(active.id, (event) => {
    if (event.kind === 'stream') { setGlow(event.agent ? meshName(event.agent) : glowing); return; }
    const id = agentFor(event.message);
    if (id) setGlow(id);
    if (event.kind === 'done' || event.kind === 'error') setGlow(null);
  });
  setGlow(null);
  followLiveRun();   // a finished run is followed by whatever runs next
}

// The header pill is shared markup with the other pages, but `app.js` - which
// normally fills it - is the runs page's module. Left alone it reads "checking…"
// for as long as the page is open, which is a worse answer than none.
function paintMeshPill() {
  const pill = document.getElementById('mesh');
  if (!pill) return;
  if (!mesh) { pill.className = 'pill err'; pill.textContent = 'mesh down'; return; }
  const up = Object.keys(mesh.agents ?? {}).length;
  const total = Object.keys(mesh.registry ?? {}).length;
  pill.className = `pill ${up === total ? 'ok' : 'err'}`;
  pill.textContent = `mesh ${up}/${total}`;
}

function refreshMesh() {
  return api.mesh().then((data) => {
    mesh = data;
    paintHealth();
    paintMeshPill();
    if (selected) selectNode(selected); else defaultPanel();
  }).catch(() => {
    // Health is an overlay - the topology stands without it - but the pill has
    // to stop claiming it is still checking.
    mesh = null;
    paintMeshPill();
  });
}

function boot() {
  canvas.innerHTML = diagram();
  for (const node of NODES) {
    const element = document.getElementById(`arch-${node.id}`);
    element?.addEventListener('click', () => selectNode(node.id));
    element?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectNode(node.id); }
    });
  }
  defaultPanel();

  refreshMesh();

  api.health().then((h) => {
    const version = document.getElementById('version');
    if (version && h.version) version.textContent = h.version;
  }).catch(() => {});

  followLiveRun();
  setInterval(refreshMesh, 30_000);
}

// Collapsing hides the panel and gives its 380px to the diagram; remembered,
// because somebody who wants the wide diagram wants it every time.
collapse.addEventListener('click', () => {
  const wide = view.classList.toggle('compact');
  try { localStorage.setItem('sourcework.archCompact', wide ? '1' : '0'); } catch { /* private mode */ }
});
try {
  if (localStorage.getItem('sourcework.archCompact') === '1') view.classList.add('compact');
} catch { /* private mode */ }

boot();
