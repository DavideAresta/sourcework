// The railway: where a run has got to, as a strip of stops.
//
// A run is a fixed sequence of stages (orchestrator/pipeline.py), and the log
// says so one line at a time - which is the right record and the wrong summary.
// Somebody glancing at a running PRD wants "it is analysing, ingest took 40s",
// and reading that out of forty lines of prose is work.
//
// Two rules the strip obeys:
//
//   1. It may lag; it may never lie. A message that matches no stage leaves the
//      strip exactly as it was, rather than guessing forward.
//   2. Nothing here is a second source of truth. Finished runs are drawn from
//      `stats.timings_s`, which the orchestrator measured; live runs from the
//      stage lines it already emits.

import { el, clear } from './dom.js';
import * as Icons from './icons.js';

// The stops, in pipeline order. `agent` is which mesh agent does the work, and
// picks the icon; `timings` are the `stats.timings_s` keys that belong to this
// stop, so a finished run can be drawn exactly rather than inferred.
const STOPS = [
  { key: 'discover', label: 'Discover', agent: 'orchestrator', timings: ['discovery', 'confluence_search'] },
  { key: 'ingest', label: 'Ingest', agent: 'ingestion', timings: ['ingest'] },
  { key: 'analyse', label: 'Analyse', agent: 'requirements', timings: ['analyse'] },
  { key: 'write', label: 'Write', agent: 'writer', timings: ['write'] },
  { key: 'review', label: 'Review', agent: 'critic', timings: ['review'] },
  { key: 'publish', label: 'Publish', agent: 'confluence', timings: ['publish'] },
  { key: 'done', label: 'Done', agent: 'flag', timings: [] },
];

// What the orchestrator says when it enters a stage. Matched against the
// progress line as sent; `pipeline.py` is the other half of this table, and
// tests/test_ui.py asserts the two still agree.
const ENTERS = [
  [/^Mesh online|^CQL /, 'discover'],
  [/^Ingesting|^Carrying forward|^Reusing \d+ evidence|^Skipping |evidence item\(s\) from /, 'ingest'],
  [/^Normalising requirements|^Reusing the requirements/, 'analyse'],
  [/^Drafting|^Revising/, 'write'],
  [/^Reviewing|^Reusing the review|^Review: /, 'review'],
  [/^Publishing to|^Publish failed/, 'publish'],
  [/^Finished/, 'done'],
];

// The orchestrator relays a specialist's lines under its own name for the role
// ("analyst"); the mesh, the icon set and the diagram all key on the agent name
// ("requirements"). One translation, exported, so the hero and the architecture
// view cannot disagree about who is working.
export const AGENT_ALIASES = { analyst: 'requirements', writer: 'writer', critic: 'critic' };

export function meshName(agent) {
  return AGENT_ALIASES[agent] ?? agent;
}

export function stopFor(message) {
  for (const [pattern, key] of ENTERS) if (pattern.test(message)) return key;
  return null;
}

function seconds(value) {
  if (value == null) return '';
  return value < 60 ? `${Math.round(value)}s` : `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`;
}

// A run's timings, folded onto the stops. Write and review are numbered per
// round (`write_0`, `review_1`), so each stop takes the sum of its rounds -
// three drafts are one Write that took as long as all three.
function timingFor(stop, timings) {
  let total = null;
  for (const [key, value] of Object.entries(timings ?? {})) {
    const base = key.replace(/_\d+$/, '');
    if (stop.timings.includes(base)) total = (total ?? 0) + value;
  }
  return total;
}

