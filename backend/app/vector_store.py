"""
Lightweight RAG store: fastembed (ONNX Runtime, no torch) for embeddings,
SQLite (via db_models.EmbeddingChunk) for storage, numpy for cosine similarity.

Used for:
  - Grounded retrieval during generation (Stages 4-8 can pull the most relevant
    source chunks for a period/topic instead of stuffing the whole document
    into every prompt).
  - Stage 9 hallucination scoring: does generated content have a reasonably
    similar chunk somewhere in the source? Low max-similarity = ungrounded risk.

The embedding model is loaded lazily and once per process (module-level
singleton) since loading it is the single most expensive operation we do.
"""
import logging
from typing import List, Tuple

import numpy as np
from sqlalchemy.orm import Session

from .config import settings
from .db_models import EmbeddingChunk

logger = logging.getLogger("teacher_ai.vectorstore")

_model = None


def _get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        logger.info("loading_embedding_model model=%s", settings.embedding_model)
        _model = TextEmbedding(model_name=settings.embedding_model)
    return _model


def embed_texts(texts: List[str]) -> np.ndarray:
    """Returns an (n, dim) float32 array. fastembed yields a generator of
    numpy arrays; we materialize + stack + L2-normalize once so every later
    similarity computation is a plain dot product."""
    model = _get_model()
    vectors = np.array(list(model.embed(texts)), dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def ingest_chunks(db: Session, document_id: str, chunks: List[str]) -> None:
    """Embeds and stores all chunks for a document. Idempotent: clears any
    existing chunks for this document_id first (relevant on re-runs)."""
    db.query(EmbeddingChunk).filter(EmbeddingChunk.document_id == document_id).delete()
    if not chunks:
        db.commit()
        return
    vectors = embed_texts(chunks)
    for i, (text, vec) in enumerate(zip(chunks, vectors)):
        db.add(EmbeddingChunk(
            document_id=document_id,
            chunk_index=i,
            text=text,
            vector=vec.astype(np.float32).tobytes(),
        ))
    db.commit()


def _load_all(db: Session, document_id: str) -> Tuple[List[str], np.ndarray]:
    rows = (
        db.query(EmbeddingChunk)
        .filter(EmbeddingChunk.document_id == document_id)
        .order_by(EmbeddingChunk.chunk_index)
        .all()
    )
    if not rows:
        return [], np.zeros((0, settings.embedding_dim), dtype=np.float32)
    texts = [r.text for r in rows]
    matrix = np.stack([np.frombuffer(r.vector, dtype=np.float32) for r in rows])
    return texts, matrix


def retrieve(db: Session, document_id: str, query: str, k: int = 5) -> List[str]:
    """Top-k most similar source chunks to `query` for this document."""
    texts, matrix = _load_all(db, document_id)
    if not texts:
        return []
    q = embed_texts([query])[0]  # already normalized
    sims = matrix @ q  # matrix rows are normalized too -> cosine similarity
    top_idx = np.argsort(-sims)[:k]
    return [texts[i] for i in top_idx]


def max_similarity_to_source(db: Session, document_id: str, text: str) -> float:
    """Used by Stage 9: the highest cosine similarity between `text` (a piece
    of generated content) and ANY chunk of the original source. Low values
    flag possible hallucination / drift from the primary reference."""
    texts, matrix = _load_all(db, document_id)
    if not texts or not text.strip():
        return 0.0
    q = embed_texts([text])[0]
    sims = matrix @ q
    return float(np.max(sims))


def delete_document_vectors(db: Session, document_id: str) -> None:
    db.query(EmbeddingChunk).filter(EmbeddingChunk.document_id == document_id).delete()
    db.commit()
