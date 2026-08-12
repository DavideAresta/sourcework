// Unsent work, kept safe.
//
// The tab bar rebuilds a panel from scratch on every switch, so anything typed
// into the Refine boxes was gone the moment you looked at the Requirements tab
// to check what you were answering — which is exactly when you would.
//
// Drafts live in localStorage rather than in a variable, so they also survive a
// reload, a trip to another run, and closing the tab. Keyed by run, because
// each run asks different questions.

const PREFIX = 'prdforge.draft.';
const MAX_DRAFTS = 40;
const MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000; // 30 days

// Private-mode Safari throws on access, not just on write. Everything below
// degrades to "no drafts" rather than taking the page down with it.
function storage() {
  try {
    const s = window.localStorage;
    const probe = `${PREFIX}__probe`;
    s.setItem(probe, '1');
    s.removeItem(probe);
    return s;
  } catch {
    return null;
  }
}

function entries(s) {
  const out = [];
  for (let i = 0; i < s.length; i += 1) {
    const key = s.key(i);
    if (!key?.startsWith(PREFIX)) continue;
    try {
      out.push({ key, value: JSON.parse(s.getItem(key)) });
    } catch {
      s.removeItem(key); // corrupt entry — drop it rather than trip over it later
    }
  }
  return out;
}

// Called before every write: a UI that silently accumulates storage forever is
// its own kind of bug.
function prune(s) {
  const all = entries(s);
  const now = Date.now();
  const fresh = all.filter(({ key, value }) => {
    if (!value?.savedAt || now - new Date(value.savedAt).getTime() > MAX_AGE_MS) {
      s.removeItem(key);
      return false;
    }
    return true;
  });
  fresh
    .sort((a, b) => new Date(b.value.savedAt) - new Date(a.value.savedAt))
    .slice(MAX_DRAFTS)
    .forEach(({ key }) => s.removeItem(key));
}

export function load(runId) {
  const s = storage();
  if (!s) return null;
  try {
    return JSON.parse(s.getItem(PREFIX + runId));
  } catch {
    return null;
  }
}

export function save(runId, data) {
  const s = storage();
  if (!s) return null;
  const record = { ...data, savedAt: new Date().toISOString() };
  try {
    prune(s);
    s.setItem(PREFIX + runId, JSON.stringify(record));
    return record.savedAt;
  } catch {
    // Quota exceeded even after pruning. Losing the draft is bad; throwing in
    // the middle of someone's typing is worse.
    return null;
  }
}

export function discard(runId) {
  const s = storage();
  if (s) s.removeItem(PREFIX + runId);
}

/** True when the draft has anything worth restoring. */
export function isEmpty(draft) {
  if (!draft) return true;
  const answers = Object.values(draft.answers ?? {});
  return (
    !answers.some((a) => a.trim())
    && !(draft.notes ?? '').trim()
    && !(draft.uris ?? '').trim()
    && !(draft.instructions ?? '').trim()
  );
}
