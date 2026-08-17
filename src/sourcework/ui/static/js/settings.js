// The settings page: a form over .env, plus a read-only view of which backends
// this machine can actually use.
//
// Two things it is careful to be honest about. Secrets come down masked and a
// masked value is never sent back, so opening the page cannot leak a token and
// saving cannot blank one you did not touch. And saving does not change a
// running mesh — the agents read their configuration once, at start-up — so the
// page says "restart" rather than implying it took effect.
//
// The models section is shaped around one observation: you run one backend at a
// time. Twenty of the twenty-four model controls are therefore about backends
// that will not be used tonight, so the active one gets the card and the rest get a
// disclosure. Profiles sit on top because the useful knowledge here — that
// `opencode/claude-opus-4-6` reasons well, and that an unset opencode model
// fails outright — belongs in the app rather than in your memory.

import { el, clear, mount, toast } from './dom.js';
import { api } from './api.js';
import { attachModelPicker } from './combo.js';

const form = document.getElementById('form');
const backendsBox = document.getElementById('backends');
const pathLabel = document.getElementById('env-path');
const saveButton = document.getElementById('save');

const controls = new Map();

const ROLES = ['default', 'reasoning', 'vision', 'critic'];

// Role ids are the code's vocabulary, not the reader's. "reasoning" is the role
// the analyst runs on, and that is what the label should say.
const ROLE_LABEL = {
  default: 'Everyday work',
  reasoning: 'Hard thinking',
  vision: 'Reading images',
  critic: 'Adversarial review',
};

// Backend-specific gotchas worth surfacing where the model is chosen.
const HINTS = {
  'llama-cpp': 'Runs directly against a local llama-server or llama-swap; it does not need a '
    + 'LiteLLM proxy. Start llama-server, then choose one of the model ids it reports here.',
  'opencode-cli': 'opencode needs an explicit model: with none it fails outright with '
    + '"Unexpected server error".',
  'agy-cli': 'agy ids usually carry their own tier (-high/-medium/-low), and then the '
    + 'reasoning-effort setting is left alone. It cannot read images, so the vision '
    + 'role falls through to another backend.',
  'codex-cli': 'Codex tiers by reasoning effort rather than by model, so one id across '
    + 'the roles is normal. An OPENAI_API_KEY in your environment overrides `codex '
    + 'login`, billing the API instead of your subscription.',
};

const ROLE_HELP = {
  default: 'Ingestion, drafting, review',
  reasoning: 'The analyst — the call that decides whether the PRD is any good',
  vision: 'Screenshots, wireframes, diagrams',
  critic: 'Best from another family - a critic that shares the writer\'s training shares its blind spots',
};

function control(field) {
  if (field.kind === 'bool') {
    const box = el('input', { type: 'checkbox' });
    box.checked = ['1', 'true', 'yes', 'on'].includes(String(field.value).toLowerCase());
    const node = el('label', { style: 'display:flex;gap:8px;align-items:center;margin:0' }, box, field.label);
    node.dataset.key = field.key;
    return { node, read: () => (box.checked ? '1' : '0'), labelled: true };
  }
  if (field.kind === 'select') {
    const select = el('select', {}, ...field.options.map((o) => el('option', { value: o }, o || '(unset)')));
    select.value = field.value ?? '';
    select.dataset.key = field.key;
    return { node: select, read: () => select.value };
  }
  const input = el('input', {
    type: field.kind === 'password' ? 'password' : field.kind === 'number' ? 'number' : 'text',
    value: field.value ?? '',
    placeholder: field.placeholder || '',
    step: 'any',
  });
  input.dataset.key = field.key;

  // A suggestion is pre-filled, not just hinted: an empty model cell is a
  // choice nobody made, and on opencode it is the difference between a run and
  // an "Unexpected server error". Styled as unconfirmed until touched, so it is
  // never mistaken for something already saved — and Save does write it.
  if (!input.value && field.suggested) {
    input.value = field.suggested;
    input.classList.add('suggested');
    input.addEventListener('input', () => input.classList.remove('suggested'), { once: true });
  }
  return { node: input, read: () => input.value };
}

