// The dashboard: which PRDs can be handed over, and what is standing in the
// way of the rest.
//
// One row per PRD, not per run — a document refined twice is three runs and one
// deliverable. The row shows the newest finished version, because that is what
// somebody would actually open.

import { el, clear, mount, ago, toast } from './dom.js';
import { api } from './api.js';

const STATE = {
  ready: { label: 'Ready', cls: 'pill ok' },
  needs_work: { label: 'Needs work', cls: 'pill err' },
  unreviewed: { label: 'Not reviewed', cls: 'pill warn' },
  running: { label: 'In progress', cls: 'pill run' },
  failed: { label: 'Failed', cls: 'pill err' },
};

const KIND_LABEL = {
  review: 'Review finding',
  question: 'Blocking question',
  conflict: 'Unresolved conflict',
};

const root = document.getElementById('board');
const totalsBar = document.getElementById('totals');

function stateOf(row) {
  if (row.readiness) return row.readiness.state;
  if (row.in_flight) return 'running';
  return row.run.status === 'ok' ? 'unreviewed' : row.run.status;
}

function tile(label, value, cls) {
  return el('div', { class: 'stat' },
    el('div', { class: 'k' }, label),
    el('div', { class: `v ${cls ?? ''}` }, value));
}

function blockerList(readiness) {
  const wrap = el('div', { style: 'margin-top:10px' });
  // Grouped by kind: "three blocking questions" is a different conversation
  // from "three review findings", and they go to different people.
  for (const kind of ['review', 'question', 'conflict']) {
    const items = readiness.blockers.filter((b) => b.kind === kind);
    if (!items.length) continue;
    wrap.append(el('div', { class: 'small muted', style: 'margin:8px 0 4px' },
      `${KIND_LABEL[kind]}s (${items.length})`));
    for (const b of items) {
      wrap.append(el('div', { class: 'cite' },
        b.severity ? el('span', { class: 'pill err', style: 'margin-right:6px' }, b.severity) : null,
        el('span', {}, b.detail),
        b.location ? el('span', { class: 'loc' }, ` — ${b.location}`) : null));
    }
  }
  return wrap;
}

function card(row) {
  const state = stateOf(row);
  const meta = STATE[state] ?? { label: state, cls: 'pill' };
  const run = row.run;
  const r = row.readiness;

  const body = el('div', { class: 'card' });
  mount(body,
    el('div', { class: 'row', style: 'align-items:baseline' },
      el('span', { class: meta.cls },
        el('span', { class: `dot ${state === 'running' ? 'pulse' : ''}` }), meta.label),
      el('a', { href: `/#/run/${run.id}`, style: 'font-weight:600;font-size:15px' }, run.title),
      row.versions > 1
        ? el('span', { class: 'pill', title: 'refined versions of this PRD' }, `v${row.versions}`)
        : null,
      el('span', { style: 'flex:1' }),
      el('span', { class: 'muted small' }, ago(run.created_at)),
    ),

    el('div', { class: 'muted small', style: 'margin-top:6px' },
      r ? r.headline : run.error ? `Failed: ${run.error.slice(0, 160)}` : 'No result yet.'),

    row.in_flight && row.in_flight.id !== run.id
      ? el('div', { class: 'small', style: 'margin-top:6px' },
          'A newer version is ',
          el('a', { href: `/#/run/${row.in_flight.id}` }, 'still running'),
          ' — this is the last finished one.')
      : null,

    el('div', { class: 'row', style: 'margin-top:10px' },
      run.requirements ? el('span', { class: 'pill' }, `${run.requirements} requirements`) : null,
      run.evidence ? el('span', { class: 'pill' }, `${run.evidence} evidence`) : null,
      run.sources ? el('span', { class: 'pill' }, `${run.sources} sources`) : null,
      r && r.verdict ? el('span', { class: 'pill' }, `critic: ${r.verdict}`) : null,
      el('span', { style: 'flex:1' }),
      run.status === 'ok'
        ? el('a', { href: api.artifactUrl(run.id, 'md') }, el('button', {}, 'Markdown'))
        : null,
      run.status === 'ok' && r && !r.ready
        ? el('a', { href: `/#/run/${run.id}` }, el('button', { class: 'primary' }, 'Resolve →'))
        : null,
    ),
  );

  if (r && r.blockers.length) {
    const details = el('details', { class: 'adv', style: 'margin-top:10px' },
      el('summary', {}, `What is blocking it (${r.blockers.length})`));
    details.append(blockerList(r));
    body.append(details);
  }
  return body;
}

async function load() {
  clear(root).append(el('div', { class: 'empty' }, 'Loading…'));
  let data;
  try {
    data = await api.dashboard();
  } catch (error) {
    clear(root).append(el('div', { class: 'empty' }, `Could not load: ${error.message}`));
    return;
  }

  clear(totalsBar).append(
    tile('Ready', data.totals.ready ?? 0),
    tile('Needs work', data.totals.needs_work ?? 0),
    tile('Not reviewed', data.totals.unreviewed ?? 0),
    tile('In progress', data.totals.running ?? 0),
  );

  clear(root);
  if (!data.prds.length) {
    root.append(el('div', { class: 'empty' }, 'No PRDs yet.'));
    return;
  }

  // Needs-work first: the dashboard exists to surface what still needs doing,
  // so the documents that are finished sink to the bottom.
  const order = { needs_work: 0, unreviewed: 1, running: 2, failed: 3, ready: 4 };
  const rows = [...data.prds].sort((a, b) => (order[stateOf(a)] ?? 9) - (order[stateOf(b)] ?? 9));

  let current = null;
  for (const row of rows) {
    const state = stateOf(row);
    if (state !== current) {
      current = state;
      root.append(el('h2', {}, (STATE[state] ?? { label: state }).label));
    }
    root.append(card(row));
  }
}

document.getElementById('refresh').addEventListener('click', () => {
  load().then(() => toast('Refreshed', 'ok'));
});

load();
setInterval(load, 60_000);
