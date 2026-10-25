"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import {
  authenticate,
  authenticateWithGoogle,
  Book,
  chat,
  Citation,
  deleteBook,
  listBooks,
  uploadBook,
} from "@/lib/api";

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (options: {
            client_id: string;
            callback: (response: { credential: string }) => void;
          }) => void;
          renderButton: (
            element: HTMLElement,
            options: { theme: string; size: string; width: number },
          ) => void;
        };
      };
    };
  }
}

const statusCopy: Record<string, string> = {
  queued: "Queued",
  extracting: "Reading pages",
  cleaning: "Cleaning text",
  chunking: "Mapping sections",
  embedding: "Building index",
  indexing: "Finalizing",
  completed: "Ready to chat",
  failed: "Needs attention",
};
type ChatLine = {
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
};

export default function Home() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [books, setBooks] = useState<Book[]>([]);
  const [selectedBook, setSelectedBook] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [chatLines, setChatLines] = useState<ChatLine[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [chatLoading, setChatLoading] = useState(false);
  const [notice, setNotice] = useState("");
  const googleButtonRef = useRef<HTMLDivElement>(null);
  const uploadInFlight = useRef(false);
  const lastUpload = useRef("");

  useEffect(() => {
    setNotice("");
    if (!localStorage.getItem("book_rag_token")) return;
    setIsLoggedIn(true);
    listBooks()
      .then(setBooks)
      .catch(() => {
        localStorage.removeItem("book_rag_token");
        setBooks([]);
        setSelectedBook(null);
        setIsLoggedIn(false);
        setNotice("Your session expired. Please sign in again.");
      });
  }, []);

  useEffect(() => {
    if (!isLoggedIn) return;
    let cancelled = false;
    const refreshBooks = async () => {
      try {
        const refreshed = await listBooks();
        if (!cancelled) setBooks(refreshed);
      } catch {
        if (!cancelled && !localStorage.getItem("book_rag_token")) {
          setBooks([]);
          setSelectedBook(null);
          setIsLoggedIn(false);
          setNotice("Your session expired. Please sign in again.");
        }
      }
    };
    const interval = window.setInterval(refreshBooks, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [isLoggedIn]);

  async function signIn(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setNotice("");
    try {
      await authenticate("/auth/login", email, password);
      setIsLoggedIn(true);
      setBooks(await listBooks());
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to sign in");
    } finally {
      setBusy(false);
    }
  }
  async function register() {
    setBusy(true);
    setNotice("");
    try {
      await authenticate("/auth/register", email, password);
      setIsLoggedIn(true);
      setBooks(await listBooks());
    } catch (error) {
      setNotice(
        error instanceof Error ? error.message : "Unable to create account",
      );
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    if (isLoggedIn || !googleButtonRef.current) return;
    const googleClientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID;
    if (!googleClientId) {
      setNotice("Google sign in is not configured. Add NEXT_PUBLIC_GOOGLE_CLIENT_ID to frontend/.env.local.");
      return;
    }
    let cancelled = false;
    let rendered = false;
    const renderGoogleButton = () => {
      if (cancelled || rendered || !googleButtonRef.current || !window.google) return false;
      window.google.accounts.id.initialize({
        client_id: googleClientId,
        callback: async ({ credential }) => {
          setBusy(true);
          setNotice("");
          try {
            await authenticateWithGoogle(credential);
            setIsLoggedIn(true);
            setBooks(await listBooks());
          } catch (error) {
            setNotice(error instanceof Error ? error.message : "Unable to sign in with Google");
          } finally {
            setBusy(false);
          }
        },
      });
      window.google.accounts.id.renderButton(googleButtonRef.current, {
        theme: "outline",
        size: "large",
        width: Math.min(400, googleButtonRef.current.offsetWidth),
      });
      rendered = true;
      return true;
    };
    if (renderGoogleButton()) return () => { cancelled = true; };
    const retry = window.setInterval(() => {
      if (renderGoogleButton()) window.clearInterval(retry);
    }, 100);
    return () => {
      cancelled = true;
      window.clearInterval(retry);
    };
  }, [isLoggedIn]);
  async function uploadSelectedFile(file: File, input?: HTMLInputElement) {
    const uploadKey = `${file.name}:${file.size}:${file.lastModified}`;
    if (uploadInFlight.current || lastUpload.current === uploadKey) return;
    uploadInFlight.current = true;
    lastUpload.current = uploadKey;
    setBusy(true);
    setNotice("");
    try {
      const uploaded = await uploadBook(file);
      setBooks(await listBooks());
      setSelectedBook(uploaded.book_id);
      if (input) input.value = "";
      setNotice(
        uploaded.processing_status === "failed"
          ? "The book was saved, but processing failed. Check the book status."
          : "Upload accepted. Processing runs in the background.",
      );
    } catch (error) {
      lastUpload.current = "";
      setNotice(error instanceof Error ? error.message : "Upload failed");
    } finally {
      uploadInFlight.current = false;
      setBusy(false);
    }
  }
  async function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const input = event.currentTarget.elements.namedItem(
      "book",
    ) as HTMLInputElement;
    const file = input.files?.[0];
    if (file) await uploadSelectedFile(file, input);
  }
  async function removeBook(book: Book) {
    if (!window.confirm(`Delete "${book.title}" and all of its data?`)) return;
    setBusy(true);
    setNotice("");
    try {
      await deleteBook(book.book_id);
      setBooks((items) =>
        items.filter((item) => item.book_id !== book.book_id),
      );
      if (selectedBook === book.book_id) {
        setSelectedBook(null);
        setChatLines([]);
        setConversationId(null);
      }
      setNotice("Book deleted.");
    } catch (error) {
      setNotice(
        error instanceof Error ? error.message : "Unable to delete book",
      );
    } finally {
      setBusy(false);
    }
  }
  async function askQuestion(event: FormEvent) {
    event.preventDefault();
    if (!selectedBook || !question.trim()) return;
    const prompt = question.trim();
    setQuestion("");
    setChatLines((lines) => [...lines, { role: "user", content: prompt }]);
    setChatLoading(true);
    setBusy(true);
    try {
      const result = await chat(
        prompt,
        selectedBook,
        conversationId || undefined,
      );
      setConversationId(result.conversation_id);
      setChatLines((lines) => [
        ...lines,
        {
          role: "assistant",
          content: result.content,
          citations: result.citations,
        },
      ]);
    } catch (error) {
      setChatLines((lines) => [
        ...lines,
        {
          role: "assistant",
          content: error instanceof Error ? `Unable to answer: ${error.message}` : "Unable to answer right now.",
        },
      ]);
    } finally {
      setChatLoading(false);
      setBusy(false);
    }
  }
  function clearChat() {
    setChatLines([]);
    setConversationId(null);
    setQuestion("");
  }

  if (!isLoggedIn)
    return (
      <main className="auth-shell">
        <section className="auth-card">
          <div className="eyebrow">THE MARGIN / 01</div>
          <h1>Read closer.</h1>
          <p className="lede">
            Ask your books precise questions and keep every answer tied to the
            page.
          </p>
          <form onSubmit={signIn} className="auth-form">
            <label>
              Email
              <input
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="you@example.com"
                required
              />
            </label>
            <label>
                <br />
              <input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="8 characters minimum"
                minLength={8}
                required
              />
            </label>
            <button type="submit" disabled={busy}>
              {busy ? "Opening..." : "Sign in"}
            </button>
            <button
              type="button"
              className="quiet-button"
              onClick={register}
              disabled={busy}
            >
              Create an account
            </button>
            <div className="auth-divider"><span>or</span></div>
            <div ref={googleButtonRef} className="google-button" />
          </form>
          {notice && <p className="notice">{notice}</p>}
        </section>
        <aside className="auth-art">
        
          <p>
            “
            Evidence is the
            <br />
            beginning of a better
            <br />
            conversation.”
            <br />
          </p>
     
        </aside>
      </main>
    );

  const activeBook = books.find((book) => book.book_id === selectedBook);
  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <div className="eyebrow">THE MARGIN / YOUR LIBRARY</div>
          <h1>Bookshelf</h1>
        </div>
        <button
          className="quiet-button"
          onClick={() => {
            localStorage.removeItem("book_rag_token");
            setIsLoggedIn(false);
          }}
        >
          Sign out
        </button>
      </header>
      <section className="workspace">
        <aside className="library-panel">
          <form className="upload-box" onSubmit={handleUpload}>
            <span className="upload-mark">+</span>
            <div>
              <strong>{busy ? "Adding book..." : "Add a book"}</strong>
              <small>PDF or DOCX, up to 50 MB</small>
            </div>
            <input
              name="book"
              type="file"
              accept=".pdf,.docx"
              required
              disabled={busy}
              onChange={(event) => {
                const file = event.currentTarget.files?.[0];
                if (file) void uploadSelectedFile(file, event.currentTarget);
              }}
            />
          </form>
          <div className="library-heading">
            <span>Your books</span>
            <span>{books.length.toString().padStart(2, "0")}</span>
          </div>
          <div className="book-list">
            {books.map((book) => (
              <div key={book.book_id} className="book-row">
                <button
                  type="button"
                  className="delete-book"
                  aria-label={`Delete ${book.title}`}
                  title="Delete book"
                  onClick={() => void removeBook(book)}
                  disabled={busy}
                >
                  ×
                </button>
                <button
                  className={`book-item ${selectedBook === book.book_id ? "selected" : ""}`}
                  onClick={() => setSelectedBook(book.book_id)}
                >
                  <span className="book-spine" />
                  <span>
                    <strong>{book.title}</strong>
                    <small>
                      {statusCopy[book.processing_status] ||
                        book.processing_status}
                    </small>
                  </span>
                </button>
              </div>
            ))}
            {books.length === 0 && (
              <p className="empty-copy">
                Your next good question starts with a book.
              </p>
            )}
          </div>
        </aside>
        <section className="chat-panel">
          <div className="chat-header">
            <div>
              <span className="eyebrow">READING ROOM</span>
              <h2>{activeBook?.title || "Choose a book to begin"}</h2>
            </div>
            {activeBook && (
              <button
                type="button"
                className="clear-chat-button"
                onClick={clearChat}
                disabled={busy}
                title="Clear chat"
              >
                Clear chat
              </button>
            )}
          </div>
          <div className="chat-body">
            {!selectedBook ? (
              <div className="empty-chat">
                <span>01</span>
                <h3>Bring a question.</h3>
                <p>
                  Select a finished book from your library and the room will
                  open.
                </p>
              </div>
            ) : chatLines.length === 0 ? (
              <div className="empty-chat">
                <span>02</span>
                <h3>What would you like to find?</h3>
                <p>
                  Answers stay close to the text. When the book is silent, we
                  will say so.
                </p>
              </div>
            ) : (
              <>
                {chatLines.map((line, index) => (
                  <article
                    className={`chat-line ${line.role}`}
                    key={`${line.role}-${index}`}
                  >
                    <span className="line-label">
                      {line.role === "user" ? "YOU" : "THE MARGIN"}
                    </span>
                    <p>{line.content}</p>
                    {line.citations?.map((citation) => (
                      <div className="citation" key={citation.citation_id}>
                        <span>↳</span>
                        <div>
                          <strong>
                            {citation.chapter || "Book passage"}
                            {citation.page_number
                              ? ` / p. ${citation.page_number}`
                              : ""}
                          </strong>
                          <small>{citation.source_text}</small>
                        </div>
                      </div>
                    ))}
                  </article>
                ))}
                {chatLoading && (
                  <div className="chat-loading" role="status" aria-live="polite">
                    <span className="spinner" aria-hidden="true" />
                    <span>Thinking...</span>
                  </div>
                )}
              </>
            )}
          </div>
          <form className="chat-input" onSubmit={askQuestion}>
            <input
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder={
                selectedBook
                  ? "Ask something grounded in this book..."
                  : "Select a book first"
              }
              disabled={!selectedBook || busy || chatLoading}
            />
            <button
              type="submit"
              disabled={!selectedBook || busy || chatLoading || !question.trim()}
            >
              Ask <span>↗</span>
            </button>
          </form>
        </section>
      </section>
      {notice && <div className="toast">{notice}</div>}
    </main>
  );
}
