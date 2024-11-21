from pydantic_settings import BaseSettings
from pathlib import Path
from typing import Optional

class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./explainai.db"  # Changed to async SQLite
    
    # Document Processing
    MAX_DOCUMENT_SIZE: int = 10 * 1024 * 1024  # 10MB
    DEFAULT_CHUNK_SIZE: int = 50000
    MAX_CHUNKS_PER_DOC: int = 100
    SUPPORTED_MIME_TYPES: list = ["application/pdf"]
    
    # API Keys
    LLAMA_CLOUD_API_KEY: str
    GEMINI_API_KEY: str
    ANTHROPIC_API_KEY: str
    OPENAI_API_KEY: str
    DEEPGRAM_API_KEY: str
    CARTESIA_API_KEY: str
    DEEPINFRA_TOKEN: str
    
    # New Supabase settings
    SUPABASE_URL: Optional[str] = None
    SUPABASE_SERVICE_ROLE_KEY: Optional[str] = None
    
    # Paths
    BASE_DIR: Path = Path(__file__).parent.parent.parent
    UPLOAD_DIR: Path = BASE_DIR / "uploads"
    
    class Config:
        env_file = ".env"
        # Allow extra values in case we add more API keys later
        extra = "allow"

# Create settings instance
settings = Settings()