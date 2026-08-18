// Shell: sidebar history, mesh indicator, and a two-state main panel.
// Routing is the URL hash, so a run is linkable and reload keeps your place.

import { el, clear, ago, toast } from './dom.js';
import { api } from './api.js';
import * as drafts from './drafts.js';
import { newRunView } from './newrun.js';
import { runView } from './result.js';

const main = document.getElementById('main');
const sidebar = document.getElementById('runs');
const meshPill = document.getElementById('mesh');

const STATUS_PILL = {
  ok: 'pill ok', failed: 'pill err', cancelled: 'pill warn',
  running: 'pill run', queued: 'pill',
};

async function refreshSidebar() {
  let runs = [];
  try {
    runs = await api.listRuns();
  } catch (error) {
    clear(sidebar).append(el('div', { class: 'small muted' }, `History unavailable: ${error.message}`));
    return;
  }

  const current = location.hash.replace('#/run/', '');
  clear(sidebar);
  if (!runs.length) {
    sidebar.append(el('div', { class: 'small muted', style: 'padding:6px 8px' }, 'No runs yet.'));
    return;
  }

  for (const run of runs) {
    sidebar.append(
      el('button', {
        class: `run-item ${run.id === current ? 'active' : ''}`,
        onClick: () => { location.hash = `#/run/${run.id}`; },
      },
        el('div', { class: 't' }, run.title),
        el('div', { class: 'm' },
          el('span', { class: STATUS_PILL[run.status] ?? 'pill' },
            el('span', { class: `dot ${run.status === 'running' ? 'pulse' : ''}` }), run.status),
          el('span', {}, ago(run.created_at)),
          run.requirements !== null && run.requirements !== undefined
            ? el('span', {}, `· ${run.requirements} reqs`) : null,
          run.parent_id ? el('span', { title: 'refines an earlier version' }, '· \u21b3') : null,
          run.approval === 'approved'
            ? el('span', { title: 'signed off' }, '· \u2713 approved')
            : run.approval === 'rejected'
              ? el('span', { title: 'sent back' }, '· \u2717 rejected')
              : null,
          // A draft is invisible otherwise: you'd have to open the run and
          // click through to Refine to discover you left work there.
          !drafts.isEmpty(drafts.load(run.id))
            ? el('span', { title: 'unsent draft in Refine' }, '· \u270e draft') : null,
        ),
      ),
    );
  }
}

// The Quit control, drawn only when the server says it has somewhere to send it.
// Running in a checkout or under compose there is no shutdown endpoint, and a
// button that 404s is worse than no button.
async function mountQuit() {
  const header = document.querySelector('header.top');
  if (!header || document.getElementById('quit')) return;
  let health;
  try {
    health = await api.health();
  } catch {
    return;
  }
  const version = document.getElementById('version');
  if (version && health.version) version.textContent = health.version;
  if (!health.shutdown) return;

  const button = el('button', {
    id: 'quit',
    class: 'ghost',
    title: 'Stop SourceWork. Anything still running is lost.',
    onClick: async () => {
      if (!confirm('Quit SourceWork? A run in progress will be lost.')) return;
      try {
        await api.shutdown();
      } catch { /* the socket closing *is* the success case */ }
      document.body.innerHTML =
        '<p style="padding:2rem;font:14px system-ui">SourceWork has stopped. '
        + 'You can close this tab.</p>';
    },
  }, 'Quit');
  header.append(button);
}

async function refreshMesh() {
  try {
    const mesh = await api.mesh();
    const up = Object.keys(mesh.agents).length;
    const total = Object.keys(mesh.registry).length;
    const healthy = up === total;
    clear(meshPill);
    meshPill.className = `pill ${healthy ? 'ok' : 'err'}`;
    meshPill.append(el('span', { class: 'dot' }), `mesh ${up}/${total}`);
    meshPill.title = healthy
      ? Object.keys(mesh.agents).sort().join(', ')
      : `unreachable: ${mesh.unreachable.join(', ')}`;
  } catch {
    clear(meshPill);
    meshPill.className = 'pill err';
    meshPill.append(el('span', { class: 'dot' }), 'mesh down');
    meshPill.title = 'The UI cannot reach the orchestrator. Is the mesh running?';
  }
}

function render() {
  const hash = location.hash;
  const match = /^#\/run\/([a-z0-9]+)$/.exec(hash);

  if (match) {
    clear(main).append(runView(match[1], {
      onChanged: (goHome) => {
        refreshSidebar();
        if (goHome === null) location.hash = '#/new';
      },
    }));
  } else {
    clear(main).append(newRunView((id) => {
      location.hash = `#/run/${id}`;
      refreshSidebar();
    }));
  }
  refreshSidebar();
}

window.addEventListener('hashchange', render);

document.getElementById('new-run').addEventListener('click', () => {
  if (location.hash === '#/new') render();
  else location.hash = '#/new';
});

render();
refreshMesh();
mountQuit();
setInterval(refreshMesh, 30_000);
// Cheap and good enough: a run started in another tab shows up within a minute.
setInterval(refreshSidebar, 60_000);

window.addEventListener('unhandledrejection', (event) => {
  toast(`Unexpected error: ${event.reason?.message ?? event.reason}`, 'err');
});
