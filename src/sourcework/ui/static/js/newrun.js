// The new-run form.
//
// The LLM section is the interesting part: those choices become an `llm`
// override that travels inside the request to every agent, so picking
// claude-code here runs the whole mesh on claude-code without restarting
// anything. Leaving a control on "configured default" omits the key entirely,
// which is what makes the environment's setting still mean something.

import { el, clear, bytes, toast } from './dom.js';
import { api } from './api.js';
import * as notify from './notify.js';
import { attachModelPicker } from './combo.js';

const TEMPLATES = ['standard', 'lean', 'technical', 'discovery'];
const ROLES = [
  ['default', 'Extraction'],
  ['reasoning', 'Analyst / writer'],
  ['vision', 'Images'],
  ['critic', 'Review'],
];

export function newRunView(onStarted) {
  const files = [];
  const root = el('div');

  // -- inputs ------------------------------------------------------------
  const fileList = el('div');
  const drop = el('div', { class: 'drop' }, 'Drop files here, or click to choose — PDF, DOCX, PPTX, XLSX, CSV, HTML, MD, VTT, SRT, PNG, JPG');
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
            title: 'Remove',
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
  const uris = el('textarea', { placeholder: 'https://intranet/specs/matching.html\nconfluence://PRD/393220\n\nOne URI per line.' });
  const notes = el('textarea', { placeholder: 'Must ship before year-end close.\n\nOne note per line — each becomes an inline source.' });
  const cql = el('textarea', { placeholder: 'space = PRD AND text ~ "reconciliation"\n\nOne CQL query per line.' });

  const audience = el('input', { type: 'text', value: 'engineering and product' });
  const template = el('select', {}, ...TEMPLATES.map((t) => el('option', { value: t }, t)));
  const rounds = el('input', { type: 'number', value: '1', min: '0', max: '4' });
  const instructions = el('textarea', { placeholder: 'Treat GDPR retention as a hard constraint.' });

  const publish = el('input', { type: 'checkbox' });
  const space = el('input', { type: 'text', placeholder: 'PRD' });
  const parent = el('input', { type: 'text', placeholder: 'parent page id' });

  // -- model controls ----------------------------------------------------
  const backend = el('select', {}, el('option', { value: '' }, 'configured default'));
  const effort = el('select', {},
    el('option', { value: '' }, 'model default'),
    ...['low', 'medium', 'high', 'xhigh', 'max'].map((e) => el('option', { value: e }, e)));
  const failover = el('input', { type: 'text', placeholder: 'claude-code,opencode-cli' });
  const modelInputs = {};
  const modelRow = el('div', { class: 'grid3' });

  for (const [role, label] of ROLES) {
    const input = el('input', { type: 'text', placeholder: 'backend default' });
    modelInputs[role] = input;
    modelRow.append(el('div', {}, el('label', {}, label), input));
  }

  const backendHint = el('div', { class: 'small muted' });

  let catalogue = {};
  api.backends().then((data) => {
    catalogue = Object.fromEntries(data.backends.map((b) => [b.id, b]));
    for (const b of data.backends) {
      backend.append(el('option', {
        value: b.id,
        disabled: !b.available,
      }, b.available ? b.id : `${b.id} — not available here`));
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
    pickers = ROLES
      .map(([role]) => attachModelPicker(modelInputs[role], info?.models ?? []))
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
    for (const [role] of ROLES) {
      if (modelInputs[role].value.trim()) models[role] = modelInputs[role].value.trim();
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
      el('label', {}, 'Title'), title,
      el('div', { style: 'height:12px' }),
      drop, picker, fileList,
      el('div', { class: 'grid2', style: 'margin-top:12px' },
        el('div', {}, el('label', {}, 'URIs'), uris),
        el('div', {}, el('label', {}, 'Inline notes'), notes),
      ),
      el('div', { style: 'margin-top:12px' }, el('label', {}, 'Confluence CQL'), cql),
    ),

    el('div', { class: 'card' },
      el('h3', { style: 'margin-top:0' }, 'Model'),
      el('div', { class: 'grid3' },
        el('div', {}, el('label', {}, 'Backend'), backend),
        el('div', {}, el('label', {}, 'Reasoning effort'), effort),
        el('div', {}, el('label', {}, 'Failover order'), failover),
      ),
      el('div', { style: 'height:12px' }),
      modelRow,
      el('div', { style: 'height:8px' }), backendHint,
    ),

    el('div', { class: 'card' },
      el('h3', { style: 'margin-top:0' }, 'Shape'),
      el('div', { class: 'grid3' },
        el('div', {}, el('label', {}, 'Template'), template),
        el('div', {}, el('label', {}, 'Audience'), audience),
        el('div', {}, el('label', {}, 'Review rounds'), rounds),
      ),
      el('div', { style: 'margin-top:12px' }, el('label', {}, 'Extra instructions'), instructions),
      el('details', { class: 'adv', style: 'margin-top:14px' },
        el('summary', {}, 'Publish to Confluence'),
        el('div', { class: 'row' },
          el('label', { style: 'display:flex;gap:6px;align-items:center;margin:0' }, publish, 'Publish when finished'),
        ),
        el('div', { class: 'grid2', style: 'margin-top:10px' },
          el('div', {}, el('label', {}, 'Space key'), space),
          el('div', {}, el('label', {}, 'Parent page id'), parent),
        ),
      ),
    ),

    el('div', { class: 'row' }, submit, el('span', { class: 'muted small' }, 'CLI backends take minutes — you can leave this page.')),
  );

  return root;
}
