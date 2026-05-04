"""
Chat engine factory.
Creates a CondensePlusContextChatEngine backed by a per-PDF FAISS index.
"""

import os
import time
import logging
from dotenv import load_dotenv
from llama_index.core import (
    StorageContext,
    load_index_from_storage,
    Settings,
    SummaryIndex,
)
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.groq import Groq
from llama_index.core.tools import QueryEngineTool, ToolMetadata
from llama_index.core.query_engine import RouterQueryEngine
from llama_index.core.selectors import LLMSingleSelector
from llama_index.vector_stores.faiss import FaissVectorStore
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.chat_engine import CondensePlusContextChatEngine

load_dotenv()

logger = logging.getLogger("chatpdf.engine")

# ---------------------------------------------------------------------------
# Config (all secrets from .env, no hardcoded keys)
# ---------------------------------------------------------------------------
DEFAULT_PERSIST_DIR = os.path.join(os.path.dirname(__file__), "faiss_db")
EMBED_MODEL         = "BAAI/bge-small-en-v1.5"
GROQ_MODEL          = "llama-3.3-70b-versatile"
TOP_K               = 5
MEMORY_TOKENS       = 4096

FACT_PROMPT = (
    "You are a financial data assistant. Answer based ONLY on the provided context.\n"
    "Rules:\n"
    "1. If the answer is not found, reply exactly: Not found in the document.\n"
    "2. Append citation [px:cx] at the end of every sentence containing a fact or number.\n"
    "3. Be concise and strictly factual. No filler words."
)

SUMMARY_PROMPT = (
    "You are a financial data assistant. Generate a comprehensive factual summary.\n"
    "Rules:\n"
    "1. If information is missing, say: Not found in the document.\n"
    "2. Be direct. No apologies, no filler."
)

SYSTEM_PROMPT = (
    "You are a financial data assistant. Answer based ONLY on the provided context.\n"
    "Output format:\n"
    "- List citation references first on one line.\n"
    "- Then give the direct answer.\n"
    "Rules:\n"
    "1. If the answer is not present, reply exactly: Not found in the document.\n"
    "2. Append citation [px:cx] at the end of every sentence containing a fact or number.\n"
    "3. No filler, no apologies, no guessing."
)


def get_chat_engine(persist_dir: str | None = None):
    """
    Build and return a CondensePlusContextChatEngine.

    Parameters
    ----------
    persist_dir : str, optional
        Path to the FAISS index directory for a specific PDF.
        Falls back to the default ``./faiss_db`` when not provided (CLI usage).
    """
    t_start = time.time()
    logger.info(f"[BUILD] Building chat engine (persist_dir={persist_dir})")

    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        raise EnvironmentError("GROQ_API_KEY not set in .env file.")

    t0 = time.time()
    Settings.embed_model = HuggingFaceEmbedding(model_name=EMBED_MODEL)
    Settings.llm         = Groq(model=GROQ_MODEL, api_key=groq_api_key, temperature=0.0)
    logger.info(f"[BUILD] Models loaded in {time.time()-t0:.2f}s")

    persist_dir = persist_dir or DEFAULT_PERSIST_DIR

    if not os.path.exists(persist_dir):
        raise FileNotFoundError(f"FAISS index not found at {persist_dir}. Run ingest.py first.")

    t0 = time.time()
    vector_store    = FaissVectorStore.from_persist_dir(persist_dir)
    storage_context = StorageContext.from_defaults(
        vector_store=vector_store,
        persist_dir=persist_dir,
    )
    index = load_index_from_storage(storage_context=storage_context)
    logger.info(f"[BUILD] FAISS index loaded in {time.time()-t0:.2f}s")

    # --- Fact-retrieval tool ---
    t0 = time.time()
    fact_engine = index.as_query_engine(
        similarity_top_k=TOP_K,
        system_prompt=FACT_PROMPT,
    )
    fact_tool = QueryEngineTool(
        query_engine=fact_engine,
        metadata=ToolMetadata(
            name="fact_tool",
            description="Use for specific numbers, figures, and factual queries.",
        ),
    )

    # --- Summary tool ---
    all_nodes      = list(index.docstore.docs.values())
    summary_index  = SummaryIndex(all_nodes)
    summary_engine = summary_index.as_query_engine(
        response_mode="tree_summarize",
        system_prompt=SUMMARY_PROMPT,
    )
    summary_tool = QueryEngineTool(
        query_engine=summary_engine,
        metadata=ToolMetadata(
            name="summary_tool",
            description="Use only for summarize, overview, or describe requests.",
        ),
    )

    # --- Router engine ---
    router_engine = RouterQueryEngine(
        selector=LLMSingleSelector.from_defaults(),
        query_engine_tools=[fact_tool, summary_tool],
        verbose=False,
    )
    logger.info(f"[BUILD] Query engines & router built in {time.time()-t0:.2f}s")

    # --- Chat engine with memory ---
    memory = ChatMemoryBuffer.from_defaults(token_limit=MEMORY_TOKENS)

    chat_engine = CondensePlusContextChatEngine.from_defaults(
        query_engine=router_engine,
        retriever=index.as_retriever(similarity_top_k=TOP_K),
        memory=memory,
        system_prompt=SYSTEM_PROMPT,
    )

    logger.info(f"[BUILD] ✅ Chat engine ready — total build time: {time.time()-t_start:.2f}s")
    return chat_engine


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        engine = get_chat_engine()
        logger.info("Engine ready.")
    except Exception as e:
        logger.error(f"Error: {e}")