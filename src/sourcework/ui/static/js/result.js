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
import { railway, hero } from './railway.js';

const KIND_CLASS = { error: 'error', done: 'done', status: 'status' };

// readiness.py's three states, as the dashboard already colours them.
const READY_PILL = { ready: 'pill ok', needs_work: 'pill err', unreviewed: 'pill warn' };

// A button that cannot be pressed twice.
//
// Every action in this header hands work to an agent or rewrites stored
// artifacts, and none of them was disabled while it ran: publishing twice
// because nothing acknowledged the first click is a second Confluence page,
// and deleting twice is a 404 toast over a run that did go. The label says
// what is happening rather than freezing, because a frozen label reads as a
// hung page.
function button(label, { class: cls = '', busy = 'Working…', title = '' }, run) {
  const node = el('button', { class: cls, title }, label);
  node.addEventListener('click', async () => {
    if (node.disabled) return;
    node.disabled = true;
    node.textContent = busy;
    try {
      await run();
    } catch (error) {
      toast(error.message, 'err');
    } finally {
      // The view usually re-renders over this node; when it does not - a
      // cancelled confirm, a failure - the button has to come back.
      node.disabled = false;
      node.textContent = label;
    }
  });
  return node;
}

// Tab labels are prose; the URL wants a token. One direction only - the label
// stays the thing that gets edited.
const slug = (label) => label.toLowerCase();

