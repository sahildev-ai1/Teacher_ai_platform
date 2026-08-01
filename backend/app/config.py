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

    # --- Embeddings ---
    # fastembed (ONNX Runtime, no torch). Quantized MiniLM: ~90MB on disk,
    # comfortably fits a 512MB Render free instance alongside FastAPI/uvicorn.
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
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
