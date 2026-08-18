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

const KEY = "sourcework.theme";
const ORDER = ["auto", "light", "dark"];
const LABEL = { auto: "Auto", light: "Light", dark: "Dark" };

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
    button.textContent = LABEL[mode];
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