async function load() {
  const data = await api.readSettings();
  pathLabel.textContent = data.path;
  clear(form);
  controls.clear();

  const groups = new Map();
  for (const field of data.fields) {
    if (!groups.has(field.group)) groups.set(field.group, []);
    groups.get(field.group).push(field);
  }

  for (const [group, fields] of groups) {
    const isModels = fields.every((f) => f.backend && f.role);
    const body = isModels ? modelSection(fields, data.profiles ?? {}) : plainGrid(fields);
    form.append(el('div', { class: 'card' }, el('h3', { style: 'margin-top:0' }, group), body));
  }
}

function plainGrid(fields) {
  const grid = el('div', { class: 'grid2' });
  for (const field of fields) {
    const made = control(field);
    controls.set(field.key, made.read);
    grid.append(
      el('div', {},
        made.labelled ? null : el('label', { title: field.key }, field.label),
        made.node,
        field.help ? el('div', { class: 'small muted', style: 'margin-top:3px' }, field.help) : null,
      ),
    );
  }
  return grid;
}

function modelSection(fields, profiles) {
  const backends = [...new Set(fields.map((f) => f.backend))];

  // Only the roles the server actually sent. A browser holding a newer bundle
  // than the process it is talking to - exactly what a restart-less deploy or a
  // cached tab produces - would otherwise dereference a cell that does not
  // exist, and one throw in here takes down every section below it: Models,
  // Limits, Credentials and Confluence all vanish over one missing input.
  const served = new Set(fields.map((f) => f.role));
  const roles = ROLES.filter((role) => served.has(role));

  // Built once and re-parented rather than re-rendered, so switching the active
  // backend cannot silently discard something half-typed.
  const cells = new Map();
  for (const field of fields) {
    const made = control(field);
    controls.set(field.key, made.read);
    made.node.title = field.key;
    made.node.dataset.backend = field.backend;
    cells.set(`${field.backend} ${field.role}`, { field, input: made.node });
  }

  const featured = el('div', { class: 'backend-card' });
  const rest = el('div');
  const details = el('details', { class: 'more' }, el('summary', {}, 'Other backends'), rest);

  function paint() {
    const active = activeBackend(backends);

    clear(featured);
    // `mount`, not native append: the last child is absent for every backend
    // but opencode, and `.append(null)` renders the literal text "null".
    mount(featured,
      el('div', { class: 'row', style: 'gap:8px' },
        el('span', { class: 'mono', style: 'font-size:15px' }, active),
        el('span', { class: 'pill ok' }, 'active'),
      ),
      el('div', { class: 'small muted', style: 'margin:2px 0 14px' },
        'Everything runs here unless a run chooses otherwise.'),
      el('div', { class: 'grid4' },
        ...roles.map((role) => {
          const cell = cells.get(`${active} ${role}`);
          return el('div', {},
            el('label', { title: cell.field.key }, ROLE_LABEL[role] ?? role),
            cell.input,
            el('div', { class: 'small muted', style: 'margin-top:3px' }, ROLE_HELP[role] ?? ''));
        }),
      ),
      HINTS[active]
        ? el('div', { class: 'small muted', style: 'margin-top:12px' }, HINTS[active])
        : null,
    );

    clear(rest);
    const others = backends.filter((b) => b !== active);
    for (const backend of others) {
      rest.append(
        el('div', { class: 'other-backend' },
          el('div', { class: 'mono small', style: 'margin-bottom:6px' }, backend),
          el('div', { class: 'grid4' },
            ...roles.map((role) => {
              const cell = cells.get(`${backend} ${role}`);
              return el('div', {},
                el('label', { class: 'small', title: cell.field.key }, ROLE_LABEL[role] ?? role),
                cell.input);
            })),
        ),
      );
    }
    details.querySelector('summary').textContent =
      `Models for the other ${others.length} backends`;
  }

  document.querySelector('[data-key="SOURCEWORK_LLM__BACKEND"]')
    ?.addEventListener('change', paint);

  const section = el('div', {}, profileRow(profiles, cells), featured, details);
  paint();
  return section;
}

