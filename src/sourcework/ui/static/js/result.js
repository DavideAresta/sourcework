// The run view: live progress while it works, then the PRD.
//
// The Requirements tab is the one that justifies the whole project — every
// requirement shown with the evidence that licenses it, and an explicit warning
// where there is none. That is the traceability claim, made checkable.

import { el, clear, mount, escape, ago, duration, toast } from './dom.js';
import { api } from './api.js';
import { renderMarkdown } from './markdown.js';
import * as notify from './notify.js';
import { refineTab } from './refine.js';
import { narrationPanel } from './narration.js';

const KIND_CLASS = { error: 'error', done: 'done', status: 'status' };

export function runView(runId, { onChanged }) {
  const root = el('div');
  const header = el('div');
  const logBox = el('div', { class: 'log' });
  // Open while it works, because then it is the only thing to look at. Folded
  // once it is over, because then the document is - and a 350px box of finished
  // steps was pushing the PRD itself below the fold. Folding is per status
  // change, so reopening it on a finished run stays reopened.
  const logSummary = el('summary', {}, 'Progress');
  const logCard = el('details', { class: 'card fold', open: true }, logSummary, logBox);
  let shownFor = null;
  const narration = narrationPanel();
  const body = el('div');
  let seen = new Set();

  root.append(header, logCard, narration.node, body);

  // A single step can run for ten minutes with nothing to say. Without a
  // ticking counter that is indistinguishable from a hung run, and the honest
  // answer - "this stage is genuinely this slow" - is the one thing the user
  // cannot tell from a static log.
  let ticker = null;
  const elapsed = el('span', { class: 'ts' });

  function startTicking(fromIso) {
    stopTicking();
    const since = new Date(fromIso).getTime();
    const tick = () => {
      const s = Math.max(0, Math.round((Date.now() - since) / 1000));
      elapsed.textContent = s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
    };
    tick();
    ticker = setInterval(tick, 1000);
  }

  function stopTicking() {
    if (ticker) clearInterval(ticker);
    ticker = null;
  }

  function line(event) {
    // Narration is ephemeral and carries no seq - it is the model working, not
    // a step of the run, and it belongs in its own panel rather than in the
    // progress log where it would bury the eight lines that matter.
    if (event.kind === 'stream') {
      narration.push(event);
      return;
    }
    if (seen.has(event.seq)) return;
    seen.add(event.seq);
    const time = event.t ? new Date(event.t).toLocaleTimeString() : '';
    const row = el('div', { class: `line ${KIND_CLASS[event.kind] ?? ''}` },
      el('span', { class: 'ts' }, time),
      el('span', {}, event.message),
    );
    logBox.append(row);
    if (event.kind === 'progress' || event.kind === 'status') {
      row.append(el('span', { style: 'flex:1' }), elapsed);
      startTicking(event.t);
    } else {
      stopTicking();
      elapsed.remove();
    }
    logBox.scrollTop = logBox.scrollHeight;
  }

  async function load() {
    let run;
    try {
      run = await api.getRun(runId);
    } catch (error) {
      clear(root).append(el('div', { class: 'empty' }, `Could not load that run: ${error.message}`));
      return;
    }

    renderHeader(run);
    foldLog(run);
    stopTicking();
    clear(logBox);
    // Not the narration: `load` runs again when the run finishes, and clearing
    // it there would wipe the reasoning the moment it became worth reading.
    seen = new Set();
    for (const event of run.events) line(event);
    renderBody(run);

    if (run.active || run.status === 'running' || run.status === 'queued') {
      await api.streamRun(runId, (event) => {
        line(event);
        if (event.kind === 'done' || event.kind === 'error') {
          narration.settle();
          setTimeout(load, 250);
        }
      });
      stopTicking();
      narration.settle();
      // Re-read before notifying: the stream resolves on a network drop too,
      // and the run row is the source of truth about how it ended.
      const finished = await api.getRun(runId).catch(() => null);
      if (finished) notify.runFinished(finished);
      onChanged?.();
    }
  }

  function foldLog(run) {
    if (run.status === shownFor) return;
    shownFor = run.status;
    const working = run.status === 'running' || run.status === 'queued';
    logCard.open = working;
    clear(logSummary).append(
      'Progress',
      working ? null : el('span', { class: 'muted small' }, ' — every step, if you want to check one'),
    );
  }

  function renderHeader(run) {
    const status = {
      ok: ['pill ok', 'finished'],
      failed: ['pill err', 'failed'],
      cancelled: ['pill warn', 'cancelled'],
      running: ['pill run', 'running'],
      queued: ['pill', 'queued'],
    }[run.status] ?? ['pill', run.status];

    const actions = el('div', { class: 'row' });
    if (run.status === 'running' || run.status === 'queued') {
      actions.append(el('button', {
        class: 'danger',
        onClick: async () => { await api.cancelRun(runId); toast('Cancelling…'); },
      }, 'Cancel'));
    }
    // Offered only when there is state to resume from, and never for a run that
    // already produced a document - that one wants refining, not repeating.
    if (run.resumable?.length && !run.result && !run.active) {
      actions.append(el('button', {
        class: 'primary',
        title: `Reuses ${run.resumable.join(', ')} and continues from there`,
        onClick: async () => {
          const { reusing } = await api.resumeRun(runId);
          toast(`Resuming, keeping ${reusing.length} finished stage(s)`);
          load();
        },
      }, 'Resume'));
    }
    if (run.result) {
      actions.append(
        el('a', { href: api.artifactUrl(runId, 'md') }, el('button', {}, 'Markdown')),
        el('a', { href: api.artifactUrl(runId, 'json') }, el('button', {}, 'JSON')),
        el('a', { href: api.artifactUrl(runId, 'xhtml') }, el('button', {}, 'Confluence XHTML')),
      );
      if (!run.result.published_url) {
        actions.append(el('button', { onClick: () => publish(run) }, 'Publish to Confluence'));
      }
    }
    // Pushed to the far end: it was one button away from "Publish to
    // Confluence", and those two should not be neighbours.
    actions.append(el('span', { style: 'flex:1' }));
    actions.append(el('button', {
      class: 'danger ghost',
      onClick: async () => {
        if (!confirm('Delete this run and its history?')) return;
        await api.deleteRun(runId);
        onChanged?.(null);
      },
    }, 'Delete'));

    mount(clear(header),
      el('h1', {}, run.title),
      el('div', { class: 'row', style: 'margin-bottom:14px' },
        el('span', { class: status[0] }, el('span', { class: `dot ${run.status === 'running' ? 'pulse' : ''}` }), status[1]),
        el('span', { class: 'pill' }, run.backend),
        run.parent_id
          ? el('a', { href: `#/run/${run.parent_id}`, class: 'pill', title: 'the version this refines' },
              '\u21b3 refines ' + run.parent_id.slice(0, 8))
          : null,
        el('span', { class: 'muted small' }, ago(run.created_at)),
        run.finished_at && el('span', { class: 'muted small' }, `· took ${duration(run.created_at, run.finished_at)}`),
        el('span', { style: 'flex:1' }),
        actions,
      ),
      run.error && el('div', { class: 'card', style: 'border-color:var(--err)' },
        el('b', {}, 'Failed. '), run.error),
      run.result?.published_url && el('div', { class: 'card' },
        'Published: ', el('a', { href: run.result.published_url, target: '_blank', rel: 'noopener' }, run.result.published_url)),
    );
  }

  async function publish(run) {
    try {
      const result = await api.publish(runId, {
        space_key: run.request.confluence_space_key,
        parent_id: run.request.confluence_parent_id,
      });
      toast(`Published: ${result.url}`, 'ok');
      load();
    } catch (error) {
      toast(error.message, 'err');
    }
  }

  function renderBody(run) {
    clear(body);
    if (!run.result) return;

    const prd = run.result.prd ?? {};
    const tabs = [
      ['PRD', () => documentTab(run)],
      ['Requirements', () => requirementsTab(prd)],
      ['Evidence', () => evidenceTab(prd)],
      ['Review', () => reviewTab(run.result.review)],
      ['Run', () => statsTab(run)],
      ['Refine', () => refineTab(run, (id) => { location.hash = `#/run/${id}`; })],
    ];

    const bar = el('div', { class: 'tabs' });
    const panel = el('div');
    tabs.forEach(([label, render], index) => {
      const button = el('button', { class: `tab ${index === 0 ? 'active' : ''}` }, label);
      button.addEventListener('click', () => {
        bar.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
        button.classList.add('active');
        clear(panel).append(render());
      });
      bar.append(button);
    });
    body.append(bar, panel);
    panel.append(tabs[0][1]());
  }

  // -- tabs --------------------------------------------------------------

  function documentTab(run) {
    return el('div', { class: 'doc', html: renderMarkdown(run.result.markdown ?? '') });
  }

  function requirementsTab(prd) {
    const requirements = prd.requirements?.requirements ?? [];
    if (!requirements.length) return el('div', { class: 'empty' }, 'No requirements.');

    const evidence = new Map((prd.evidence ?? []).map((e) => [e.id, e]));
    const sources = new Map((prd.sources ?? []).map((s) => [s.id, s]));
    const wrap = el('div');

    for (const req of requirements) {
      const refs = req.source_refs ?? [];
      const cites = refs.length
        ? refs.map((ref) => {
            const item = evidence.get(ref.evidence_id);
            const source = sources.get(ref.source_id);
            return el('span', { class: 'cite' },
              el('span', {}, `“${ref.quote ?? item?.text ?? '(evidence not in this document)'}”`),
              el('span', { class: 'loc' }, ` — ${source?.title ?? ref.source_id}${ref.locator ? ` @ ${ref.locator}` : ''}`),
            );
          })
        // The whole point of the citation contract: an uncited requirement is
        // visibly inferred, not quietly presented as sourced.
        : [el('span', { class: 'cite none' }, 'No evidence cited — inferred, not stated.')];

      wrap.append(
        el('div', { class: 'req' },
          el('div', { class: 'head' },
            el('span', { class: 'id' }, req.id),
            el('span', { class: 'pill' }, (req.priority ?? '').toUpperCase()),
            el('span', { class: 'pill' }, (req.kind ?? '').replaceAll('_', ' ')),
            el('span', { class: 'title' }, req.title),
          ),
          el('div', { class: 'statement' }, req.statement),
          req.acceptance_criteria?.length ? el('ul', { class: 'ac' }, ...req.acceptance_criteria.map((c) => el('li', {}, c))) : null,
          ...cites,
        ),
      );
    }
    return wrap;
  }

  function evidenceTab(prd) {
    const evidence = prd.evidence ?? [];
    if (!evidence.length) return el('div', { class: 'empty' }, 'No evidence.');
    const sources = new Map((prd.sources ?? []).map((s) => [s.id, s]));

    const rows = evidence.map((item) => el('tr', {},
      el('td', { class: 'mono' }, item.id),
      el('td', {}, sources.get(item.source_id)?.title ?? item.source_id),
      el('td', { class: 'mono' }, item.locator ?? ''),
      el('td', {}, el('span', { class: 'pill' }, item.kind)),
      el('td', {}, item.text),
      el('td', { class: 'mono' }, (item.confidence ?? 1).toFixed(2)),
    ));

    return el('div', { class: 'table-wrap' },
      el('table', {},
        el('thead', {}, el('tr', {},
          ...['ID', 'Source', 'Locator', 'Kind', 'Claim', 'Conf.'].map((h) => el('th', {}, h)))),
        el('tbody', {}, ...rows)));
  }

  function reviewTab(review) {
    if (!review) return el('div', { class: 'empty' }, 'No review was run.');
    const findings = review.findings ?? [];
    return el('div', {},
      el('div', { class: 'row', style: 'margin-bottom:14px' },
        el('span', { class: 'pill' }, review.verdict ?? 'reviewed'),
        el('span', { class: 'muted' }, `${findings.length} finding(s)`)),
      review.summary ? el('p', {}, review.summary) : null,
      findings.length > 0
        ? el('div', { class: 'table-wrap' }, el('table', {},
            el('thead', {}, el('tr', {}, ...['Severity', 'Category', 'Where', 'Detail', 'Suggested fix'].map((h) => el('th', {}, h)))),
            el('tbody', {}, ...findings.map((f) => el('tr', {},
              el('td', {}, el('span', { class: `pill ${f.severity === 'blocking' ? 'err' : 'warn'}` }, f.severity)),
              el('td', {}, f.category),
              el('td', { class: 'mono' }, f.location ?? ''),
              el('td', {}, f.detail),
              el('td', { class: 'muted' }, f.suggested_fix ?? ''))))))
        : el('div', { class: 'empty' }, 'Nothing flagged.'));
  }

  function statsTab(run) {
    const stats = run.result?.stats ?? {};
    const usage = run.usage ?? stats.usage ?? {};
    const backends = usage.backends ?? {};

    const tiles = el('div', { class: 'grid3' },
      ...[
        ['Sources', stats.sources],
        ['Evidence', stats.evidence],
        ['Requirements', stats.requirements],
        ['LLM calls', usage.calls],
      ].filter(([, v]) => v !== undefined && v !== null)
        .map(([k, v]) => el('div', { class: 'stat' }, el('div', { class: 'k' }, k), el('div', { class: 'v' }, v))));

    const costRows = Object.entries(backends).map(([name, row]) => el('tr', {},
      el('td', { class: 'mono' }, name),
      el('td', {}, row.calls),
      el('td', {}, (row.input_tokens ?? 0).toLocaleString()),
      el('td', {}, (row.output_tokens ?? 0).toLocaleString()),
      // Units are kept apart on purpose: provider dollars, Claude Code's
      // API-equivalent figure and converted Copilot credits are not the same
      // thing, and one total across them would be meaningless.
      el('td', { class: 'mono' }, Object.entries(row.cost ?? {})
        .map(([unit, value]) => `${Number(value).toFixed(4)} ${unit}`).join('  ') || '—')));

    const timings = Object.entries(stats.timings_s ?? {})
      .sort((a, b) => b[1] - a[1])
      .map(([stage, seconds]) => el('tr', {}, el('td', { class: 'mono' }, stage), el('td', {}, `${seconds}s`)));

    return el('div', {},
      tiles,
      costRows.length ? el('h3', {}, 'Spend') : null,
      costRows.length ? el('div', { class: 'table-wrap' }, el('table', {},
        el('thead', {}, el('tr', {}, ...['Backend', 'Calls', 'In', 'Out', 'Cost'].map((h) => el('th', {}, h)))),
        el('tbody', {}, ...costRows))) : null,
      timings.length ? el('h3', {}, 'Stage timings') : null,
      timings.length ? el('div', { class: 'table-wrap' }, el('table', {},
        el('thead', {}, el('tr', {}, el('th', {}, 'Stage'), el('th', {}, 'Duration'))),
        el('tbody', {}, ...timings))) : null,
      (stats.warnings ?? []).length ? el('h3', {}, `Warnings (${stats.warnings.length})`) : null,
      (stats.warnings ?? []).length ? el('ul', {}, ...stats.warnings.map((w) => el('li', { class: 'small muted' }, w))) : null,
      (stats.failures ?? []).length ? el('h3', {}, 'Failures') : null,
      (stats.failures ?? []).length ? el('ul', {}, ...stats.failures.map((f) => el('li', { class: 'small' }, f))) : null,
      el('h3', {}, 'Request'),
      el('pre', { class: 'raw', html: escape(JSON.stringify(run.request, null, 2)) }));
  }

  load();
  return root;
}
