import { useEffect, useState } from "react";
import { api } from "../api.js";
import ExportMenu from "./ExportMenu.jsx";

const STAGES = [
  { key: "stage4_teaching_plan", label: "4 · Teaching Plan" },
  { key: "stage5_classroom_content", label: "5 · Classroom Content" },
  { key: "stage6_activities", label: "6 · Activities" },
  { key: "stage7_assessments", label: "7 · Assessments" },
  { key: "stage8_learning_gaps", label: "8 · Learning Gaps" },
];

function PeriodDetails({ periodNumber, summary, defaultOpen, children }) {
  return (
    <details open={defaultOpen} style={{ marginBottom: "0.9rem", border: "1px solid var(--rule)", borderRadius: 4, padding: "0.6rem 0.9rem", background: "#fff" }}>
      <summary style={{ cursor: "pointer", fontWeight: 600, color: "var(--board)" }}>
        Period {periodNumber} {summary ? `— ${summary}` : ""}
      </summary>
      <div style={{ marginTop: "0.7rem" }}>{children}</div>
    </details>
  );
}

function StageContentView({ stageKey, content }) {
  if (!content) return null;

  if (stageKey === "stage4_teaching_plan") {
    return (
      <div>
        <p className="muted">{content.rationale}</p>
        {content.periods?.map((p) => (
          <div key={p.period_number} style={{ marginBottom: "0.8rem" }}>
            <strong>Period {p.period_number}: {p.title}</strong> — {p.duration_minutes} min
            <div className="muted">{p.objectives?.join(", ")}</div>
          </div>
        ))}
      </div>
    );
  }

  if (stageKey === "stage5_classroom_content") {
    const periods = content.periods || [];
    return periods.map((pc) => (
      <PeriodDetails key={pc.period_number} periodNumber={pc.period_number} defaultOpen={periods.length <= 2}>
        <p><em>Entry ticket:</em> {pc.entry_ticket}</p>
        <p><em>Teacher script:</em> {pc.teacher_script}</p>
        <p><em>Blackboard notes:</em> {pc.blackboard_notes}</p>
        <p><em>Classroom activity:</em> {pc.classroom_activity_summary}</p>
        <p><em>Checkpoint questions:</em></p>
        <ul>{pc.checkpoint_questions?.map((q, i) => <li key={i}>{q}</li>)}</ul>
        <p><em>Exit ticket:</em> {pc.exit_ticket}</p>
        <p><em>Homework:</em> {pc.homework}</p>
        <p><em>Mentor moment:</em> {pc.mentor_moment}</p>
      </PeriodDetails>
    ));
  }

  if (stageKey === "stage6_activities") {
    const activities = content.activities || [];
    return activities.map((a, i) => (
      <PeriodDetails key={i} periodNumber={a.period_number}
                     summary={`${a.title} (${a.type}, ${a.duration_minutes} min)`}
                     defaultOpen={activities.length <= 2}>
        <p><em>Materials:</em> {a.materials?.join(", ")}</p>
        <p><em>Instructions:</em></p>
        <ol>{a.instructions?.map((s, j) => <li key={j}>{s}</li>)}</ol>
        <p><em>Success criteria:</em></p>
        <ul>{a.success_criteria?.map((s, j) => <li key={j}>{s}</li>)}</ul>
      </PeriodDetails>
    ));
  }

  if (stageKey === "stage7_assessments") {
    const assessments = content.assessments || [];
    return assessments.map((a, i) => (
      <PeriodDetails key={i} periodNumber={a.period_number}
                     summary={`${a.mcqs?.length || 0} MCQs, ${a.written_questions?.length || 0} written questions`}
                     defaultOpen={assessments.length <= 2}>
        {a.mcqs?.map((mcq, qi) => (
          <div key={`mcq-${qi}`} style={{ marginBottom: "0.9rem" }}>
            <p style={{ marginBottom: "0.3rem" }}><strong>{qi + 1}. {mcq.question}</strong></p>
            <ul style={{ listStyle: "none", paddingLeft: "1rem", margin: "0 0 0.3rem" }}>
              {mcq.options?.map((opt, oi) => (
                <li key={oi} style={{ color: oi === mcq.correct_index ? "#3d6b3f" : "inherit", fontWeight: oi === mcq.correct_index ? 600 : 400 }}>
                  {String.fromCharCode(97 + oi)}) {opt}{oi === mcq.correct_index ? "  ✓" : ""}
                </li>
              ))}
            </ul>
            <p className="muted" style={{ fontSize: "0.85rem", margin: 0 }}>{mcq.explanation}</p>
          </div>
        ))}
        {a.written_questions?.map((wq, qi) => (
          <div key={`wq-${qi}`} style={{ marginBottom: "0.9rem" }}>
            <p style={{ marginBottom: "0.3rem" }}><strong>({wq.type})</strong> {wq.question}</p>
            <p className="muted" style={{ margin: "0 0 0.15rem" }}><em>Answer key:</em> {wq.answer_key}</p>
            <p className="muted" style={{ margin: 0 }}><em>Rubric:</em> {wq.rubric}</p>
          </div>
        ))}
      </PeriodDetails>
    ));
  }

  if (stageKey === "stage8_learning_gaps") {
    return content.gaps?.map((g, i) => (
      <div key={i} style={{ marginBottom: "0.9rem" }}>
        <span className={`pill ${g.severity === "high" ? "warning" : "ok"}`}>{g.severity}</span>{" "}
        <strong>{g.misconception}</strong>
        <p className="muted" style={{ margin: "0.3rem 0" }}><em>Diagnostic question:</em> {g.diagnostic_question}</p>
        <p className="muted" style={{ margin: 0 }}><em>Remedial action:</em> {g.remedial_action}</p>
      </div>
    ));
  }
  return <pre>{JSON.stringify(content, null, 2)}</pre>;
}

