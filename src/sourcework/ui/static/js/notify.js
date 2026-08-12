// Telling you a run finished, when you are no longer looking.
//
// This replaces a tray icon, and does the job better. A run takes minutes, so
// you will tab away; an icon changing colour in a menu bar tells you *something*
// happened, while a notification tells you which document and whether it worked.
// It also costs nothing — the Notifications API is in the browser already,
// where the alternative was a GUI toolkit, a platform-specific backend and an
// LGPL dependency.
//
// Two rules it follows, both about not being the kind of app people mute:
//
//   * Permission is asked for on a *user gesture* — starting a run — and never
//     on page load. A permission prompt that appears before you have done
//     anything is the one people click "block" on, and blocking is permanent.
//   * Nothing is raised while you are looking at the page. If the tab is
//     visible, the UI already shows the result and a notification is noise.

const TITLES = {
  ok: 'PRD ready',
  failed: 'Run failed',
  cancelled: 'Run cancelled',
};

export function supported() {
  return typeof Notification !== 'undefined';
}

/** Ask, but only when the user has just done something. Safe to call repeatedly. */
export function askOnGesture() {
  if (!supported() || Notification.permission !== 'default') return;
  // Fire and forget: a rejected promise here means an older browser signature,
  // and there is nothing useful to do about it either way.
  Notification.requestPermission?.().catch(() => {});
}

/**
 * Raise a notification for a finished run, if that is warranted.
 * Returns what it decided, which is what makes it testable.
 */
export function runFinished(run, { visible = !document.hidden } = {}) {
  if (!supported() || Notification.permission !== 'granted') return 'no-permission';
  if (visible) return 'tab-visible';
  if (!TITLES[run?.status]) return 'not-terminal';

  const body = run.status === 'ok'
    ? `${run.title} — ${run.requirements ?? '?'} requirements from ${run.sources ?? '?'} source(s)`
    : `${run.title} — ${(run.error ?? '').split('\n')[0].slice(0, 120) || 'see the run for details'}`;

  const note = new Notification(TITLES[run.status], {
    body,
    // One notification per run: a refinement re-notifying under the same tag
    // replaces its predecessor rather than stacking.
    tag: `sourcework-run-${run.id}`,
  });
  note.onclick = () => {
    window.focus();
    note.close();
  };
  return 'raised';
}