// The active backend, read live from the Routing control, so the card follows
// what you just picked rather than what was last saved.
function activeBackend(backends) {
  const chosen = document.querySelector('[data-key="SOURCEWORK_LLM__BACKEND"]')?.value;
  return backends.includes(chosen) ? chosen : backends[0];
}

function profileRow(profiles, cells) {
  const names = Object.keys(profiles);
  if (!names.length) return el('div');

  const row = el('div', { class: 'profiles' });
  for (const name of names) {
    const profile = profiles[name];
    row.append(el('button', {
      class: 'profile',
      type: 'button',
      onClick: () => {
        // Every backend, not only the active one: a failover target with no
        // model is a failover that does not work.
        for (const cell of cells.values()) {
          const value = (profile.models ?? {})[cell.field.key];
          if (value === undefined) continue;
          cell.input.value = value;
          cell.input.classList.remove('suggested');
        }
        toast(`${profile.label} applied to every backend — Save to keep it.`, 'ok');
      },
    },
      el('span', { class: 'name' }, profile.label ?? name),
      el('span', { class: 'small muted' }, profile.detail ?? ''),
    ));
  }
  return el('div', { style: 'margin-bottom:16px' },
    el('div', { class: 'small muted', style: 'margin-bottom:8px' },
      'Start from a profile, then adjust anything below.'),
    row);
}

// Real model ids from this machine, offered per backend. Free text either way —
// a curated list goes stale and the backends move faster than we do — but
// typing `opencode/claude-sonnet-4-5` from memory is how you get a typo that
// only surfaces nine minutes into a run.
let pickers = [];

async function suggestModels() {
  let data;
  try {
    data = await api.backends();
  } catch {
    return;
  }
  for (const picker of pickers) picker.destroy();
  pickers = [];
  for (const backend of data.backends ?? []) {
    for (const input of document.querySelectorAll(`input[data-backend="${backend.id}"]`)) {
      const picker = attachModelPicker(input, backend.models ?? []);
      if (picker) pickers.push(picker);
    }
  }
}

async function loadBackends() {
  clear(backendsBox);
  try {
    const data = await api.backends();
    backendsBox.append(
      el('div', { class: 'small muted', style: 'margin-bottom:10px' },
        `Active: ${data.active}`,
        data.failover_order.length ? ` · failover: ${data.failover_order.join(' → ')}` : ' · no failover configured'),
    );
    for (const backend of data.backends) {
      backendsBox.append(
        el('div', { class: 'row', style: 'padding:5px 0;border-top:1px solid var(--line)' },
          el('span', { class: `pill ${backend.available ? 'ok' : 'err'}` },
            el('span', { class: 'dot' }), backend.available ? 'available' : 'not here'),
          el('span', { class: 'mono' }, backend.id),
          el('span', { class: 'small muted' }, backend.vision ? 'vision' : 'text-only'),
          el('span', { style: 'flex:1' }),
          el('span', { class: 'small muted' },
            backend.detail
              ? backend.detail
              : backend.configured_model ? `model: ${backend.configured_model}` : 'backend default'),
        ),
      );
    }
    backendsBox.append(
      el('div', { class: 'small muted', style: 'margin-top:10px' },
        'CLI backends authenticate as you — if you are signed into the tool, runs use that subscription and need no API key here.'),
    );
  } catch (error) {
    backendsBox.append(el('div', { class: 'small muted' }, `Could not probe backends: ${error.message}`));
  }
}

saveButton.addEventListener('click', async () => {
  const values = {};
  for (const [key, read] of controls) values[key] = read();
  saveButton.disabled = true;
  try {
    const result = await api.writeSettings(values);
    toast(result.message, result.restart_required ? '' : 'ok');
    if (result.restart_required) {
      document.getElementById('restart-note').style.display = '';
      // The agents re-exec themselves a moment after answering; this page's
      // own probes would race the restart, so let the mesh come back first.
      setTimeout(() => location.reload(), 2500);
    }
    await load();
    await suggestModels();
    await loadBackends();
  } catch (error) {
    toast(error.message, 'err');
  } finally {
    saveButton.disabled = false;
  }
});

load()
  .then(suggestModels)
  .catch((error) => toast(error.message, 'err'));
loadBackends();
