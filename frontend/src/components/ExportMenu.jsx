import { api } from "../api.js";

const FORMATS = [
  { key: "json", label: "TeacherKnowledgePackage.json" },
  { key: "md", label: "Markdown" },
  { key: "html", label: "HTML" },
  { key: "pdf", label: "PDF" },
  { key: "docx", label: "Word (.docx)" },
];

export default function ExportMenu({ documentId }) {
  return (
    <div className="export-menu">
      {FORMATS.map((f) => (
        <a key={f.key} href={api.exportUrl(documentId, f.key)} target="_blank" rel="noreferrer">
          <button className="secondary">Download {f.label}</button>
        </a>
      ))}
    </div>
  );
}
