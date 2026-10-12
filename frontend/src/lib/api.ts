const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api";

export type Book = {
  book_id: string;
  title: string;
  author?: string | null;
  file_name: string;
  file_type: string;
  file_size: number;
  processing_status: string;
  processing_error?: string | null;
  chunk_count: number;
};

export type Citation = {
  citation_id: string;
  chunk_id: string;
  page_number?: number | null;
  chapter?: string | null;
  section?: string | null;
  source_text: string;
};

export type ChatResult = {
  conversation_id: string;
  message_id: string;
  content: string;
  citations: Citation[];
  retrieval: { attempts: number; sufficient: boolean };
};

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = typeof window === "undefined" ? null : localStorage.getItem("book_rag_token");
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, { ...options, headers });
  } catch {
    throw new Error("Unable to connect to FastAPI. Make sure the backend is running on port 8000.");
  }
  if (!response.ok) {
    const rawBody = await response.text();
    let body: { detail?: string; error?: { message?: string } } = {};
    try { body = JSON.parse(rawBody); } catch { /* Keep the raw response below. */ }
    if (response.status === 401 && typeof window !== "undefined") localStorage.removeItem("book_rag_token");
    throw new Error(body.detail || body.error?.message || rawBody || `Request failed (${response.status})`);
  }
  return response.json();
}

export async function authenticate(path: "/auth/login" | "/auth/register", email: string, password: string) {
  const result = await request<{ access_token: string; email: string }>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  localStorage.setItem("book_rag_token", result.access_token);
  return result;
}

export async function authenticateWithGoogle(credential: string) {
  const result = await request<{ access_token: string; email: string }>("/auth/google", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ credential }),
  });
  localStorage.setItem("book_rag_token", result.access_token);
  return result;
}

export function listBooks() {
  return request<Book[]>("/books");
}

export function deleteBook(bookId: string) {
  return request<{ book_id: string; status: string }>(`/books/${bookId}`, { method: "DELETE" });
}

export async function uploadBook(file: File) {
  const body = new FormData();
  body.append("file", file);
  return request<{ book_id: string; document_id: string; job_id: string; processing_status: string; processing_error?: string }>("/books/upload", { method: "POST", body });
}

export function chat(message: string, bookId: string, conversationId?: string) {
  return request<ChatResult>("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, book_id: bookId, conversation_id: conversationId }),
  });
}
