"""
Vector store: Qdrant Cloud, using server-side Cloud Inference.

Why this replaced the old fastembed/ONNX approach: fastembed still needed to
download a ~90MB model onto Render's disk on every cold start (Render's free
tier disk is small and ephemeral) and load onnxruntime into the same 512MB-RAM
process as everything else -- that combination is what ran the instance out
of space right after Stage 3 finished and Stage 1's embeddings kicked in.

Qdrant Cloud's free tier (cloud.qdrant.io -- 1GB RAM, 4GB disk, permanently
free, no credit card) computes embeddings *inside Qdrant's own cluster* when
you pass a `models.Document(text=..., model=...)` instead of a raw vector --
no local model, no download, no onnxruntime dependency at all.

All documents share ONE collection (`settings.qdrant_collection`); each point
is tagged with a `document_id` payload field and every read/write is scoped
with a filter on it. Given the single-document-at-a-time lock, only one
document's points ever exist at once in practice, but filtering is still
correct/defensive rather than relying on that.

If QDRANT_URL isn't configured, every function here degrades gracefully
(logs a warning, returns empty/neutral results) rather than crashing the
pipeline -- grounding/hallucination checks just come back uninformative
until Qdrant is set up.
"""
import logging
import uuid
from typing import List, Optional

from qdrant_client import QdrantClient, models
from sqlalchemy.orm import Session

from .config import settings

logger = logging.getLogger("teacher_ai.vectorstore")

_client: Optional[QdrantClient] = None
_collection_ready = False


def is_configured() -> bool:
    return bool(settings.qdrant_url)


def _get_client() -> QdrantClient:
    global _client, _collection_ready
    if _client is None:
        logger.info("connecting_qdrant url=%s", settings.qdrant_url)
        _client = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
            cloud_inference=True,
            timeout=settings.qdrant_timeout_seconds,
        )
    if not _collection_ready:
        if not _client.collection_exists(settings.qdrant_collection):
            logger.info("creating_qdrant_collection name=%s", settings.qdrant_collection)
            _client.create_collection(
                collection_name=settings.qdrant_collection,
                vectors_config=models.VectorParams(
                    size=settings.embedding_dim, distance=models.Distance.COSINE
                ),
            )
        # Qdrant requires an explicit payload index before a field can be
        # used in a filter -- every ingest_chunks/retrieve/max_similarity_to_
        # source/delete_document_vectors call below filters on document_id,
        # so without this every one of them 400s with "Index required but
        # not found for document_id". Safe to call unconditionally (even on
        # a collection that already existed): it also self-heals a
        # collection created before this fix existed, like the one already
        # sitting on your cluster from an earlier run.
        try:
            _client.create_payload_index(
                collection_name=settings.qdrant_collection,
                field_name="document_id",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("qdrant_index_creation_failed error=%s", exc)
        _collection_ready = True
    return _client


def healthcheck() -> None:
    """Cheap connectivity check -- ensures the collection/index exist without
    touching any document's data. Meant to be called BEFORE the expensive
    Stage 1-3 work starts, so a broken Qdrant setup (wrong URL, bad API key,
    cluster unreachable) fails fast in seconds instead of after burning
    through minutes of LLM calls. Raises on failure; callers decide what to
    do with that (see orchestrator.run_intelligence_pipeline)."""
    if not is_configured():
        return
    _get_client()


def _doc_filter(document_id: str) -> models.Filter:
    return models.Filter(
        must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))]
    )


def _point_id(document_id: str, chunk_index: int) -> str:
    """Qdrant point IDs must be an unsigned integer or a valid UUID -- a plain
    string like "{document_id}-{i}" gets rejected with a 400. uuid5 gives a
    deterministic, valid-format UUID derived from the same inputs, so this
    stays idempotent (re-ingesting the same document_id/chunk_index always
    maps to the same point) without needing to track IDs anywhere else."""
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"{document_id}:{chunk_index}"))


def _batched(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def ingest_chunks(db: Session, document_id: str, chunks: List[str]) -> None:
    """Embeds and stores all chunks for a document via Qdrant Cloud Inference.
    Idempotent: clears any existing points for this document_id first.
    Upserts are batched (settings.qdrant_upsert_batch_size) rather than sent
    as one request -- Cloud Inference embeds every chunk server-side as part
    of the request, so a large document's full chunk set in a single upsert
    can take long enough to hit a timeout even with a generous client-side
    limit. `db` is accepted (unused) purely to keep the call signature
    identical to the previous SQLite-backed implementation -- orchestrator.py
    doesn't need to change."""
    if not is_configured():
        logger.warning("qdrant_not_configured skip_ingest document_id=%s", document_id)
        return
    if not chunks:
        return
    client = _get_client()
    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=models.FilterSelector(filter=_doc_filter(document_id)),
    )
    points = [
        models.PointStruct(
            id=_point_id(document_id, i),
            vector=models.Document(text=chunk, model=settings.embedding_model),
            payload={"document_id": document_id, "chunk_index": i, "text": chunk},
        )
        for i, chunk in enumerate(chunks)
    ]
    total_batches = (len(points) + settings.qdrant_upsert_batch_size - 1) // settings.qdrant_upsert_batch_size
    for batch_num, batch in enumerate(_batched(points, settings.qdrant_upsert_batch_size), start=1):
        logger.info("qdrant_upsert_batch document_id=%s batch=%d/%d size=%d",
                    document_id, batch_num, total_batches, len(batch))
        client.upsert(collection_name=settings.qdrant_collection, points=batch)


def retrieve(db: Session, document_id: str, query: str, k: int = 5) -> List[str]:
    """Top-k most similar source chunks to `query` for this document."""
    if not is_configured():
        return []
    client = _get_client()
    result = client.query_points(
        collection_name=settings.qdrant_collection,
        query=models.Document(text=query, model=settings.embedding_model),
        query_filter=_doc_filter(document_id),
        limit=k,
        with_payload=True,
    )
    return [p.payload["text"] for p in result.points]


def max_similarity_to_source(db: Session, document_id: str, text: str) -> float:
    """Used by Stage 9: the highest cosine similarity between `text` (a piece
    of generated content) and ANY chunk of the original source. Low values
    flag possible hallucination / drift from the primary reference. Returns
    0.0 (neutral/unknown, not "definitely hallucinated") if Qdrant isn't
    configured or there's nothing to compare against."""
    if not is_configured() or not text.strip():
        return 0.0
    client = _get_client()
    result = client.query_points(
        collection_name=settings.qdrant_collection,
        query=models.Document(text=text, model=settings.embedding_model),
        query_filter=_doc_filter(document_id),
        limit=1,
        with_payload=False,
    )
    return float(result.points[0].score) if result.points else 0.0


def delete_document_vectors(db: Session, document_id: str) -> None:
    if not is_configured():
        return
    client = _get_client()
    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=models.FilterSelector(filter=_doc_filter(document_id)),
    )