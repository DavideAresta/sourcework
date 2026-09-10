// A model-id picker: type freely, or choose from what this machine can reach.
//
// This replaces `<datalist>`, which was the right first move — one attribute,
// no code — and the wrong final one. The browser renders it as an unstyleable
// system menu: 16px rows in the OS font, its own width, no way to say which
// entry is the one you already have. Next to a flat, hairline-bordered form it
// reads as a different application.
//
// The list stays *advisory*, which is the important part. Model ids move faster
// than any list we ship, and an id absent from the catalogue is often exactly
// the one you want — so this never constrains what you can type, and offers no
// "invalid" state. It is autocomplete, not a select box.

import autocomplete from './vendor/autocomplete.js';
import { el } from './dom.js';

// High enough that a backend's real catalogue is not truncated: opencode-cli
// alone reports ~139 ids, and a 60-cap cut it alphabetically, hiding models
// that were not in the first slice. The list scrolls; typing narrows it.
const MAX_SUGGESTIONS = 200;
const MIN_POPUP_WIDTH = 380;
const MAX_POPUP_HEIGHT = 420;
const FLIP_ABOVE_PX = 260;

// The vendored library anchors the list to the document and only ever opens
// downward, so near the bottom of the viewport `maxHeight` collapses to a
// sliver - the list is unusable exactly where the lower model cells are. The
// library calls this after positioning, which is the seam to fix it from
// without editing the vendored file.
function place(input, inputRect, container, _maxHeight) {
  const below = window.innerHeight - inputRect.bottom;
  const above = inputRect.top;
  const pageTop = inputRect.top + (window.pageYOffset || document.documentElement.scrollTop);
  const pageLeft = inputRect.left + (window.pageXOffset || document.documentElement.scrollLeft);

  if (below < FLIP_ABOVE_PX && above > below) {
    // Set the cap before reading offsetHeight so the flip-up lands exactly
    // above the field rather than at the collapsed downward height.
    container.style.maxHeight = `${Math.max(Math.min(above - 12, MAX_POPUP_HEIGHT), 120)}px`;
    container.style.top = `${pageTop - container.offsetHeight - 4}px`;
  } else {
    container.style.maxHeight = `${Math.max(Math.min(below - 8, MAX_POPUP_HEIGHT), 120)}px`;
  }
  container.style.left = `${pageLeft}px`;

  // The library sets width to the input's, which clips a long model id in a
  // narrow grid cell. Give the list room, but never past the viewport edge.
  const room = window.innerWidth - inputRect.left - 12;
  container.style.width = `${Math.max(Math.min(MIN_POPUP_WIDTH, room), input.offsetWidth)}px`;
}

export function attachModelPicker(input, models, names = {}) {
  if (!input || !models?.length) return null;

  const nameOf = (id) => names[id] || id;
  // A hit is a hit on the name a person knows ("DeepSeek V4.1 Flash") or on
  // the id an engine needs (`opencode-go/deepseek-flash`); prefix hits rank
  // above substring ones so "deepseek" surfaces the deepseek family first.
  const rank = (id, needle) => {
    if (!needle) return 1;
    return id.toLowerCase().startsWith(needle) || nameOf(id).toLowerCase().startsWith(needle)
      ? 0
      : 1;
  };

  return autocomplete({
    input,
    minLength: 0,
    // Opening on focus dumped up to 60 rows the instant a cell was clicked,
    // covering the neighbouring fields. A deliberate click opens it; so does
    // typing, or ArrowDown for a keyboard user.
    showOnFocus: false,
    disableAutoSelect: true,
    className: 'combo',
    emptyMsg: 'No match — what you typed is still used',
    customize: place,
    click: ({ fetch }) => fetch(),
    fetch(text, update) {
      const needle = text.trim().toLowerCase();
      const matches = models.filter((id) =>
        id.toLowerCase().includes(needle) || nameOf(id).toLowerCase().includes(needle));
      matches.sort((a, b) => rank(a, needle) - rank(b, needle) || nameOf(a).localeCompare(nameOf(b)));
      update(matches.slice(0, MAX_SUGGESTIONS).map((id) => ({ label: nameOf(id), value: id })));
    },
    render(item, currentValue) {
      const needle = currentValue.trim();
      const row = el('div', { class: 'combo-item' });
      // The name goes on top — it is what you searched by — and the id sits
      // beneath on its own line, full width, so both are readable instead of
      // two truncated fragments fighting for one row.
      row.append(
        el('div', { class: 'combo-head' },
          el('span', { class: 'combo-name' }, highlight(item.label, needle)),
          item.value === input.dataset.current
            ? el('span', { class: 'combo-current' }, 'current')
            : null),
        el('div', { class: 'combo-id' }, highlight(item.value, needle)),
      );
      return row;
    },
    onSelect(item) {
      input.value = item.value;
      input.classList.remove('suggested');
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.blur();
    },
  });
}

// The matched run, marked up rather than styled inline, so the emphasis colour
// is the page's and not a second opinion about it.
function highlight(label, needle) {
  const fragment = document.createDocumentFragment();
  const at = needle ? label.toLowerCase().indexOf(needle.toLowerCase()) : -1;
  if (at === -1) {
    fragment.append(label);
    return fragment;
  }
  fragment.append(
    label.slice(0, at),
    el('b', {}, label.slice(at, at + needle.length)),
    label.slice(at + needle.length),
  );
  return fragment;
}
