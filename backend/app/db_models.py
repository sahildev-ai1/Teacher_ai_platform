import uuid
import datetime as dt

from sqlalchemy import Column, String, Integer, Float, Text, DateTime, ForeignKey, JSON, LargeBinary
from sqlalchemy.orm import relationship

from .database import Base


def _uid() -> str:
    return uuid.uuid4().hex


class Document(Base):
    """A single uploaded source document and everything derived from it."""
    __tablename__ = "documents"

    id = Column(String, primary_key=True, default=_uid)
    filename = Column(String, nullable=False)
    file_type = Column(String, nullable=False)          # pdf | docx | pptx | txt
    doc_type_hint = Column(String, default="not_sure")  # user-supplied routing hint
    raw_text_path = Column(String)                       # where extracted text lives
    structure_json = Column(JSON)                        # headings/sections/tables/figures
    classification_json = Column(JSON)                   # Stage 2 output
    knowledge_json = Column(JSON)                         # Stage 3 output
    status = Column(String, default="uploaded")
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    stages = relationship("StageOutput", back_populates="document", cascade="all, delete-orphan")
    chats = relationship("ChatMessage", back_populates="document", cascade="all, delete-orphan")


class StageOutput(Base):
    """One row per pipeline stage per document. Stores the JSON payload for that
    stage plus a lightweight state machine (pending -> generated -> approved)."""
    __tablename__ = "stage_outputs"

    id = Column(String, primary_key=True, default=_uid)
    document_id = Column(String, ForeignKey("documents.id"))
    stage = Column(String, nullable=False)  # e.g. "stage4_teaching_plan"
    content_json = Column(JSON)
    status = Column(String, default="pending")  # pending | generated | approved
    validation_json = Column(JSON)
    version = Column(Integer, default=1)
    updated_at = Column(DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow)

    document = relationship("Document", back_populates="stages")


class ChatMessage(Base):
    """Interactive refinement chat history, scoped per (document, stage)."""
    __tablename__ = "chat_messages"

    id = Column(String, primary_key=True, default=_uid)
    document_id = Column(String, ForeignKey("documents.id"))
    stage = Column(String, nullable=False)
    role = Column(String, nullable=False)  # user | assistant | system
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    document = relationship("Document", back_populates="chats")


class EmbeddingChunk(Base):
    """Lightweight, single-file replacement for a separate vector DB (Chroma/
    Pinecone). Vectors are stored as raw float32 bytes (via numpy.tobytes()) so
    a 384-dim MiniLM vector costs ~1.5KB instead of ~4KB as JSON. Retrieval is
    done in-process with numpy cosine similarity -- fine at chapter-sized chunk
    counts (tens to low hundreds of rows) and removes an entire dependency
    (chromadb + its onnxruntime transitive deps) from the Render build."""
    __tablename__ = "embedding_chunks"

    id = Column(String, primary_key=True, default=_uid)
    document_id = Column(String, ForeignKey("documents.id"), index=True)
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    vector = Column(LargeBinary, nullable=False)  # float32 bytes, dim = settings.embedding_dim
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class PipelineRun(Base):
    """Tracks progress of a full/partial pipeline run for the streaming API."""
    __tablename__ = "pipeline_runs"

    id = Column(String, primary_key=True, default=_uid)
    document_id = Column(String, ForeignKey("documents.id"))
    current_stage = Column(String)
    progress = Column(Integer, default=0)
    state = Column(String, default="running")  # running | waiting_user | done | error
    message = Column(Text)
    updated_at = Column(DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow)
