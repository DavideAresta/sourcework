// Every call to the server, in one place.

// Every write carries this header. It is not a secret and does not need to be:
// its only job is to be something a cross-site form cannot set, which forces a
// preflight the server does not answer. See the middleware in ui/app.py.
const WRITE_HEADER = { 'X-SourceWork-UI': '1' };

async function request(url, options = {}) {
  const writes = ['POST', 'PUT', 'PATCH', 'DELETE'];
  const method = (options.method || 'GET').toUpperCase();
  if (writes.includes(method)) {
    options = { ...options, headers: { ...(options.headers || {}), ...WRITE_HEADER } };
  }
  const response = await fetch(url, options);
  if (!response.ok) {
    // FastAPI puts the useful part in `detail`; anything else is a raw body.
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body && body.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch { /* not JSON — keep the status line */ }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

const json = (body) => ({
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const api = {
  listRuns: () => request('/api/runs'),
  getRun: (id) => request(`/api/runs/${id}`),
  deleteRun: (id) => request(`/api/runs/${id}`, { method: 'DELETE' }),
  cancelRun: (id) => request(`/api/runs/${id}/cancel`, { method: 'POST' }),
  resumeRun: (id) => request(`/api/runs/${id}/resume`, { method: 'POST' }),
  publish: (id, body) => request(`/api/runs/${id}/publish`, { method: 'POST', ...json(body) }),
  setApproval: (id, body) => request(`/api/runs/${id}/approval`, { method: 'POST', ...json(body) }),

  mesh: () => request('/api/mesh'),
  health: () => request('/healthz'),
  shutdown: () => request('/api/shutdown', { method: 'POST' }),
  dashboard: () => request('/api/dashboard'),
  backends: () => request('/api/backends'),

  readSettings: () => request('/api/settings'),
  writeSettings: (values) => request('/api/settings', { method: 'PUT', ...json(values) }),

  // Multipart, because the files ride along with the JSON spec.
  createRun(spec, files) {
    const form = new FormData();
    form.append('request', JSON.stringify(spec));
    for (const file of files) form.append('files', file, file.name);
    return request('/api/runs', { method: 'POST', body: form });
  },

  // Same multipart shape as createRun: the new material may include files.
  refine(id, spec, files) {
    const form = new FormData();
    form.append('request', JSON.stringify(spec));
    for (const file of files) form.append('files', file, file.name);
    return request(`/api/runs/${id}/refine`, { method: 'POST', body: form });
  },

  artifactUrl: (id, kind) => `/api/runs/${id}/artifact/${kind}`,
  auditUrl: (id) => `/api/runs/${id}/audit`,

  // Resolves when the run *ends* — not when the connection does.
  //
  // `onEvent` fires for every progress line. `onState` fires with a freshly
  // read run each time the connection drops and is about to be retried, so the
  // view can re-sync from the source of truth instead of sitting on whatever it
  // last saw.
  //
  // Reconnecting is the whole point of the rewrite. A drop used to resolve this
  // promise, and the caller re-read the run exactly once and then stopped — so a
  // run that was still going left the page frozen on its last line, ticker
  // stopped, header stale, recoverable only by a manual reload. Drops are not
  // rare: a run is minutes long and the server only sends bytes when something
  // happens, which is precisely what an idle-connection timeout kills.
  //
  // Reconnecting is safe because `subscribe` replays the run's stored events and
  // `result.js` filters them by `seq`.
  streamRun(id, onEvent, { onState } = {}) {
    const BACKOFF_MS = [1000, 2000, 5000, 10_000];
    let attempt = 0;

    return new Promise((resolve) => {
      const open = () => {
        const source = new EventSource(`/api/runs/${id}/events`);
        // A connection that lived is not a failing one: the backoff counts
        // consecutive failures, so it has to reset when one succeeds.
        source.onopen = () => { attempt = 0; };
        source.onmessage = (message) => {
          try { onEvent(JSON.parse(message.data)); } catch { /* not an event */ }
        };
        // The server closing the stream is the only authoritative "it is over".
        source.addEventListener('end', () => { source.close(); resolve(); });
        source.onerror = async () => {
          source.close();
          // The run row decides whether to try again. Gone (deleted) or
          // terminal (it finished while we were disconnected) means there is
          // nothing left to stream; anything else means we lost the wire, not
          // the run.
          const run = await request(`/api/runs/${id}`).catch(() => null);
          if (!run || (!run.active && run.status !== 'running' && run.status !== 'queued')) {
            if (run) onState?.(run);
            resolve();
            return;
          }
          onState?.(run);
          setTimeout(open, BACKOFF_MS[Math.min(attempt++, BACKOFF_MS.length - 1)]);
        };
      };
      open();
    });
  },
};
