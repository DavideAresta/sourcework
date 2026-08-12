// The Refine panel: turn a finished PRD into its next version.
//
// The open questions are the point. The previous run ended by saying what it
// could not determine; this is where you answer it, and the answer becomes
// ordinary evidence so the requirement it justifies can cite it. Nothing here
// edits the existing PRD — a refinement is a new run with the old one as its
// baseline, which is the same argument the rest of the system makes about
// traceability.

import { el, clear, bytes, toast, ago } from './dom.js';
import { api } from './api.js';
import * as drafts from './drafts.js';

const TEMPLATES = ['standard', 'lean', 'technical', 'discovery'];

export function refineTab(run, onStarted) {
  const prd = run.result?.prd ?? {};
  const questions = prd.requirements?.open_questions ?? [];
  const conflicts = prd.requirements?.conflicts ?? [];
  const files = [];
  const answers = new Map();

  // Restored before any control is built, so every field can seed itself from
  // it as it is created.
  const draft = drafts.load(run.id) ?? {};
  const status = el('span', { class: 'muted small' });

  const root = el('div');

  // -- open questions ----------------------------------------------------
  const qCard = el('div', { class: 'card' },
    el('h3', { style: 'margin-top:0' }, `Open questions (${questions.length})`));

  if (!questions.length) {
    qCard.append(el('div', { class: 'muted small' },
      'The previous version left nothing open. You can still add material below.'));
  } else {
    qCard.append(el('div', { class: 'muted small', style: 'margin-bottom:12px' },
      'An answer becomes a new source, cited like any other evidence. Leave one blank to keep it open.'));
    for (const [index, q] of questions.entries()) {
      const box = el('textarea', {
        placeholder: 'Yes — decided at the steering group on 12 April, in scope for phase 1.',
        rows: '2',
      });
      // Keyed by the question text, not its position: a refinement can reorder
      // or resolve questions, and an index-keyed draft would then reappear
      // under the wrong one.
      box.value = draft.answers?.[q.question] ?? '';
      answers.set(index, { question: q.question, input: box });
      qCard.append(
        el('div', { style: 'margin-bottom:14px' },
          el('div', { style: 'display:flex;gap:8px;align-items:baseline;margin-bottom:4px' },
            q.blocking ? el('span', { class: 'pill err' }, 'blocking') : el('span', { class: 'pill' }, 'open'),
            el('b', {}, q.question)),
          q.why_it_matters ? el('div', { class: 'muted small', style: 'margin-bottom:5px' }, q.why_it_matters) : null,
          box),
      );
    }
  }
  root.append(qCard);

  // -- recorded conflicts, as context -----------------------------------
  if (conflicts.length) {
    const cCard = el('div', { class: 'card' },
      el('h3', { style: 'margin-top:0' }, `Recorded conflicts (${conflicts.length})`),
      el('div', { class: 'muted small', style: 'margin-bottom:10px' },
        'Say which side wins in a note below and the analyst will apply it and drop the conflict.'));
    for (const c of conflicts) {
      cCard.append(el('div', { style: 'margin-bottom:8px' },
        el('span', { class: 'mono small' }, (c.requirement_ids ?? []).join(', ') || '—'),
        el('div', {}, c.description),
        c.resolution_hint ? el('div', { class: 'muted small' }, `Suggested: ${c.resolution_hint}`) : null));
    }
    root.append(cCard);
  }

  // -- additions ---------------------------------------------------------
  const notes = el('textarea', {
    placeholder: 'Returns from marketplace sellers are in scope for phase 1.\nThe handling fee is waived for loyalty members.\n\nOne per line — each becomes a new source.',
    rows: '5',
  });
  const uris = el('textarea', { placeholder: 'https://intranet/specs/addendum.html\n\nOne URI per line.', rows: '2' });
  const fileList = el('div');
  const drop = el('div', { class: 'drop' }, 'Drop new documents, transcripts or images here');
  const picker = el('input', { type: 'file', multiple: true, style: 'display:none' });

  function renderFiles() {
    clear(fileList);
    files.forEach((file, index) => fileList.append(
      el('div', { class: 'file-row' },
        el('span', { class: 'name' }, file.name),
        el('span', { class: 'muted small' }, bytes(file.size)),
        el('button', { class: 'ghost', onClick: () => { files.splice(index, 1); renderFiles(); } }, '✕'))));
  }
  drop.addEventListener('click', () => picker.click());
  picker.addEventListener('change', () => { files.push(...picker.files); picker.value = ''; renderFiles(); });
  for (const e of ['dragenter', 'dragover']) drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add('over'); });
  for (const e of ['dragleave', 'drop']) drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.remove('over'); });
  drop.addEventListener('drop', (ev) => { files.push(...ev.dataTransfer.files); renderFiles(); });

  const template = el('select', {}, ...TEMPLATES.map((t) => el('option', { value: t }, t)));
  template.value = draft.template ?? run.request?.template ?? 'standard';
  const rounds = el('input', { type: 'number', value: draft.rounds ?? '1', min: '0', max: '4' });
  const instructions = el('textarea', { placeholder: 'Tighten the acceptance criteria on the refund requirements.', rows: '2' });
  notes.value = draft.notes ?? '';
  uris.value = draft.uris ?? '';
  instructions.value = draft.instructions ?? '';

  // -- autosave ----------------------------------------------------------

  function collect() {
    const byQuestion = {};
    for (const { question, input } of answers.values()) {
      if (input.value.trim()) byQuestion[question] = input.value;
    }
    return {
      answers: byQuestion,
      notes: notes.value,
      uris: uris.value,
      instructions: instructions.value,
      template: template.value,
      rounds: rounds.value,
    };
  }

  function showSaved(at) {
    clear(status).append(at ? `Draft saved ${ago(at)}` : 'Draft could not be saved in this browser');
  }

  let pending = null;
  function flush() {
    clearTimeout(pending);
    pending = null;
    const data = collect();
    if (drafts.isEmpty(data)) {
      drafts.discard(run.id);
      clear(status);
      return;
    }
    showSaved(drafts.save(run.id, data));
  }

  // Debounced on input so a fast typist is not writing to storage per
  // keystroke, but flushed synchronously on blur - which is what fires when
  // you click another tab, and therefore the case that started all this.
  const autosave = () => { clearTimeout(pending); pending = setTimeout(flush, 400); };
  for (const field of [notes, uris, instructions, template, rounds,
                       ...[...answers.values()].map((a) => a.input)]) {
    field.addEventListener('input', autosave);
    field.addEventListener('blur', flush);
  }
  // Covers closing the tab or switching windows mid-sentence, where `blur` on
  // the field may never arrive.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush();
  });

  if (!drafts.isEmpty(draft)) showSaved(draft.savedAt);

  root.append(
    el('div', { class: 'card' },
      el('h3', { style: 'margin-top:0' }, 'Add requirements or material'),
      el('label', {}, 'New requirements and decisions'), notes,
      el('div', { style: 'height:12px' }),
      drop, picker, fileList,
      el('div', { style: 'margin-top:12px' }, el('label', {}, 'URIs'), uris),
      el('div', { class: 'grid3', style: 'margin-top:12px' },
        el('div', {}, el('label', {}, 'Template'), template),
        el('div', {}, el('label', {}, 'Review rounds'), rounds),
        el('div', {}, el('label', {}, 'Extra instructions'), instructions)),
    ),
  );

  // -- submit ------------------------------------------------------------
  const submit = el('button', { class: 'primary' }, 'Create next version');
  const lines = (node) => node.value.split('\n').map((s) => s.trim()).filter(Boolean);

  submit.addEventListener('click', async () => {
    const given = [...answers.values()]
      .filter((a) => a.input.value.trim())
      .map((a) => ({ question: a.question, answer: a.input.value.trim() }));

    if (!given.length && !lines(notes).length && !lines(uris).length && !files.length) {
      toast('Answer a question, add a note, or attach something.', 'err');
      return;
    }

    submit.disabled = true;
    submit.textContent = 'Starting…';
    try {
      const { id } = await api.refine(run.id, {
        answers: given,
        notes: lines(notes),
        uris: lines(uris),
        template: template.value,
        review_rounds: Number(rounds.value) || 0,
        extra_instructions: instructions.value.trim() || null,
        // The parent's model choice carries over unless the server overrides —
        // a version built by a different model for no stated reason is a
        // difference nobody asked for.
        llm: run.request?.llm ?? null,
      }, files);
      // Submitted, so it is no longer a draft. Cleared only on success — a
      // failed submit must leave the typing intact.
      drafts.discard(run.id);
      onStarted(id);
    } catch (error) {
      toast(error.message, 'err');
      submit.disabled = false;
      submit.textContent = 'Create next version';
    }
  });

  const discardButton = el('button', {
    class: 'ghost',
    onClick: () => {
      if (!confirm('Discard everything typed here?')) return;
      drafts.discard(run.id);
      for (const { input } of answers.values()) input.value = '';
      notes.value = ''; uris.value = ''; instructions.value = '';
      clear(status);
      toast('Draft discarded');
    },
  }, 'Discard draft');

  root.append(el('div', { class: 'row' },
    submit,
    status,
    discardButton,
    el('span', { class: 'muted small' },
      `Carries ${(prd.evidence ?? []).length} evidence item(s) and `
      + `${(prd.requirements?.requirements ?? []).length} requirement(s) forward. `
      + 'Existing REQ ids are preserved.')));

  return root;
}
