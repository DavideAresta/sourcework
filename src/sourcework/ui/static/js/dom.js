// Small DOM helpers. Everything the UI renders goes through `el` or `escape`,
// so there is exactly one place where untrusted text could become markup — and
// it doesn't.

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === 'dataset') Object.assign(node.dataset, value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function escape(text) {
  return String(text ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

// Append to an existing node with the same skipping rules as `el`. Native
// `.append(null)` renders the literal text "null", which is how a header with
// two absent optional rows ends up saying "nullnull".
export function mount(node, ...children) {
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function $(selector, root = document) {
  return root.querySelector(selector);
}

// One live region, reused. Toasts were appended straight to <body>, which meant
// two at once drew on top of each other, and none of them was ever announced -
// for several failures in this app the toast is the *only* surfacing there is,
// so a screen reader was told nothing at all.
let toastStack = null;

export function toast(message, kind = '') {
  if (!toastStack) {
    toastStack = el('div', {
      class: 'toasts', role: 'status', 'aria-live': 'polite', 'aria-atomic': 'false',
    });
    document.body.append(toastStack);
  }
  const node = el('div', { class: `toast ${kind}` }, message);
  toastStack.append(node);
  setTimeout(() => node.remove(), kind === 'err' ? 7000 : 3500);
}

let fieldSeq = 0;

// A labelled control, with the label actually attached to it.
//
// Every label in this app used to be a sibling <label> with no `for`, so
// clicking one focused nothing and a screen reader announced the input
// unlabelled. Minting the id here rather than at each call site is what keeps
// that from being a thing anyone has to remember.
export function field(label, control, help = '', title = '') {
  const id = control.id || `f${++fieldSeq}`;
  control.id = id;
  return el('div', {},
    el('label', { for: id, title: title || null }, label),
    control,
    help ? el('div', { class: 'small muted hint' }, help) : null,
  );
}

export function ago(iso) {
  if (!iso) return '';
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}

export function duration(from, to) {
  if (!from || !to) return '';
  const seconds = Math.round((new Date(to) - new Date(from)) / 1000);
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export function bytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