export function railway() {
  const node = el('div', { class: 'railway', role: 'group', 'aria-label': 'Run stages' });
  let reached = null;   // the furthest stop the run has entered
  let failed = false;

  // Told, not guessed: the caller passes each progress line through here.
  function sawMessage(message, kind) {
    if (kind === 'error') { failed = true; return true; }
    const key = stopFor(message ?? '');
    if (!key) return false;
    reached = key;
    return true;
  }

  function render(run) {
    const stats = run?.result?.stats ?? {};
    const timings = stats.timings_s ?? null;
    const finished = run?.status && run.status !== 'running' && run.status !== 'queued';
    // A finished run is drawn from what was measured, not from what was parsed.
    const done = new Set();
    if (timings) {
      for (const stop of STOPS) if (timingFor(stop, timings) != null) done.add(stop.key);
    }
    if (run?.status === 'ok') done.add('done');

    const captions = {
      discover: stats.sources != null ? `${stats.sources} source(s)` : '',
      ingest: stats.evidence != null ? `${stats.evidence} evidence` : '',
      analyse: stats.requirements != null ? `${stats.requirements} requirement(s)` : '',
      review: run?.result?.review?.verdict ?? '',
      publish: run?.result?.published_url ? 'published' : '',
    };

    // A run with timings has been measured; one without has only been narrated.
    const measured = timings != null && Object.keys(timings).length > 0;
    const reachedIndex = STOPS.findIndex((s) => s.key === reached);
    clear(node);
    STOPS.forEach((stop, index) => {
      // Two sources, and the measured one wins wherever it exists. Timings say
      // which stages actually ran; the parsed lines only say how far the log
      // got, and "everything before the furthest line" would tick Publish on
      // every run that never published - the strip inventing work, which is the
      // one thing it must not do.
      let state = 'pending';
      if (done.has(stop.key)) state = 'done';
      else if (measured) state = finished ? 'skipped' : 'pending';
      else if (index < reachedIndex) state = 'done';
      else if (index === reachedIndex) state = failed ? 'failed' : 'running';

      const took = timings ? timingFor(stop, timings) : null;
      const sub = took != null ? seconds(took)
        : state === 'skipped' ? 'not run'
          : (captions[stop.key] ?? '');

      node.append(
        el('div', { class: `rail-node ${state}` },
          el('div', {
            class: 'rail-dot',
            // The glyph replaces the icon at the two states where the outcome
            // matters more than the identity of whoever produced it.
            html: state === 'done' ? '✓'
              : state === 'failed' ? '✕'
                : state === 'skipped' ? '–'
                  : Icons.svg(stop.agent),
          }),
          el('div', { class: 'rail-label' }, stop.label),
          el('div', { class: 'rail-sub' }, sub || ' '),
        ),
      );
      if (index < STOPS.length - 1) node.append(el('div', { class: 'rail-link' }));
    });
  }

  return { node, sawMessage, render };
}

// The hero: the one line somebody watching a run actually wants.
//
// While the mesh works, the page's largest type says who is working and for how
// long. That was previously legible only by reading the tail of the log and
// doing the subtraction - and "is it stuck?" is the question a nine-minute
// analyst call provokes every time.
export function hero() {
  const agentName = el('div', { class: 'hero-agent' });
  const action = el('div', { class: 'hero-action' });
  const icon = el('div', { class: 'hero-icon' });
  const timerValue = el('div', { class: 'hero-timer-v' });
  const stats = el('div', { class: 'hero-stats' });

  const node = el('div', { class: 'card hero', hidden: true },
    icon,
    el('div', { class: 'hero-id' }, agentName, action),
    el('span', { class: 'grow' }),
    stats,
    el('div', { class: 'hero-timer' },
      timerValue,
      el('div', { class: 'hero-timer-l' }, 'elapsed')),
  );

  // Written straight into the node, never through a re-render: this changes
  // every second and the rest of the card does not.
  function tick(text) {
    timerValue.textContent = text;
  }

  function tile(label, value) {
    return el('div', { class: 'hero-stat' },
      el('div', { class: 'hero-stat-v' }, value),
      el('div', { class: 'hero-stat-l' }, label));
  }

  function update(run, { agent, message }) {
    const working = run?.status === 'running' || run?.status === 'queued' || run?.active;
    node.hidden = !working;
    if (!working) return;

    const iconKey = meshName(agent ?? '');
    const known = Icons.has(iconKey) ? iconKey : 'orchestrator';
    clear(icon).append(el('span', { class: 'ico-wrap', html: Icons.svg(known) }));
    agentName.textContent = agent ?? 'SourceWork';
    action.textContent = message ?? 'starting…';

    // Tiles appear as the numbers exist, and no sooner: a row of zeroes reads
    // as data that failed to load.
    const s = run?.result?.stats ?? {};
    clear(stats);
    if (s.sources) stats.append(tile('sources', s.sources));
    if (s.evidence) stats.append(tile('evidence', s.evidence));
    if (s.requirements) stats.append(tile('requirements', s.requirements));
  }

  return { node, update, tick };
}
