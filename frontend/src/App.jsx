import { useEffect, useState } from "react";
import { api } from "./api.js";
import UploadPanel from "./components/UploadPanel.jsx";
import ProgressStream from "./components/ProgressStream.jsx";
import StageWorkspace from "./components/StageWorkspace.jsx";

export default function App() {
  const [activeId, setActiveId] = useState(null);
  const [ready, setReady] = useState(false); // stages 1-3 done, workspace unlocked
  const [checking, setChecking] = useState(true);

  async function checkActive() {
    setChecking(true);
    try {
      const info = await api.getActive();
      if (info.active) {
        setActiveId(info.document_id);
        setReady(info.pipeline_state === "waiting_user" || info.status !== "uploaded");
      } else {
        setActiveId(null);
      }
    } finally {
      setChecking(false);
    }
  }

  useEffect(() => { checkActive(); }, []);

  async function handleDelete() {
    if (!activeId) return;
    if (!confirm("Delete this document and everything generated from it? This cannot be undone.")) return;
    await api.deleteDocument(activeId);
    setActiveId(null);
    setReady(false);
  }

  return (
    <div className="app-shell">
      <header className="board-header">
        <div>
          <h1>Teacher Knowledge Package Studio</h1>
          <div className="subtitle">Document → Classroom-Ready Lesson Package</div>
        </div>
        {activeId && (
          <button className="secondary" style={{ borderColor: "var(--board-ink)", color: "var(--board-ink)" }}
                  onClick={handleDelete}>
            Delete &amp; Start New
          </button>
        )}
      </header>

      <main className="content">
        {checking ? (
          <p className="muted">Checking for an active document…</p>
        ) : !activeId ? (
          <UploadPanel onUploaded={(id) => { setActiveId(id); setReady(false); }} />
        ) : !ready ? (
          <ProgressStream documentId={activeId} onReady={() => setReady(true)} />
        ) : (
          <StageWorkspace documentId={activeId} />
        )}
      </main>
    </div>
  );
}
