from functools import partial
import os
import asyncio
from typing import AsyncIterator
import dotenv
from lightrag import LightRAG, QueryParam
from lightrag.llm.ollama import ollama_embed, ollama_model_complete
from lightrag.kg.shared_storage import initialize_pipeline_status
from lightrag.utils import setup_logger, EmbeddingFunc


setup_logger("lightrag", level="INFO")

WORKING_DIR = "src/rag_storage"
if not os.path.exists(WORKING_DIR):
    os.mkdir(WORKING_DIR)


async def init_rag() -> LightRAG:
    rag = LightRAG(
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
    await rag.initialize_storages()
    await initialize_pipeline_status()
    return rag


async def main():
    async def aprint(query: str | AsyncIterator[str]):
        if isinstance(query, str):
            print(query, end="", flush=True)
        else:
            async for part in query:
                print(part, end="", flush=True)
        print("")

    rag = None
    try:
        # Initialize RAG instance
        rag = await init_rag()
        file = f"{WORKING_DIR}/zabbix.txt"
        with open(file, "r", encoding="utf-8") as f:
            await rag.ainsert(f.read(), file_paths=file)

        query = "What is operational status 2 in zabbix?"
        PartialQueryParam = partial(QueryParam, stream=True)
        # PartialQueryParam = partial(QueryParam, stream=True)

        # Perform naive search
        print("\n=====================")
        print("Query mode: naive")
        print("=====================")
        await aprint(await rag.aquery(query, param=PartialQueryParam(mode="naive")))

        # Perform local search
        print("\n=====================")
        print("Query mode: local")
        print("=====================")
        await aprint(await rag.aquery(query, param=PartialQueryParam(mode="local")))

       
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        print()
        if rag:
            await rag.finalize_storages()


if __name__ == "__main__":
    dotenv.load_dotenv()
    asyncio.run(main())
