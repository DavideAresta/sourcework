/* The theme control.
 *
 * Three states rather than two, because a two-way switch makes "follow the
 * system" unreachable the moment somebody clicks once: their laptop going dark
 * in the evening would stop being followed forever, and nothing in the
 * interface would say why. Auto is therefore a state you can get back to.
 *
 * The choice lives in localStorage under one key and is applied by stamping
 * `data-theme` on the root element. The stylesheet reads that stamp; nothing
 * here knows a colour. `index.html` and its siblings apply the stored value in
 * a blocking script in <head>, because this module is deferred and would repaint
 * the page a moment after it was already visible.
 */

import { svg } from "./icons.js";

const KEY = "sourcework.theme";
const ORDER = ["auto", "light", "dark"];
const LABEL = { auto: "Auto", light: "Light", dark: "Dark" };
// The icon is the state, so the control says "follow the system" / "light" /
// "dark" without a word, and the label moves to the tooltip and the accessible
// name where there is room for a sentence.
const ICON = { auto: "auto", light: "sun", dark: "moon" };

function stored() {
  try {
    const value = localStorage.getItem(KEY);
    return ORDER.includes(value) ? value : "auto";
  } catch {
    // Private mode, or storage disabled. Following the system is the right
    // fallback: it is what the page does before any script runs.
    return "auto";
  }
}

function apply(mode) {
  const root = document.documentElement;
  if (mode === "auto") delete root.dataset.theme;
  else root.dataset.theme = mode;

  try {
    if (mode === "auto") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, mode);
  } catch {
    // The theme still applies for this page; it just will not be remembered.
  }

  const button = document.getElementById("theme");
  if (button) {
    button.innerHTML = svg(ICON[mode]);
    button.title = `Theme: ${LABEL[mode]} — click to change`;
    button.setAttribute(
      "aria-label",
      `Theme: ${LABEL[mode].toLowerCase()}. Activate to change it.`,
    );
  }
}

const button = document.getElementById("theme");
if (button) {
  button.addEventListener("click", () => {
    apply(ORDER[(ORDER.indexOf(stored()) + 1) % ORDER.length]);
  });
}

apply(stored());
