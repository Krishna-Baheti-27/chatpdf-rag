"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "./context/AuthContext";
import {
  listChats,
  listPdfs,
  uploadPdf,
  createChat,
  getChat,
  sendMessage,
  deleteChat,
  getPdfViewUrl,
} from "./lib/api";
import styles from "./page.module.css";

export default function Home() {
  const { user, loading: authLoading, logoutUser } = useAuth();
  const router = useRouter();

  const [chats, setChats] = useState([]);
  const [pdfs, setPdfs] = useState([]);
  const [activeChat, setActiveChat] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [currentPage, setCurrentPage] = useState("");

  const messagesEndRef = useRef(null);
  const fileInputRef = useRef(null);
  const inputRef = useRef(null);

  // Redirect to login if not authenticated
  useEffect(() => {
    if (!authLoading && !user) {
      router.push("/login");
    }
  }, [authLoading, user, router]);

  // Load chats and PDFs
  useEffect(() => {
    if (user) {
      loadData();
    }
  }, [user]);

  // Scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function loadData() {
    try {
      console.log("[APP] Loading chats and PDFs...");
      const [chatData, pdfData] = await Promise.all([listChats(), listPdfs()]);
      setChats(chatData);
      setPdfs(pdfData);
      console.log(`[APP] Loaded ${chatData.length} chats, ${pdfData.length} PDFs`);
    } catch (err) {
      console.error("[APP] Failed to load data:", err);
    }
  }

  async function handleFileUpload(file) {
    if (!file || !file.name.toLowerCase().endsWith(".pdf")) {
      alert("Please upload a PDF file");
      return;
    }
    setUploading(true);
    console.log(`[APP] ▶ Upload started: ${file.name} (${(file.size/1024/1024).toFixed(1)} MB)`);
    const t0 = performance.now();
    try {
      console.log("[APP] Step 1 — uploading PDF...");
      const pdf = await uploadPdf(file);
      console.log(`[APP] Step 2 — PDF uploaded (id=${pdf.id}), creating chat...`);
      const chat = await createChat(pdf.id);
      console.log(`[APP] Step 3 — chat created (id=${chat.id}), refreshing data...`);
      await loadData();
      console.log("[APP] Step 4 — opening chat...");
      await openChat(chat.id);
      console.log(`[APP] ✅ Upload flow complete in ${((performance.now()-t0)/1000).toFixed(2)}s`);
    } catch (err) {
      console.error(`[APP] ✗ Upload failed after ${((performance.now()-t0)/1000).toFixed(2)}s:`, err);
      alert("Upload failed: " + err.message);
    } finally {
      setUploading(false);
    }
  }

  async function openChat(chatId) {
    try {
      console.log(`[APP] Opening chat: ${chatId}`);
      const chat = await getChat(chatId);
      setActiveChat(chat);
      setMessages(chat.messages || []);
      setCurrentPage(""); // Reset page on new chat
      console.log(`[APP] Chat opened: title="${chat.title}", ${(chat.messages||[]).length} messages`);
      setTimeout(() => inputRef.current?.focus(), 100);
    } catch (err) {
      console.error("[APP] Failed to open chat:", err);
    }
  }

  async function handleSend(e) {
    e?.preventDefault();
    if (!input.trim() || !activeChat || sending) return;

    const userMsg = {
      role: "user",
      content: input.trim(),
      citations: [],
      timestamp: new Date().toISOString(),
    };

    console.log(`[APP] ▶ Sending message: ${userMsg.content.substring(0, 80)}...`);
    const t0 = performance.now();

    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setSending(true);

    try {
      console.log("[APP] Calling sendMessage API...");
      const updated = await sendMessage(activeChat.id, userMsg.content);
      const elapsed = ((performance.now() - t0) / 1000).toFixed(2);
      console.log(`[APP] ✅ Response received in ${elapsed}s — ${updated.messages.length} total messages, title=${updated.title}`);
      setMessages(updated.messages);
      setActiveChat(updated);
      loadData(); // refresh sidebar
    } catch (err) {
      const elapsed = ((performance.now() - t0) / 1000).toFixed(2);
      console.error(`[APP] ✗ sendMessage FAILED after ${elapsed}s:`, err);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: "Sorry, something went wrong. Please try again.",
          citations: [],
          timestamp: new Date().toISOString(),
        },
      ]);
    } finally {
      setSending(false);
    }
  }

  async function handleDeleteChat(chatId, e) {
    e.stopPropagation();
    if (!confirm("Delete this chat?")) return;
    try {
      await deleteChat(chatId);
      if (activeChat?.id === chatId) {
        setActiveChat(null);
        setMessages([]);
        setCurrentPage("");
      }
      await loadData();
    } catch (err) {
      console.error("Delete failed:", err);
    }
  }

  function handleDrop(e) {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFileUpload(file);
  }

  async function handleStartChatWithPdf(pdfId) {
    try {
      const chat = await createChat(pdfId);
      await loadData();
      await openChat(chat.id);
    } catch (err) {
      alert("Failed to start chat: " + err.message);
    }
  }

  function handleCitationClick(citationText) {
    // Expected format: "p12:c19" (page 12) or similar.
    // Try to extract the first sequence of digits right after 'p'
    const match = citationText.match(/p(\d+)/i);
    if (match && match[1]) {
      setCurrentPage(match[1]);
    } else {
      // Fallback: just extract the first number found
      const anyNum = citationText.match(/\d+/);
      if (anyNum) {
        setCurrentPage(anyNum[0]);
      }
    }
  }

  if (authLoading) {
    return (
      <div className={styles.loadingScreen}>
        <div className={styles.loadingSpinner} />
        <p>Loading...</p>
      </div>
    );
  }

  if (!user) return null;

  return (
    <div className={styles.appContainer}>
      {/* ─── Sidebar ─── */}
      <aside className={`${styles.sidebar} ${sidebarOpen ? styles.sidebarOpen : ""}`}>
        <div className={styles.sidebarHeader}>
          <div className={styles.sidebarLogo}>
            <svg width="28" height="28" viewBox="0 0 40 40" fill="none">
              <rect width="40" height="40" rx="10" fill="url(#sg)" />
              <path d="M12 28V12h8a6 6 0 110 12h-8" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M16 20h4l4 8" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"/>
              <defs>
                <linearGradient id="sg" x1="0" y1="0" x2="40" y2="40">
                  <stop stopColor="#7c5cfc" />
                  <stop offset="1" stopColor="#a78bfa" />
                </linearGradient>
              </defs>
            </svg>
            <span>ChatPDF</span>
          </div>
          <button
            className={styles.newChatBtn}
            onClick={() => {
              setActiveChat(null);
              setMessages([]);
            }}
            title="New Chat"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            New
          </button>
        </div>

        <div className={styles.sidebarContent}>
          {chats.length > 0 && (
            <div className={styles.sidebarSection}>
              <h3 className={styles.sectionTitle}>Chats</h3>
              <div className={styles.chatList}>
                {chats.map((chat) => (
                  <div
                    key={chat.id}
                    className={`${styles.chatItem} ${activeChat?.id === chat.id ? styles.chatItemActive : ""}`}
                    onClick={() => openChat(chat.id)}
                  >
                    <div className={styles.chatItemIcon}>
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                        <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
                      </svg>
                    </div>
                    <div className={styles.chatItemInfo}>
                      <span className={styles.chatItemTitle}>{chat.title}</span>
                      <span className={styles.chatItemMeta}>{chat.pdf_filename}</span>
                    </div>
                    <button
                      className={styles.chatDeleteBtn}
                      onClick={(e) => handleDeleteChat(chat.id, e)}
                      title="Delete chat"
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6h14z"/>
                      </svg>
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {pdfs.length > 0 && (
            <div className={styles.sidebarSection}>
              <h3 className={styles.sectionTitle}>Your PDFs</h3>
              <div className={styles.chatList}>
                {pdfs.map((pdf) => (
                  <div
                    key={pdf.id}
                    className={styles.chatItem}
                    onClick={() => handleStartChatWithPdf(pdf.id)}
                  >
                    <div className={styles.chatItemIcon}>
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                        <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
                        <polyline points="14 2 14 8 20 8"/>
                      </svg>
                    </div>
                    <div className={styles.chatItemInfo}>
                      <span className={styles.chatItemTitle}>{pdf.filename}</span>
                      <span className={styles.chatItemMeta}>{pdf.page_count} pages</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className={styles.sidebarFooter}>
          <div className={styles.userInfo}>
            <div className={styles.userAvatar}>
              {user.name?.charAt(0).toUpperCase()}
            </div>
            <span className={styles.userName}>{user.name}</span>
          </div>
          <button className={styles.logoutBtn} onClick={logoutUser} title="Logout">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9"/>
            </svg>
          </button>
        </div>
      </aside>

      {/* ─── Toggle button for mobile ─── */}
      <button
        className={styles.sidebarToggle}
        onClick={() => setSidebarOpen(!sidebarOpen)}
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <line x1="3" y1="6" x2="21" y2="6"/>
          <line x1="3" y1="12" x2="21" y2="12"/>
          <line x1="3" y1="18" x2="21" y2="18"/>
        </svg>
      </button>

      {/* ─── Main Content ─── */}
      <main className={styles.mainContent}>
        {!activeChat ? (
          /* ─── Landing / Upload View ─── */
          <div className={styles.landingContainer}>
            <div className={styles.landingContent}>
              <h1 className={styles.landingTitle}>
                <span className={styles.sparkle}>✨</span>{" "}
                <span className={styles.gradientText}>AI-Powered</span> PDF Assistant
              </h1>
              <p className={styles.landingSubtitle}>
                Upload any PDF and get instant, accurate answers with citations
              </p>

              <div
                className={`${styles.uploadZone} ${dragOver ? styles.uploadZoneDrag : ""} ${uploading ? styles.uploadZoneUploading : ""}`}
                onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                onDragLeave={() => setDragOver(false)}
                onDrop={handleDrop}
                onClick={() => !uploading && fileInputRef.current?.click()}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf"
                  hidden
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) handleFileUpload(file);
                  }}
                />
                {uploading ? (
                  <div className={styles.uploadProgress}>
                    <div className={styles.loadingSpinner} />
                    <p>Processing your PDF...</p>
                    <span>This may take a minute for large documents</span>
                  </div>
                ) : (
                  <>
                    <div className={styles.uploadIcon}>
                      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                        <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M17 8l-5-5-5 5M12 3v12"/>
                      </svg>
                    </div>
                    <p className={styles.uploadText}>
                      Drop a file or{" "}
                      <span className={styles.uploadLink}>upload</span>
                    </p>
                    <span className={styles.uploadHint}>PDF files up to 50MB</span>
                  </>
                )}
              </div>

              <div className={styles.features}>
                <div className={styles.featureCard}>
                  <div className={styles.featureIcon}>🎯</div>
                  <h3>Accurate Answers</h3>
                  <p>AI answers strictly from your document with citations</p>
                </div>
                <div className={styles.featureCard}>
                  <div className={styles.featureIcon}>⚡</div>
                  <h3>Lightning Fast</h3>
                  <p>Powered by Llama 3.3 70B & FAISS vector search</p>
                </div>
                <div className={styles.featureCard}>
                  <div className={styles.featureIcon}>🔒</div>
                  <h3>Private & Secure</h3>
                  <p>Your documents stay private and encrypted</p>
                </div>
              </div>
            </div>
          </div>
        ) : (
          /* ─── Split View: PDF (Left) + Chat (Right) ─── */
          <div className={styles.splitViewContainer}>
            {/* ─── PDF Viewer ─── */}
            <div className={styles.pdfViewerContainer}>
              <iframe
                src={getPdfViewUrl(activeChat.pdf_id, currentPage)}
                className={styles.pdfIframe}
                title="PDF Viewer"
              />
            </div>

            {/* ─── Chat View ─── */}
            <div className={styles.chatContainer}>
              <div className={styles.chatHeader}>
              <div className={styles.chatHeaderInfo}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
                  <polyline points="14 2 14 8 20 8"/>
                </svg>
                <div>
                  <h2 className={styles.chatTitle}>{activeChat.title}</h2>
                  <span className={styles.chatPdfName}>{activeChat.pdf_filename}</span>
                </div>
              </div>
            </div>

            <div className={styles.messagesContainer}>
              {messages.length === 0 && (
                <div className={styles.emptyChat}>
                  <div className={styles.emptyChatIcon}>💬</div>
                  <h3>Start a conversation</h3>
                  <p>Ask anything about <strong>{activeChat.pdf_filename}</strong></p>
                </div>
              )}

              {messages.map((msg, i) => (
                <div
                  key={i}
                  className={`${styles.message} ${msg.role === "user" ? styles.messageUser : styles.messageAi}`}
                  style={{ animationDelay: `${i * 0.05}s` }}
                >
                  {msg.role === "assistant" && (
                    <div className={styles.messageAvatar}>
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                        <circle cx="12" cy="12" r="10"/>
                        <path d="M8 14s1.5 2 4 2 4-2 4-2M9 9h.01M15 9h.01"/>
                      </svg>
                    </div>
                  )}
                  <div className={styles.messageBubble}>
                    <div className={styles.messageContent}>
                      {msg.content}
                    </div>
                    {msg.citations && msg.citations.length > 0 && (
                      <div className={styles.citations}>
                        {msg.citations.map((c, ci) => (
                          <span
                            key={ci}
                            className={styles.citationBadge}
                            onClick={() => handleCitationClick(c)}
                            title="Click to jump to this page in the PDF"
                          >
                            📄 {c}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}

              {sending && (
                <div className={`${styles.message} ${styles.messageAi}`}>
                  <div className={styles.messageAvatar}>
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                      <circle cx="12" cy="12" r="10"/>
                      <path d="M8 14s1.5 2 4 2 4-2 4-2M9 9h.01M15 9h.01"/>
                    </svg>
                  </div>
                  <div className={styles.messageBubble}>
                    <div className={styles.typingIndicator}>
                      <span /><span /><span />
                    </div>
                  </div>
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>

            <form className={styles.inputBar} onSubmit={handleSend}>
              <input
                ref={inputRef}
                type="text"
                placeholder="Ask about your PDF..."
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={sending}
                className={styles.chatInput}
              />
              <button
                type="submit"
                disabled={!input.trim() || sending}
                className={styles.sendBtn}
              >
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="22" y1="2" x2="11" y2="13" />
                  <polygon points="22 2 15 22 11 13 2 9 22 2" />
                </svg>
              </button>
            </form>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
