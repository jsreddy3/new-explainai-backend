from pydantic_settings import BaseSettings
from pathlib import Path
from typing import Optional

class Settings(BaseSettings):
    # Server
    API_BASE_URL: str = "http://localhost:8000"  # Will be overridden in production
    ENVIRONMENT: str = "development"  # development, staging, production
    CORS_ORIGINS: list = [
        "http://localhost:3000",
        "https://explainai-new-528ec8eb814a.herokuapp.com",
        "https://explainai-new.vercel.app"
    ]  # Will be overridden in production
    PORT: int = 8000  # Will be overridden by Heroku
    
    # Security
    ALLOW_CREDENTIALS: bool = True
    ALLOWED_METHODS: list = ["*"]
    ALLOWED_HEADERS: list = ["*"]
    
    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./explainai.db"  # Will be overridden in productionx
    
    # Document Processing
    MAX_DOCUMENT_SIZE: int = 10 * 1024 * 1024  # 10MB
    DEFAULT_CHUNK_SIZE: int = 50000
    MAX_CHUNKS_PER_DOC: int = 100
    SUPPORTED_MIME_TYPES: list = ["application/pdf"]
    EXAMPLE_DOCUMENT_IDS: list = [
        "2ee4de4e-2813-47d5-bbea-1863a5d86242",  # Declaration of Independence - SOEMTHING'S WRONG HERE LOL
        "03db4281-0ccf-4ad7-9b54-a17698e28b7a",  # How is Inflation Measured?
        "e5dd251a-3838-48e6-b1ab-cab830b0e892",  # Lord of the Rings
        "68354321-f13b-4acd-9c1c-14d08dc7a9f5",  # Stopping by the Woods on a Swowy Evening
        "3ca86b77-c1f3-42d2-b9f0-4e88246d5c61", #Declaration of Independence
    ]

    # API Keys
    LLAMA_CLOUD_API_KEY: str
    GEMINI_API_KEY: str
    ANTHROPIC_API_KEY: str
    OPENAI_API_KEY: str
    DEEPGRAM_API_KEY: str
    CARTESIA_API_KEY: str
    DEEPINFRA_TOKEN: str
    
    # Authentication
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24
    
    # Optional Supabase settings
    SUPABASE_URL: Optional[str] = None
    SUPABASE_SERVICE_ROLE_KEY: Optional[str] = None
    
    # Paths
    BASE_DIR: Path = Path(__file__).parent.parent.parent
    UPLOAD_DIR: Path = BASE_DIR / "uploads"
    
    def get_cors_origins(self) -> list:
        """Get CORS origins based on environment"""
        if self.ENVIRONMENT == "production":
            # In production, use actual frontend URL(s)
            return self.CORS_ORIGINS
        # In development, allow localhost
        return ["http://localhost:3000", "http://127.0.0.1:3000"]
    
    class Config:
        env_file = ".env"
        extra = "allow"

# Create settings instance
settings = Settings()