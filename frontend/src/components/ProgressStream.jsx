import { useEffect, useState, useRef } from "react";
import { api } from "../api.js";

const STAGE_LABELS = {
  preflight: "Checking connections…",
  stage1_document_intelligence: "Stage 1 · Document Intelligence",
  stage2_classification: "Stage 2 · Educational Classification",
  stage3_knowledge_extraction: "Stage 3 · Knowledge Extraction",
  stage4_teaching_plan: "Stage 4 · Teaching Planner",
};

// Once we hit these stages, the LLM calls get slow (a real chapter's worth
// of concept extraction can take 20-30 minutes). The work happens in a
// background task on the server, entirely independent of this browser tab
// being open, so it's genuinely safe to close it and check back later.
const LONG_RUNNING_STAGES = new Set(["stage2_classification", "stage3_knowledge_extraction"]);

export default function ProgressStream({ documentId, onReady }) {
  const [state, setState] = useState({ state: "running", progress: 0, message: "Starting…" });
  const readyFired = useRef(false);

  useEffect(() => {
    const es = new EventSource(api.streamUrl(documentId));
    es.onmessage = (evt) => {
      const data = JSON.parse(evt.data);
      setState(data);
      if (data.state === "waiting_user" && !readyFired.current) {
        readyFired.current = true;
        es.close();
        onReady();
      }
      if (data.state === "error" || data.state === "done") es.close();
    };
    es.onerror = () => es.close();
    return () => es.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId]);

  const isLongRunning = LONG_RUNNING_STAGES.has(state.current_stage);

  return (
    <div className="card">
      <h2 className="section-title">Reading your document…</h2>
      <p className="muted">{STAGE_LABELS[state.current_stage] || state.current_stage}</p>
      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${state.progress || 0}%` }} />
      </div>
      <p style={{ marginTop: "0.7rem" }}>{state.message}</p>

      {isLongRunning && state.state !== "error" && (
        <div
          style={{
            marginTop: "1rem",
            padding: "0.9rem 1rem",
            background: "#fdf6e8",
            border: "1px solid var(--chalk-amber)",
            borderRadius: 6,
          }}
        >
          <strong style={{ color: "var(--board)" }}>You don't need to keep this tab open.</strong>
          <p className="muted" style={{ margin: "0.35rem 0 0" }}>
            This step reads and understands the whole document, which can take{" "}
            <strong>20–30 minutes</strong> for a longer chapter. It's running on the
            server, not in this browser tab — feel free to close it and come back
            later. Reopening the app and reloading this page will pick up right
            where it left off.
          </p>
        </div>
      )}

      {state.state === "error" && (
        <p className="pill warning">Pipeline error — check the backend logs, then delete and retry.</p>
      )}
    </div>
  );
}
