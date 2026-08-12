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

const MAX_SUGGESTIONS = 60;

export function attachModelPicker(input, models) {
  if (!input || !models?.length) return null;

  return autocomplete({
    input,
    minLength: 0,
    // Opening on focus is what makes it feel like a picker rather than a
    // guessing game: with 146 opencode ids, nobody knows the prefix to type.
    showOnFocus: true,
    disableAutoSelect: true,
    className: 'combo',
    emptyMsg: 'No match — what you typed is still used',
    fetch(text, update) {
      const needle = text.trim().toLowerCase();
      const matches = models.filter((m) => m.toLowerCase().includes(needle));
      // Rank a substring hit behind a prefix hit: typing "opus" should surface
      // `opus` before `claude-opus-4-6-thinking`.
      matches.sort((a, b) => {
        const ap = a.toLowerCase().startsWith(needle) ? 0 : 1;
        const bp = b.toLowerCase().startsWith(needle) ? 0 : 1;
        return ap - bp || a.localeCompare(b);
      });
      update(matches.slice(0, MAX_SUGGESTIONS).map((value) => ({ label: value, value })));
    },
    render(item, currentValue) {
      const row = el('div', { class: 'combo-item' });
      row.append(highlight(item.label, currentValue.trim()));
      if (item.label === input.dataset.current) {
        row.append(el('span', { class: 'combo-current' }, 'current'));
      }
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
