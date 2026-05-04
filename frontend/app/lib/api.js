const API_BASE = "http://localhost:8000";

/**
 * Wrapper around fetch that adds auth headers and handles JSON.
 */
async function apiFetch(path, options = {}) {
  const token =
    typeof window !== "undefined" ? localStorage.getItem("token") : null;

  const headers = {
    ...(options.headers || {}),
  };

  // Don't set Content-Type for FormData (browser sets it with boundary)
  if (!(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const method = options.method || "GET";
  const startTime = performance.now();
  console.log(`[API] → ${method} ${path}`);

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });

  const elapsed = ((performance.now() - startTime) / 1000).toFixed(2);

  if (res.status === 204) {
    console.log(`[API] ← ${method} ${path} → 204 (${elapsed}s)`);
    return null;
  }

  const data = await res.json();

  if (!res.ok) {
    console.error(`[API] ← ${method} ${path} → ${res.status} (${elapsed}s)`, data);
    throw new Error(data.detail || "Something went wrong");
  }

  console.log(`[API] ← ${method} ${path} → ${res.status} (${elapsed}s)`, data);
  return data;
}

// ── Auth ───────────────────────────────────────────────────────────
export async function register(email, name, password) {
  const data = await apiFetch("/api/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, name, password }),
  });
  localStorage.setItem("token", data.access_token);
  return data;
}

export async function login(email, password) {
  const data = await apiFetch("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  localStorage.setItem("token", data.access_token);
  return data;
}

export async function getMe() {
  return apiFetch("/api/auth/me");
}

export function logout() {
  localStorage.removeItem("token");
}

// ── PDFs ───────────────────────────────────────────────────────────
export async function uploadPdf(file) {
  const formData = new FormData();
  formData.append("file", file);
  return apiFetch("/api/pdfs/upload", {
    method: "POST",
    body: formData,
  });
}

export async function listPdfs() {
  return apiFetch("/api/pdfs/");
}

export async function deletePdf(pdfId) {
  return apiFetch(`/api/pdfs/${pdfId}`, { method: "DELETE" });
}

export function getPdfViewUrl(pdfId, page) {
  const token = typeof window !== "undefined" ? localStorage.getItem("token") : "";
  let url = `${API_BASE}/api/pdfs/${pdfId}/view?token=${token}`;
  if (page) url += `#page=${page}`;
  return url;
}

// ── Chats ──────────────────────────────────────────────────────────
export async function createChat(pdfId) {
  return apiFetch(`/api/chats/?pdf_id=${pdfId}`, { method: "POST" });
}

export async function listChats() {
  return apiFetch("/api/chats/");
}

export async function getChat(chatId) {
  return apiFetch(`/api/chats/${chatId}`);
}

export async function deleteChat(chatId) {
  return apiFetch(`/api/chats/${chatId}`, { method: "DELETE" });
}

export async function renameChat(chatId, title) {
  return apiFetch(`/api/chats/${chatId}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export async function sendMessage(chatId, content) {
  return apiFetch(`/api/chats/${chatId}/message`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}
