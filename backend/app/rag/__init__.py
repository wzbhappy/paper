"""向量检索 + RAG：Qdrant 混合检索，生成任务带引用溯源。"""

from app.rag.embedder import (
    Embedder,
    HashEmbedder,
    OllamaEmbedder,
    OpenAIEmbedder,
    get_embedder,
    set_embedder,
)
from app.rag.retriever import (
    RetrievedChunk,
    build_context,
    delete_paper_vectors,
    index_chunks,
    retrieve,
)
from app.rag.store import (
    InMemoryVectorStore,
    QdrantVectorStore,
    SearchHit,
    VectorRecord,
    VectorStore,
    get_vector_store,
    make_point_id,
    set_vector_store,
)

__all__ = [
    "Embedder",
    "HashEmbedder",
    "InMemoryVectorStore",
    "OllamaEmbedder",
    "OpenAIEmbedder",
    "QdrantVectorStore",
    "RetrievedChunk",
    "SearchHit",
    "VectorRecord",
    "VectorStore",
    "build_context",
    "delete_paper_vectors",
    "get_embedder",
    "get_vector_store",
    "index_chunks",
    "make_point_id",
    "retrieve",
    "set_embedder",
    "set_vector_store",
]