export function runView(runId, { onChanged }) {
  // Read once from the URL, then kept across re-renders. `#/run/<id>/review`
  // reloads onto the review, and an empty or unknown segment falls back to the
  // first tab rather than to nothing.
  let activeTab = (location.hash.split('/')[3] ?? '').toLowerCase();
  const root = el('div');
  const header = el('div');
  // A run is minutes of nothing but this box changing. Without a live region a
  // screen reader is told nothing at all between "Generate PRD" and the
  // document appearing. `polite` because the lines are frequent and none of
  // them is urgent enough to interrupt what is being read.
  const logBox = el('div', {
    class: 'log', role: 'log', 'aria-live': 'polite', 'aria-label': 'Run progress',
  });
  // Open while it works, because then it is the only thing to look at. Folded
  // once it is over, because then the document is - and a 350px box of finished
  // steps was pushing the PRD itself below the fold. Folding is per status
  // change, so reopening it on a finished run stays reopened.
  const logSummary = el('summary', {}, 'Progress');
  const logCard = el('details', { class: 'card fold', open: true }, logSummary, logBox);
  let shownFor = null;
  const narration = narrationPanel();
  // Where the sign-off form appears. The same node is re-attached on every
  // header render rather than rebuilt, so a background reload - a run
  // finishing, a publish returning - cannot close a form somebody is halfway
  // through typing into.
  const signOffSlot = el('div');
  const body = el('div');
  const rail = railway();
  const live = hero();
  let seen = new Set();
  let lastRun = null;
  let lastMinute = null;
  // True only while `load` is pushing the stored events through `line`. Those
  // are history: they must fill the log and advance the rail, but they must not
  // be mistaken for the run changing state under us.
  let replaying = false;

  // The rail leads: it is the only thing on the page that answers "how far in
  // is this" without reading anything.
  root.append(rail.node, live.node, header, logCard, narration.node, body);

  // A single step can run for ten minutes with nothing to say. Without a
  // ticking counter that is indistinguishable from a hung run, and the honest
  // answer - "this stage is genuinely this slow" - is the one thing the user
  // cannot tell from a static log.
  let ticker = null;

  function startTicking(fromIso) {
    stopTicking();
    const since = new Date(fromIso).getTime();
    const tick = () => {
      const s = Math.max(0, Math.round((Date.now() - since) / 1000));
      live.tick(s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`);
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
    // Every event carries the status it was emitted at. Without reading it here
    // the header, the rail and the hero all keep rendering the status the page
    // was *loaded* with — `lastRun` was fetched once and never touched again —
    // so a run opened while it was `queued` displayed "queued" for its whole
    // life, pulsing dot and all, while the log beside it scrolled. Replay is
    // excluded because `load` has already drawn the run it just fetched, and
    // replaying forty events would redraw the header for each one.
    if (!replaying && event.status && event.status !== lastRun?.status) {
      lastRun = { ...lastRun, status: event.status };
      renderHeader(lastRun);
      foldLog(lastRun);
    }
    const nearBottom = logBox.scrollHeight - logBox.scrollTop - logBox.clientHeight < 40;
    // The rail is told, never told twice: `seen` already guards the replay.
    if (rail.sawMessage(event.message, event.kind)) rail.render(lastRun);
    // One header per minute instead of a timestamp on every row. Forty lines
    // each carrying 12:19:24 is forty repetitions of the same four digits; what
    // a reader actually wants from a log is where the time jumped.
    const stamp = event.t ? new Date(event.t).toLocaleTimeString() : '';
    const minute = stamp.slice(0, 5);
    if (minute && minute !== lastMinute) {
      lastMinute = minute;
      logBox.append(el('div', { class: 'event-minute' }, minute));
    }

    // The orchestrator tags a specialist's own lines `[analyst] …`; lift the
    // tag out of the prose so it can be a chip and the message can be the
    // message.
    const tagged = /^\[([a-z-]+)\]\s*/.exec(event.message ?? '');
    const text = tagged ? event.message.slice(tagged[0].length) : (event.message ?? '');

    const row = el('div', {
      class: `line ${KIND_CLASS[event.kind] ?? ''}`, title: stamp,
    },
      el('span', { class: 'event-dot' }),
      tagged ? el('span', { class: 'event-agent' }, tagged[1]) : null,
      el('span', { class: 'event-text' }, text),
    );
    logBox.append(row);
    if (event.kind === 'progress' || event.kind === 'status') {
      // The clock belongs to the stage, so it restarts with each one - and it
      // lives in the hero now rather than chasing the newest row down the log.
      live.update(lastRun, { agent: tagged?.[1], message: text });
      startTicking(event.t);
    } else {
      stopTicking();
    }
    // Only follow the tail if the reader is already at it. Scrolling back to
    // read a line and being yanked forward a second later is the log fighting
    // the person reading it.
    if (nearBottom) logBox.scrollTop = logBox.scrollHeight;
  }

  async function load() {
    let run;
    try {
      run = await api.getRun(runId);
    } catch (error) {
      clear(root).append(el('div', { class: 'empty' }, `Could not load that run: ${error.message}`));
      return;
    }

    lastRun = run;
    renderHeader(run);
    foldLog(run);
    rail.render(run);
    live.update(run, { agent: null, message: 'starting…' });
    stopTicking();
    clear(logBox);
    // Not the narration: `load` runs again when the run finishes, and clearing
    // it there would wipe the reasoning the moment it became worth reading.
    seen = new Set();
    lastMinute = null;
    replaying = true;
    for (const event of run.events) line(event);
    replaying = false;
    renderBody(run);

    if (run.active || run.status === 'running' || run.status === 'queued') {
      await api.streamRun(runId, (event) => {
        line(event);
        if (event.kind === 'done' || event.kind === 'error') {
          narration.settle();
          setTimeout(load, 250);
        }
      }, {
        // The connection dropped and is being retried. The run row is the
        // source of truth about what happened while we were not listening, so
        // the header is redrawn from it rather than from the last event we
        // happened to receive before the wire went.
        onState: (fresh) => {
          lastRun = fresh;
          renderHeader(fresh);
          foldLog(fresh);
          rail.render(fresh);
        },
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
    // `mount`, not `append`: a bare append(null) writes the string "null", which
    // is exactly what the summary read while a run was going.
    mount(clear(logSummary),
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

    const actions = el('div', { class: 'row actions' });
    if (run.status === 'running' || run.status === 'queued') {
      actions.append(button('Cancel', { class: 'danger', busy: 'Cancelling…' },
        async () => { await api.cancelRun(runId); toast('Cancelling…'); }));
    }
    // Offered only when there is state to resume from, and never for a run that
    // already produced a document - that one wants refining, not repeating.
    if (run.resumable?.length && !run.result && !run.active) {
      actions.append(button('Resume', {
        class: 'primary', busy: 'Resuming…',
        title: `Reuses ${run.resumable.join(', ')} and continues from there`,
      }, async () => {
        const { reusing } = await api.resumeRun(runId);
        toast(`Resuming, keeping ${reusing.length} finished stage(s)`);
        load();
      }));
    }
    if (run.result) {
      // Four ways to take the document away, behind one control. As separate
      // buttons they were four ninths of a row that also holds the decisions,
      // and on any wrap the row re-ordered until Delete sat next to Publish.
      actions.append(
        el('details', { class: 'menu' },
          el('summary', { class: 'as-button' }, 'Download'),
          el('div', { class: 'menu-body' },
            el('a', { href: api.artifactUrl(runId, 'md') }, 'Markdown'),
            el('a', { href: api.artifactUrl(runId, 'json') }, 'JSON'),
            el('a', { href: api.artifactUrl(runId, 'xhtml') }, 'Confluence XHTML'),
            // The audit bundle: request, result, evidence and events in one zip
            // whose manifest digests show any after-the-fact edit.
            el('a', { href: api.auditUrl(runId), title: 'The whole run as one digest-verified zip' },
              'Audit bundle'),
          )),
      );
      if (!run.result.published_url) {
        actions.append(button('Publish to Confluence', { busy: 'Publishing…' },
          () => publish(run)));
      }
      const approval = run.approval?.state;
      actions.append(
        el('button', {
          class: approval === 'approved' ? 'ghost' : 'primary',
          onClick: () => signOff(run, 'approved'),
        }, approval === 'approved' ? '✓ approved' : 'Approve'),
        el('button', {
          class: approval === 'rejected' ? 'danger' : 'ghost',
          onClick: () => signOff(run, 'rejected'),
        }, approval === 'rejected' ? '✗ rejected' : 'Reject'),
      );
    }
    // Pushed to the far end: it was one button away from "Publish to
    // Confluence", and those two should not be neighbours. `margin-left:auto`
    // rather than a spacer element, because a spacer collapses when the row
    // wraps - which is exactly when the separation matters most.
    actions.append(button('Delete', { class: 'danger ghost last', busy: 'Deleting…' },
      async () => {
        if (!confirm('Delete this run and its history?')) return;
        // The erasure record says what stayed behind. Swallowing it would
        // leave the reader believing the uploaded files went with the run.
        const record = await api.deleteRun(runId);
        const left = record?.left_in_place ?? [];
        toast(left.length ? `Deleted. Left in place: ${left.join('; ')}` : 'Deleted', 'ok');
        onChanged?.(null);
      }));

    mount(clear(header),
      el('h1', {}, run.title),
      el('div', { class: 'row', style: 'margin-bottom:14px' },
        el('span', { class: status[0] }, el('span', { class: `dot ${run.status === 'running' ? 'pulse' : ''}` }), status[1]),
        el('span', { class: 'pill' }, run.backend),
        // A run that skipped a source still finishes `ok`. The status pill
        // cannot say so, and the list of what was skipped lives at the bottom
        // of the last tab - so the count travels with the status, and says
        // where to read the rest.
        run.failures > 0
          ? el('a', { href: '#', class: 'pill err', title: 'listed in the Run tab',
              onClick: (e) => { e.preventDefault(); showTab('Run'); } },
              `${run.failures} failure${run.failures === 1 ? '' : 's'}`)
          : null,
        run.warnings > 0
          ? el('a', { href: '#', class: 'pill warn', title: 'listed in the Run tab',
              onClick: (e) => { e.preventDefault(); showTab('Run'); } },
              `${run.warnings} warning${run.warnings === 1 ? '' : 's'}`)
          : null,
        run.parent_id
          ? el('a', { href: `#/run/${run.parent_id}`, class: 'pill', title: 'the version this refines' },
              '\u21b3 refines ' + run.parent_id.slice(0, 8))
          : null,
        el('span', { class: 'muted small' }, ago(run.created_at)),
        run.finished_at && el('span', { class: 'muted small' }, `· took ${duration(run.created_at, run.finished_at)}`),
        el('span', { class: 'grow' }),
        actions,
      ),
      // The dashboard's verdict, on the run's own page. Without it this was the
      // one place that could not answer "is this finished enough to send", and
      // the reader had to leave the document to find out.
      run.readiness
        ? el('div', { class: 'row readiness' },
            el('span', { class: READY_PILL[run.readiness.state] ?? 'pill' }, run.readiness.headline),
            run.readiness.blockers?.length
              ? el('a', { href: '#', class: 'small',
                  onClick: (e) => { e.preventDefault(); showTab('Review'); } },
                  'see what is blocking it')
              : null)
        : null,
      signOffSlot,
      trail(run),
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

  // Recorded, not authenticated: the name is what the operator types, kept so
  // the audit bundle says who believed this PRD.
  //
  // A form rather than two stacked prompt() dialogs. Those ignored the theme,
  // could not be corrected once past the first one, and - because the second
  // was read as `?? ''` - turned "cancel" on the note into a submitted
  // decision. A signature is not something to collect by accident.
  function signOff(run, state) {
    const name = el('input', {
      type: 'text', placeholder: 'your name', value: run.approval?.by ?? '',
      'aria-label': 'Signed off by',
    });
    const note = el('input', {
      type: 'text', placeholder: 'note (optional)', 'aria-label': 'Note',
    });
    const confirmLabel = state === 'approved' ? 'Approve' : 'Reject';
    const send = button(confirmLabel, {
      class: state === 'approved' ? 'primary' : 'danger', busy: 'Recording…',
    }, async () => {
      try {
        await api.setApproval(runId, {
          state, by: name.value.trim(), note: note.value.trim(),
        });
        toast(`Marked ${state}`, 'ok');
        clear(signOffSlot);
        load();
      } catch (error) {
        toast(error.message, 'err');
      }
    });

    const form = el('form', {
      class: 'card sign-off',
      onSubmit: (event) => { event.preventDefault(); send.click(); },
    },
      el('span', { class: 'small muted' },
        state === 'approved' ? 'Signing off:' : 'Sending back:'),
      name, note, send,
      el('button', { type: 'button', class: 'ghost', onClick: () => clear(signOffSlot) }, 'Cancel'));

    clear(signOffSlot).append(form);
    name.focus();
  }

  // Append-only by design, so showing only the latest state would throw away
  // the reason it is a trail at all: a rejected-then-approved run is a
  // different object from one approved first time.
  function trail(run) {
    const history = run.approval?.history ?? [];
    if (!history.length) return null;
    return el('div', { class: 'small muted trail' },
      ...history.map((entry) => el('div', {},
        entry.state === 'approved' ? '✓ ' : entry.state === 'rejected' ? '✗ ' : '· ',
        el('b', {}, entry.state),
        ` by ${entry.by || 'unnamed'} · ${ago(entry.at)}`,
        entry.note ? ` — ${entry.note}` : '')));
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

    const bar = el('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Run views' });
    const panel = el('div', { id: `panel-${runId}`, role: 'tabpanel', tabindex: '0' });
    const buttons = [];

    // Which tab is showing survives a re-render, because everything that
    // re-renders is something you did *from* a tab: approving, publishing,
    // resuming, or a run finishing. Snapping back to the PRD each time made
    // every action feel like it had also navigated away from your work.
    const select = (index) => {
      activeTab = slug(tabs[index][0]);
      buttons.forEach((b, i) => {
        b.classList.toggle('active', i === index);
        b.setAttribute('aria-selected', i === index ? 'true' : 'false');
        // Roving tabindex: one stop for the whole strip, then arrow keys
        // inside it - what a tablist is expected to do from the keyboard.
        b.tabIndex = i === index ? 0 : -1;
      });
      panel.setAttribute('aria-labelledby', buttons[index].id);
      clear(panel).append(tabs[index][1]());
      // replaceState rather than assigning the hash: a reload or a shared link
      // lands on the same tab, without the router tearing the view down and
      // rebuilding it on every click.
      history.replaceState(null, '', `#/run/${runId}/${activeTab}`);
    };

    tabs.forEach(([label], index) => {
      const button = el('button', {
        class: 'tab', role: 'tab', id: `tab-${slug(label)}-${runId}`,
        'aria-controls': panel.id, 'aria-selected': 'false', tabindex: '-1',
      }, label);
      button.addEventListener('click', () => select(index));
      button.addEventListener('keydown', (event) => {
        const step = { ArrowRight: 1, ArrowLeft: -1, Home: -index, End: tabs.length - 1 - index };
        if (!(event.key in step)) return;
        event.preventDefault();
        const next = (index + step[event.key] + tabs.length) % tabs.length;
        select(next);
        buttons[next].focus();
      });
      buttons.push(button);
      bar.append(button);
    });

    body.append(bar, panel);
    const wanted = tabs.findIndex(([label]) => slug(label) === activeTab);
    select(wanted === -1 ? 0 : wanted);
  }

  // Back, forward, or a link to another tab of the run already open: the router
  // sees the same run id and deliberately does not rebuild the view, so the tab
  // segment would otherwise change in the URL and nowhere else. The listener
  // removes itself once the hash names a different run, which is what keeps it
  // from outliving the view in the absence of a teardown hook.
  window.addEventListener('hashchange', function onHash() {
    const [, , id, tab] = location.hash.split('/');
    if (id !== runId) { window.removeEventListener('hashchange', onHash); return; }
    if (tab && tab.toLowerCase() !== activeTab) showTab(tab);
  });

  // Jump to a tab from outside the strip - the warning chips in the header
  // point at the Run tab, where what they are counting is actually listed.
  function showTab(label) {
    activeTab = slug(label);
    const button = body.querySelector(`#tab-${activeTab}-${runId}`);
    if (button) button.click();
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
            // ≈ for the same reason the renderers use it: a size is model
            // inference, and this is the view where inference is labelled.
            req.effort
              ? el('span', { class: 'pill', title: req.effort_rationale || 'estimated effort' },
                  `≈${req.effort}`)
              : null,
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

  // The four severities the critic emits (models.py: Severity), mapped to the
  // pill colours. Blocker and major are what `ReviewReport.blocking` counts, so
  // they are the two that read as red here too.
  const SEVERITY_CLASS = { blocker: 'err', major: 'err', minor: 'warn', nit: '' };

  function reviewTab(review) {
    if (!review) return el('div', { class: 'empty' }, 'No review was run.');
    const findings = review.findings ?? [];
    const coverage = review.coverage ?? {};
    // `requirements` is a count, everything else is a share of it. One dict,
    // two units — reading them all as ratios prints "requirements 2500%".
    const COUNTS = new Set(['requirements']);
    const score = (key, value) => (COUNTS.has(key) ? `${value}` : `${Math.round(value * 100)}%`);
    return el('div', {},
      el('div', { class: 'row', style: 'margin-bottom:14px' },
        el('span', { class: 'pill' }, review.verdict ?? 'reviewed'),
        el('span', { class: 'muted' }, `${findings.length} finding(s)`)),
      // The score line says what the deterministic rules found before the
      // model said anything - a degrading pipeline shows up as a number here,
      // not a vibe.
      Object.keys(coverage).length > 0
        ? el('div', { class: 'row', style: 'margin-bottom:10px' },
            ...Object.entries(coverage).map(([k, v]) =>
              el('span', { class: 'pill' }, `${k.replaceAll('_', ' ')} ${score(k, v)}`)))
        : null,
      review.standards
        ? el('p', { class: 'muted small' }, `Quality rules checked: ${review.standards}`)
        : null,
      review.summary ? el('p', {}, review.summary) : null,
      findings.length > 0
        ? el('div', { class: 'table-wrap' }, el('table', {},
            el('thead', {}, el('tr', {}, ...['Severity', 'Category', 'Where', 'Detail', 'Suggested fix'].map((h) => el('th', {}, h)))),
            el('tbody', {}, ...findings.map((f) => el('tr', {},
              el('td', {}, el('span', { class: `pill ${SEVERITY_CLASS[f.severity] ?? 'warn'}` }, f.severity)),
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
