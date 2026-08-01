const BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

async function req(path, options = {}) {
  const res = await fetch(`${BASE}/api${path}`, {
    headers: options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res.blob();
}

export const api = {
  getActive: () => req("/documents/active"),
  upload: (formData) => req("/documents/upload", { method: "POST", body: formData }),
  getDocument: (id) => req(`/documents/${id}`),
  deleteDocument: (id) => req(`/documents/${id}`, { method: "DELETE" }),
  streamUrl: (id) => `${BASE}/api/documents/${id}/stream`,

  generateStage: (id, stage, body) =>
    req(`/documents/${id}/stages/${stage}/generate`, { method: "POST", body: JSON.stringify(body || {}) }),
  approveStage: (id, stage) =>
    req(`/documents/${id}/stages/${stage}/approve`, { method: "POST" }),

  getChat: (id, stage) => req(`/documents/${id}/chat/${stage}`),
  sendChat: (id, stage, message) =>
    req(`/documents/${id}/chat/${stage}`, { method: "POST", body: JSON.stringify({ message }) }),

  publish: (id) => req(`/documents/${id}/publish`, { method: "POST" }),
  exportUrl: (id, format) => `${BASE}/api/documents/${id}/export?format=${format}`,
};
