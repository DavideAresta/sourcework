// The live model-output panel: what the model is thinking and writing, as it
// happens.
//
// Three things shape this file, all of them consequences of the source being a
// token stream rather than a list of messages:
//
//  * **It is append-only text, not rows.** Narration arrives in fragments that
//    continue mid-word, so each batch is appended to the *same* text node until
//    the kind or the agent changes. Rendering one row per batch would break
//    sentences at arbitrary points.
//  * **It is unbounded.** A long run produces megabytes. Only a trailing window
//    is kept in the DOM; older blocks are dropped, because nobody scrolls back
//    through half a megabyte of reasoning and a tab that tries costs memory.
//  * **Auto-scroll must yield.** Following the tail is right until the moment
//    the reader scrolls up to read something, at which point yanking them back
//    down every 600ms makes the panel unusable.
//
// Nothing here is persisted — reloading a finished run shows no narration,
// which is correct: it is the model working, not the work.

import { el } from './dom.js';

const MAX_BLOCKS = 60;
const MAX_BLOCK_CHARS = 20_000;

const LABEL = {
  reasoning: 'thinking',
  text: 'writing',
  step: 'step',
};

export function narrationPanel() {
  // Labelled but deliberately *not* a live region: this arrives token by token,
  // and announcing it would talk over everything else on the page. The progress
  // log next door is the one that speaks.
  const stream = el('div', { class: 'narration', role: 'region', 'aria-label': 'Model output' });
  const status = el('span', { class: 'ts' });
  const toggle = el('button', { class: 'ghost', onClick: () => setOpen(!open) }, 'Hide');

  const card = el('div', { class: 'card narration-card', hidden: true },
    el('div', { class: 'row', style: 'justify-content:space-between;margin-bottom:8px' },
      el('h3', { style: 'margin:0' }, 'Model output'),
      el('div', { class: 'row' }, status, toggle),
    ),
    stream,
  );

  let open = true;
  let current = null; // { kind, agent, node }
  let follow = true;
  let chars = 0;

  // Following the tail, unless the reader has scrolled away from it.
  stream.addEventListener('scroll', () => {
    const distance = stream.scrollHeight - stream.scrollTop - stream.clientHeight;
    follow = distance < 40;
  });

  function setOpen(next) {
    open = next;
    stream.hidden = !open;
    toggle.textContent = open ? 'Hide' : 'Show';
  }

  function push(event) {
    const kind = event.stream_kind || 'text';
    const agent = event.agent || '';
    const text = event.message || '';

    card.hidden = false;
    if (!text && current && current.kind === kind && current.agent === agent) return;

    if (!current || current.kind !== kind || current.agent !== agent) {
      const body = el('span', { class: 'body' });
      const block = el('div', { class: `block ${kind}` },
        el('span', { class: 'who' }, [agent, LABEL[kind] ?? kind].filter(Boolean).join(' · ')),
        body,
      );
      stream.append(block);
      current = { kind, agent, node: body };
      prune();
    }

    // `textContent` rather than innerHTML: this is model output, and it is the
    // one thing on the page most likely to contain angle brackets.
    if (text) {
      // A `step` is a status line — "thinking… ~600 tokens" — so the newest one
      // replaces the last. Only prose accumulates.
      current.node.textContent = kind === 'step'
        ? text
        : (current.node.textContent + text).slice(-MAX_BLOCK_CHARS);
      chars += text.length;
    }
    status.textContent = `${Math.round(chars / 1000)}k characters`;
    if (follow && open) stream.scrollTop = stream.scrollHeight;
  }

  function prune() {
    while (stream.childElementCount > MAX_BLOCKS) stream.firstElementChild.remove();
  }

  // The run ended: keep what is on screen - it is how the PRD was reached -
  // but stop pretending it is live. Called more than once (the `done` event and
  // again when the stream closes), so it has to be idempotent.
  let settled = false;
  function settle() {
    current = null;
    if (settled || card.hidden || chars === 0) return;
    settled = true;
    status.textContent += ' · ended';
  }

  return { node: card, push, settle };
}
