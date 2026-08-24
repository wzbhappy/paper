from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://paper:paper@localhost:5432/paperassistant"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    neo4j_url: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"

    cors_origins: str = "http://localhost:5173"

    # ---- LLM ----
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    ollama_base_url: str = "http://localhost:11434"
    llm_default: str = "deepseek-chat"

    # 按任务覆写模型；留空则回退 llm_default。
    llm_summarize: str | None = None
    llm_translate: str | None = None
    llm_polish: str | None = None
    llm_keyword_expand: str | None = None
    llm_outline: str | None = None
    llm_review_gen: str | None = None
    llm_direction: str | None = None
    llm_chat: str | None = None

    # ---- Embedding ----
    embedding_model: str = "bge-m3"
    embedding_dim: int = 1024
    qdrant_collection: str = "paper_chunks"

    # ---- 检索源 ----
    semantic_scholar_api_key: str | None = None
    crossref_mailto: str | None = None
    """填联系邮箱可进入 Crossref polite pool，限流更宽松。"""

    # 文献存储
    storage_dir: str = "./data/papers"

    @property
    def llm_routing(self) -> dict[str, str]:
        """任务 → 模型映射。省钱任务用小模型，推理任务用大模型。"""
        return {
            "summarize": self.llm_summarize or self.llm_default,
            "translate": self.llm_translate or self.llm_default,
            "polish": self.llm_polish or self.llm_default,
            "keyword_expand": self.llm_keyword_expand or self.llm_default,
            "outline": self.llm_outline or self.llm_default,
            "review_gen": self.llm_review_gen or self.llm_default,
            "direction": self.llm_direction or self.llm_default,
            "chat": self.llm_chat or self.llm_default,
        }


settings = Settings()
