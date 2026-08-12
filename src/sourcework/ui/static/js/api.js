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
  publish: (id, body) => request(`/api/runs/${id}/publish`, { method: 'POST', ...json(body) }),

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

  // Resolves when the run ends; `onEvent` fires for every progress line.
  streamRun(id, onEvent) {
    const source = new EventSource(`/api/runs/${id}/events`);
    return new Promise((resolve) => {
      source.onmessage = (message) => {
        try { onEvent(JSON.parse(message.data)); } catch { /* keepalive */ }
      };
      source.addEventListener('end', () => { source.close(); resolve(); });
      // A network drop looks the same as a finished stream from here; the
      // caller re-reads the run, which is the source of truth either way.
      source.onerror = () => { source.close(); resolve(); };
    });
  },
};
