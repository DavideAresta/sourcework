// The new-run form.
//
// The LLM section is the interesting part: those choices become an `llm`
// override that travels inside the request to every agent, so picking
// claude-code here runs the whole mesh on claude-code without restarting
// anything. Leaving a control on "configured default" omits the key entirely,
// which is what makes the environment's setting still mean something.

import { el, clear, bytes, toast, field } from './dom.js';
import { api } from './api.js';
import * as notify from './notify.js';
import { attachModelPicker } from './combo.js';
import { roleLabel, roleHelp } from './roles.js';

const TEMPLATES = ['standard', 'lean', 'technical', 'discovery'];

export function newRunView(onStarted) {
  const files = [];
  const root = el('div');

  // -- inputs ------------------------------------------------------------
  const fileList = el('div');
  // A button, not a div with a click handler: the file input it stands in for
  // is display:none, so a div left no keyboard path to attaching a file at all
  // - on the one control this form cannot work without.
  const drop = el('button', { type: 'button', class: 'drop' },
    el('strong', {}, 'Drop your sources here'),
    el('div', { class: 'small' }, 'or click to choose — PDF, DOCX, PPTX, XLSX, CSV, HTML, MD, VTT, SRT, PNG, JPG'));
  const picker = el('input', { type: 'file', multiple: true, style: 'display:none' });

  function addFiles(list) {
    for (const file of list) files.push(file);
    renderFiles();
  }

  function renderFiles() {
    clear(fileList);
    files.forEach((file, index) => {
      fileList.append(
        el('div', { class: 'file-row' },
          el('span', { class: 'name' }, file.name),
          el('span', { class: 'muted small' }, bytes(file.size)),
          el('button', {
            class: 'ghost small',
            title: `Remove ${file.name}`,
            'aria-label': `Remove ${file.name}`,
            onClick: () => { files.splice(index, 1); renderFiles(); },
          }, '✕'),
        ),
      );
    });
  }

  drop.addEventListener('click', () => picker.click());
  picker.addEventListener('change', () => { addFiles(picker.files); picker.value = ''; });
  for (const event of ['dragenter', 'dragover']) {
    drop.addEventListener(event, (e) => { e.preventDefault(); drop.classList.add('over'); });
  }
  for (const event of ['dragleave', 'drop']) {
    drop.addEventListener(event, (e) => { e.preventDefault(); drop.classList.remove('over'); });
  }
  drop.addEventListener('drop', (e) => addFiles(e.dataTransfer.files));

  const title = el('input', { type: 'text', placeholder: 'Invoice reconciliation' });
  const uris = el('textarea', { placeholder: 'https://intranet/specs/matching.html\nconfluence://PRD/393220' });
  const notes = el('textarea', { placeholder: 'Must ship before year-end close.' });
  const cql = el('textarea', { placeholder: 'space = PRD AND text ~ "reconciliation"' });

  const audience = el('input', { type: 'text', value: 'engineering and product' });
  const template = el('select', {}, ...TEMPLATES.map((t) => el('option', { value: t }, t)));
  const rounds = el('input', { type: 'number', value: '1', min: '0', max: '4' });
  const instructions = el('textarea', { placeholder: 'Treat GDPR retention as a hard constraint.' });

  const publish = el('input', { type: 'checkbox' });
  const estimate = el('input', { type: 'checkbox' });
  const space = el('input', { type: 'text', placeholder: 'PRD' });
  const parent = el('input', { type: 'text', placeholder: 'parent page id' });

  // -- model controls ----------------------------------------------------
  const backend = el('select', {}, el('option', { value: '' }, 'configured default'));
  const effort = el('select', {},
    el('option', { value: '' }, 'model default'),
    ...['low', 'medium', 'high', 'xhigh', 'max'].map((e) => el('option', { value: e }, e)));
  const failover = el('input', { type: 'text', placeholder: 'claude-code,codex-cli' });
  const modelInputs = {};
  // Filled from /api/backends below, not from a list written out here: the
  // roles a run may override are the roles the settings page can configure,
  // and one of the two saying otherwise is how the API came to advertise a
  // role no agent requests.
  const modelRow = el('div', { class: 'grid3' });

  const backendHint = el('div', { class: 'small muted' });

  let catalogue = {};
  const cliNote = el('span', { class: 'muted small' }, 'CLI backends take minutes — you can leave this page.');
  api.backends().then((data) => {
    catalogue = Object.fromEntries(data.backends.map((b) => [b.id, b]));
    // The note is a lie on a hosted install, which offers no CLI backends.
    // `cli_backends` is the offered CLI ids, sent by the server so this page
    // does not carry its own copy of the list.
    const cliOffered = (data.cli_backends ?? [])
      .some((id) => (data.backends ?? []).some((b) => b.id === id));
    if (!cliOffered) cliNote.style.display = 'none';
    for (const role of data.roles ?? []) {
      const input = el('input', { type: 'text', placeholder: 'backend default' });
      modelInputs[role] = input;
      modelRow.append(field(roleLabel(role), input, roleHelp(role)));
    }
    for (const b of data.backends) {
      // Not answering is not the same as not installed, and it is the more
      // useful thing to say: the backend is here, you can still pick it, and
      // the run will fail in seconds rather than in minutes if you do. Left
      // selectable on purpose - you may be about to start the server.
      const dead = b.available && b.reachable === false;
      const label = !b.available
        ? `${b.id} — not available here`
        : dead ? `${b.id} — installed, not answering` : b.id;
      backend.append(el('option', { value: b.id, disabled: !b.available }, label));
    }
    backend.value = '';
    describeBackend();
  }).catch(() => {
    backendHint.textContent = 'Could not read backend availability.';
  });

  let pickers = [];

  function describeBackend() {
    const chosen = backend.value;
    const info = catalogue[chosen];
    // Offer that backend's models as suggestions; the field stays free text
    // because the CLIs accept ids that no listing knows about.
    for (const picker of pickers) picker.destroy();
    pickers = Object.values(modelInputs)
      .map((input) => attachModelPicker(input, info?.models ?? []))
      .filter(Boolean);
    backendHint.textContent = !chosen
      ? 'Uses whatever the mesh was started with.'
      : info?.vision === false
        ? `${chosen} cannot carry images — image inputs will be routed to a vision-capable backend from the failover list, or fail.`
        : `${chosen} · ${(info?.models ?? []).length || 'no'} known model id(s)`;
  }
  backend.addEventListener('change', describeBackend);

  // -- submit ------------------------------------------------------------
  const submit = el('button', { class: 'primary' }, 'Generate PRD');
  const lines = (node) => node.value.split('\n').map((s) => s.trim()).filter(Boolean);

  submit.addEventListener('click', async () => {
    if (!title.value.trim()) { toast('Give the PRD a title.', 'err'); title.focus(); return; }
    if (!files.length && !lines(uris).length && !lines(notes).length && !lines(cql).length) {
      toast('Add at least one file, URI, note or CQL query.', 'err');
      return;
    }

    // On the gesture that starts a run, never on page load: a prompt that
    // appears before you have done anything is the one people block, and
    // blocking is permanent.
    notify.askOnGesture();

    const models = {};
    for (const [role, input] of Object.entries(modelInputs)) {
      if (input.value.trim()) models[role] = input.value.trim();
    }
    const llm = {};
    if (backend.value) llm.backend = backend.value;
    if (effort.value) llm.effort = effort.value;
    if (failover.value.trim()) {
      llm.failover_order = failover.value.split(',').map((s) => s.trim()).filter(Boolean);
    }
    if (Object.keys(models).length) llm.models = models;

    const spec = {
      title: title.value.trim(),
      uris: lines(uris),
      notes: lines(notes),
      confluence_queries: lines(cql),
      audience: audience.value.trim() || 'engineering and product',
      template: template.value,
      review_rounds: Number(rounds.value) || 0,
      extra_instructions: instructions.value.trim() || null,
      publish: publish.checked,
      estimate: estimate.checked,
      confluence_space_key: space.value.trim() || null,
      confluence_parent_id: parent.value.trim() || null,
      llm: Object.keys(llm).length ? llm : null,
    };

    submit.disabled = true;
    submit.textContent = 'Starting…';
    try {
      const { id } = await api.createRun(spec, files);
      onStarted(id);
    } catch (error) {
      toast(error.message, 'err');
      submit.disabled = false;
      submit.textContent = 'Generate PRD';
    }
  });

  root.append(
    el('h1', {}, 'New PRD'),
    el('p', { class: 'muted' }, 'Documents, transcripts, images and Confluence pages in. A traceable PRD out.'),

    el('div', { class: 'card' },
      field('Title', title),
      el('div', { style: 'height:12px' }),
      drop, picker, fileList,
      el('div', { class: 'grid2', style: 'margin-top:12px' },
        field('URIs', uris, 'One per line.'),
        field('Inline notes', notes, 'One per line. Each becomes a source you can cite.'),
      ),
      el('div', { style: 'margin-top:12px' }, field('Confluence CQL', cql, 'One query per line.')),
    ),

    // Folded by default. A run needs a title and something to read; everything
    // below answers a question most runs never ask, and open panels asking it
    // anyway is what made this page look like a configuration screen.
    el('details', { class: 'card fold' },
      el('summary', {}, 'Model',
        el('span', { class: 'muted small' }, ' — this run only, otherwise the configured default')),
      el('div', { class: 'grid3' },
        field('Backend', backend),
        field('Reasoning effort', effort),
        field('Failover order', failover),
      ),
      el('div', { style: 'height:12px' }),
      modelRow,
      el('div', { style: 'height:8px' }), backendHint,
    ),

    el('details', { class: 'card fold' },
      el('summary', {}, 'Shape',
        el('span', { class: 'muted small' }, ' — template, audience, review rounds, publishing')),
      el('div', { class: 'grid3' },
        field('Template', template),
        field('Audience', audience),
        field('Review rounds', rounds),
      ),
      el('div', { style: 'margin-top:12px' }, field('Extra instructions', instructions)),
      el('div', { class: 'row', style: 'margin-top:14px' },
        el('label', { style: 'display:flex;gap:6px;align-items:center;margin:0' }, estimate,
          'Estimate effort',
          el('span', { class: 'muted small' },
            '— T-shirt size per requirement, marked as model inference')),
      ),
      el('details', { class: 'adv', style: 'margin-top:14px' },
        el('summary', {}, 'Publish to Confluence'),
        el('div', { class: 'row' },
          el('label', { style: 'display:flex;gap:6px;align-items:center;margin:0' }, publish, 'Publish when finished'),
        ),
        el('div', { class: 'grid2', style: 'margin-top:10px' },
          field('Space key', space),
          field('Parent page id', parent),
        ),
      ),
    ),

    el('div', { class: 'row' }, submit, cliNote),
  );

  return root;
}