export default function StageWorkspace({ documentId }) {
  const [doc, setDoc] = useState(null);
  const [currentStage, setCurrentStage] = useState(STAGES[0].key);
  const [chat, setChat] = useState([]);
  const [chatInput, setChatInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [published, setPublished] = useState(null);

  async function refresh() {
    const d = await api.getDocument(documentId);
    setDoc(d);
    const firstUnapproved = STAGES.find((s) => d.stages[s.key]?.status !== "approved");
    setCurrentStage(firstUnapproved ? firstUnapproved.key : STAGES[STAGES.length - 1].key);
  }

  useEffect(() => { refresh(); }, [documentId]); // eslint-disable-line

  useEffect(() => {
    if (!documentId || !currentStage) return;
    api.getChat(documentId, currentStage).then(setChat).catch(() => setChat([]));
  }, [documentId, currentStage]);

  if (!doc) return <div className="card">Loading…</div>;

  const stageInfo = doc.stages[currentStage] || {};
  const isApproved = stageInfo.status === "approved";
  const stageIndex = STAGES.findIndex((s) => s.key === currentStage);
  const isUnlocked = stageIndex === 0 || doc.stages[STAGES[stageIndex - 1].key]?.status === "approved";

  async function generate(feedback) {
    setBusy(true);
    try {
      await api.generateStage(documentId, currentStage, feedback ? { feedback } : {});
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  async function sendFeedback() {
    if (!chatInput.trim()) return;
    setBusy(true);
    try {
      await api.sendChat(documentId, currentStage, chatInput);
      setChatInput("");
      const [d, c] = await Promise.all([api.getDocument(documentId), api.getChat(documentId, currentStage)]);
      setDoc(d);
      setChat(c);
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    setBusy(true);
    try {
      await api.approveStage(documentId, currentStage);
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  async function copyContent() {
    await navigator.clipboard.writeText(JSON.stringify(stageInfo.content, null, 2));
  }

  async function handlePublish() {
    setBusy(true);
    try {
      const pkg = await api.publish(documentId);
      setPublished(pkg);
    } finally {
      setBusy(false);
    }
  }

  const allApproved = STAGES.every((s) => doc.stages[s.key]?.status === "approved");

  return (
    <div>
      <div className="card">
        <h2 className="section-title">{doc.classification?.topic}</h2>
        <p className="muted">
          {doc.classification?.subject} · Grade {doc.classification?.grade} · {doc.classification?.difficulty}
        </p>
      </div>

      <div className="stage-stepper">
        {STAGES.map((s, i) => {
          const status = doc.stages[s.key]?.status;
          const unlocked = i === 0 || doc.stages[STAGES[i - 1].key]?.status === "approved";
          return (
            <div
              key={s.key}
              className={`stage-tab ${status === "approved" ? "approved" : ""} ${currentStage === s.key ? "active" : ""} ${!unlocked ? "locked" : ""}`}
              onClick={() => unlocked && setCurrentStage(s.key)}
            >
              {s.label}
            </div>
          );
        })}
      </div>

      {!isUnlocked ? (
        <div className="card"><p className="muted">Approve the previous stage to unlock this one.</p></div>
      ) : (
        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.8rem" }}>
            <h2 className="section-title" style={{ margin: 0 }}>
              {STAGES.find((s) => s.key === currentStage)?.label}
            </h2>
            {stageInfo.content && <button className="ghost" onClick={copyContent}>Copy JSON</button>}
          </div>

          {!stageInfo.content ? (
            <button className="primary" disabled={busy} onClick={() => generate()}>
              {busy ? "Generating…" : "Generate"}
            </button>
          ) : (
            <>
              <StageContentView stageKey={currentStage} content={stageInfo.content} />

              <hr style={{ border: "none", borderTop: "1px solid var(--rule)", margin: "1rem 0" }} />
              <p className="muted" style={{ marginBottom: "0.4rem" }}>
                Not quite right? Tell it what to change:
              </p>
              <div className="chat-panel">
                {chat.map((m, i) => (
                  <div key={i} className={`chat-msg ${m.role}`}>{m.content}</div>
                ))}
              </div>
              <div style={{ display: "flex", gap: "0.5rem" }}>
                <input type="text" placeholder="e.g. make period 2 more activity-based"
                       value={chatInput} onChange={(e) => setChatInput(e.target.value)}
                       onKeyDown={(e) => e.key === "Enter" && sendFeedback()} />
                <button className="secondary" disabled={busy} onClick={sendFeedback}>Send</button>
              </div>

              <div style={{ marginTop: "1.2rem" }}>
                <button className="primary" disabled={busy || isApproved} onClick={approve}>
                  {isApproved ? "Approved ✓" : "Approve & Continue"}
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {allApproved && (
        <div className="card">
          <h2 className="section-title">Publish Teacher Knowledge Package</h2>
          {!published ? (
            <button className="primary" disabled={busy} onClick={handlePublish}>
              {busy ? "Validating & Publishing…" : "Run Validation & Publish"}
            </button>
          ) : (
            <>
              <p>
                Validation: <span className={`pill ${published.validation.passed ? "ok" : "warning"}`}>
                  {published.validation.passed ? "Passed" : "Review flagged issues"}
                </span>{" "}
                · Hallucination risk score: {published.validation.hallucination_score}
              </p>
              <ExportMenu documentId={documentId} />
            </>
          )}
        </div>
      )}
    </div>
  );
}
