import { useState } from "react";
import { api } from "../api.js";

const DOC_TYPE_OPTIONS = [
  { value: "mostly_text", label: "Mostly Text" },
  { value: "text_tables", label: "Text with Tables" },
  { value: "text_diagrams", label: "Text with Diagrams/Figures" },
  { value: "text_equations", label: "Text with Equations" },
  { value: "scanned", label: "Scanned PDF" },
  { value: "not_sure", label: "I'm Not Sure (let the system decide)" },
];

export default function UploadPanel({ onUploaded }) {
  const [file, setFile] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [docTypeHint, setDocTypeHint] = useState("not_sure");
  const [gradeHint, setGradeHint] = useState("");
  const [teachingStyle, setTeachingStyle] = useState("");
  const [timeConstraints, setTimeConstraints] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function handleUpload() {
    if (!file) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("file", file);
    form.append("doc_type_hint", docTypeHint);
    if (gradeHint) form.append("grade_hint", gradeHint);
    if (teachingStyle) form.append("teaching_style", teachingStyle);
    if (timeConstraints) form.append("time_constraints", timeConstraints);
    try {
      const res = await api.upload(form);
      onUploaded(res.document_id);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h2 className="section-title">Upload a chapter, paper, or set of slides</h2>
      <p className="muted" style={{ marginTop: -6, marginBottom: 16 }}>
        PDF, DOCX, PPTX, or TXT. One document at a time — delete the current
        package before starting a new one.
      </p>

      <label
        className={`dropzone ${dragging ? "drag" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault(); setDragging(false);
          if (e.dataTransfer.files?.[0]) setFile(e.dataTransfer.files[0]);
        }}
      >
        <input
          type="file"
          accept=".pdf,.docx,.pptx,.ppt,.doc,.txt"
          style={{ display: "none" }}
          onChange={(e) => setFile(e.target.files?.[0] || null)}
        />
        {file ? <strong>{file.name}</strong> : <span>Drag a file here, or click to browse</span>}
      </label>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.8rem", marginTop: "1.2rem" }}>
        <div>
          <label className="muted">Document type (for parsing routing)</label>
          <select value={docTypeHint} onChange={(e) => setDocTypeHint(e.target.value)}>
            {DOC_TYPE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        <div>
          <label className="muted">Grade / audience (optional)</label>
          <input type="text" placeholder="e.g. Grade 9" value={gradeHint}
                 onChange={(e) => setGradeHint(e.target.value)} />
        </div>
        <div>
          <label className="muted">Preferred teaching style (optional)</label>
          <input type="text" placeholder="e.g. discussion-heavy, activity-based"
                 value={teachingStyle} onChange={(e) => setTeachingStyle(e.target.value)} />
        </div>
        <div>
          <label className="muted">Time constraints (optional)</label>
          <input type="text" placeholder="e.g. only 3 periods available"
                 value={timeConstraints} onChange={(e) => setTimeConstraints(e.target.value)} />
        </div>
      </div>

      {error && <p style={{ color: "var(--chalk-red)" }}>{error}</p>}

      <div style={{ marginTop: "1.2rem" }}>
        <button className="primary" disabled={!file || busy} onClick={handleUpload}>
          {busy ? "Uploading…" : "Generate Teacher Knowledge Package"}
        </button>
      </div>
    </div>
  );
}
