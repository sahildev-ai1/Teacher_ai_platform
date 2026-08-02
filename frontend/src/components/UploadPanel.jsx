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

const HOW_IT_WORKS = [
  {
    title: "Upload",
    text: "Any PDF, DOCX, PPTX, or plain-text chapter — any subject, any grade level.",
  },
  {
    title: "AI reads & plans",
    text: "Classifies the topic, extracts the concepts, and drafts a teaching plan sized to the content.",
  },
  {
    title: "You review & refine",
    text: "Give feedback in plain English on any stage and it's revised in place, not restarted.",
  },
  {
    title: "Publish & export",
    text: "Download the finished package as JSON, Markdown, HTML, PDF, or Word.",
  },
];

const FEATURE_BADGES = [
  "Grounded in your source document",
  "Adapts to any subject & grade",
  "You approve every stage",
  "One document at a time",
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
    <div>
      <div className="hero">
        <span className="eyebrow">AI Lesson Design, Grounded in Your Source</span>
        <h1>Turn any chapter into a classroom-ready lesson</h1>
        <p className="lede">
          Upload a textbook chapter, research paper, or slide deck. You'll get a
          complete teaching plan — objectives, activities, assessments, and a
          misconception analysis — built from what's actually in your document,
          with you reviewing and refining every stage before it's final.
        </p>
      </div>

      <div className="how-it-works">
        {HOW_IT_WORKS.map((step, i) => (
          <div className="how-step" key={step.title}>
            <span className="step-number">{i + 1}</span>
            <h3>{step.title}</h3>
            <p>{step.text}</p>
          </div>
        ))}
      </div>

      <div className="feature-badges">
        {FEATURE_BADGES.map((label) => (
          <span className="feature-badge" key={label}>{label}</span>
        ))}
      </div>

      <div className="card">
        <h2 className="section-title">Upload a chapter, paper, or set of slides</h2>
        <p className="muted" style={{ marginTop: -6, marginBottom: 16 }}>
          PDF, DOCX, PPTX, or TXT — one document at a time. Use the "Delete &amp;
          Start New" button in the header to clear the current package first.
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

        <div style={{ marginTop: "1.2rem", display: "flex", alignItems: "center", gap: "0.9rem" }}>
          <button className="primary" disabled={!file || busy} onClick={handleUpload}>
            {busy ? "Uploading…" : "Generate Teacher Knowledge Package"}
          </button>
          {!file && <span className="muted">Choose a file above to enable this.</span>}
        </div>
      </div>
    </div>
  );
}
