import asyncio
import os
import dotenv
import asyncpg
from functools import partial
from lightrag import LightRAG
from lightrag.utils import setup_logger, EmbeddingFunc
from lightrag.llm.ollama import ollama_embed, ollama_model_complete

setup_logger("lightrag", level="INFO")

WORKING_DIR = "./rag_storage"

async def fetch_doc_ids():
    conn = await asyncpg.connect(
        user=os.getenv("PGUSER", "ryan"),
        password=os.getenv("PGPASSWORD", "admin"),
        database=os.getenv("PGDATABASE", "ryan"),
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", 5432))
    )
    rows = await conn.fetch("SELECT id FROM lightrag_doc_full")
    await conn.close()
    return [r["id"] for r in rows]

async def init_rag() -> LightRAG:
    return LightRAG(
        working_dir=WORKING_DIR,
        llm_model_name=os.getenv("LLM_MODEL", "qwen2.5:14b"),
        llm_model_kwargs={
            "host": os.getenv("LLM_BINDING_HOST", "http://localhost:11434"),
            "options": {"num_ctx": int(os.getenv("MAX_TOKENS", 32768))},
        },
        embedding_func=EmbeddingFunc(
            embedding_dim=int(os.getenv("EMBEDDING_DIM", 1024)),
            max_token_size=int(os.getenv("MAX_EMBED_TOKENS", 8192)),
            func=partial(
                ollama_embed,
                embed_model=os.getenv("EMBEDDING_MODEL", "bge-m3:latest"),
                host=os.getenv("EMBEDDING_BINDING_HOST", "http://localhost:11434"),
            ),
        ),
        llm_model_func=ollama_model_complete,
        enable_llm_cache_for_entity_extract=True,
        enable_llm_cache=False,
        kv_storage="PGKVStorage",
        doc_status_storage="PGDocStatusStorage",
        graph_storage="PGGraphStorage",
        vector_storage="PGVectorStorage",
    )

async def choose_and_delete_doc():
    dotenv.load_dotenv()
    doc_ids = await fetch_doc_ids()

    if not doc_ids:
        print("No documents found.")
        return

    print("\nAvailable Document IDs:")
    for i, doc_id in enumerate(doc_ids):
        print(f"{i + 1}. {doc_id}")

    choice = input("\nEnter the number of the document to delete: ")

    try:
        idx = int(choice) - 1
        selected_id = doc_ids[idx]
    except (ValueError, IndexError):
        print("❌ Invalid selection.")
        return

    rag = await init_rag()
    await rag.initialize_storages()

    try:
        print(f"\nDeleting document with ID: {selected_id}")
        await rag.adelete_by_doc_id(selected_id)
        print("✅ Document deleted successfully.")
    except Exception as e:
        print(f"❌ Failed to delete document: {e}")
    finally:
        await rag.finalize_storages()

if __name__ == "__main__":
    asyncio.run(choose_and_delete_doc())
