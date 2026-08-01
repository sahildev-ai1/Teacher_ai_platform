import { useEffect, useState, useRef } from "react";
import { api } from "../api.js";

const STAGE_LABELS = {
  stage1_document_intelligence: "Stage 1 · Document Intelligence",
  stage2_classification: "Stage 2 · Educational Classification",
  stage3_knowledge_extraction: "Stage 3 · Knowledge Extraction",
  stage4_teaching_plan: "Stage 4 · Teaching Planner",
};

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

  return (
    <div className="card">
      <h2 className="section-title">Reading your document…</h2>
      <p className="muted">{STAGE_LABELS[state.current_stage] || state.current_stage}</p>
      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${state.progress || 0}%` }} />
      </div>
      <p style={{ marginTop: "0.7rem" }}>{state.message}</p>
      {state.state === "error" && (
        <p className="pill warning">Pipeline error — check the backend logs, then delete and retry.</p>
      )}
    </div>
  );
}
