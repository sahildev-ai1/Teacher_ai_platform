"""
Central configuration. Every tunable lives here and is overridable via env vars / .env file.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Storage ---
    # Everything (structured data + embedding vectors) lives in ONE sqlite file.
    # Deliberately not using a second persistence system (e.g. Chroma's own
    # on-disk store) -- fewer moving parts to fit on Render's free instance,
    # and a single file is trivial to wipe via the "delete everything" flow.
    sqlite_path: str = "./data/tkp.db"
    upload_dir: str = "./data/uploads"
    export_dir: str = "./data/exports"

    # --- Single-document lock ---
    # Free-tier deploys have one small instance and no auth/multi-tenancy, so
    # we hard-limit the app to ONE active document at a time. A new upload is
    # rejected until the user explicitly deletes the current one.
    enforce_single_document: bool = True

    # --- Ollama (local or cloud) ---
    ollama_host: str = ""
    ollama_cloud_url: str = "https://ollama.com/api/chat"
    ollama_api_key: str = ""
    ollama_model: str = "gemma4:31b-cloud"
    # Cheaper/faster model for high-volume, low-stakes calls (routing, per-chunk
    # extraction map steps) vs. the main model for final synthesis/generation.
    ollama_fast_model: str = "gemma4:cloud"

    # --- Vector store: Qdrant Cloud (server-side Cloud Inference) ---
    # Free tier: https://cloud.qdrant.io -- 1GB RAM / 4GB disk, permanently free,
    # no credit card. Leave qdrant_url blank to run with grounding/hallucination
    # checks soft-disabled (the rest of the pipeline still works) until you set
    # one up.
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = "document_chunks"
    # Cloud Inference computes embeddings server-side inside Qdrant's cluster,
    # which is slower than a plain vector upsert -- a large document's full
    # chunk set in one request can outrun a short default timeout. Both of
    # these exist to tune that: a generous per-request timeout, and batching
    # so no single request has to embed too many chunks at once.
    qdrant_timeout_seconds: int = 60
    qdrant_upsert_batch_size: int = 16
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"  # a free Qdrant Cloud Inference model
    embedding_dim: int = 384

    # --- Chunking ---
    chunk_size: int = 1200
    chunk_overlap: int = 150

    # --- Validation ---
    hallucination_similarity_threshold: float = 0.35  # min cosine sim to source

    # --- Optional pedagogy enrichment (never used for factual content) ---
    tavily_api_key: str = ""

    # --- CORS ---
    frontend_origin: str = "http://localhost:5173"


settings = Settings()
