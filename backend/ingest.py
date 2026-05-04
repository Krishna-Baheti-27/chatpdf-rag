"""
PDF ingestion pipeline.
Parses a PDF via LlamaParse, chunks it, builds a FAISS index, and persists to disk.
"""

import os
import sys
import time
import logging
from llama_parse import LlamaParse
from llama_index.core.node_parser import MarkdownNodeParser
import faiss
from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.vector_stores.faiss import FaissVectorStore
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("chatpdf.ingest")

# ---------------------------------------------------------------------------
# Config (all secrets from .env, no hardcoded keys)
# ---------------------------------------------------------------------------
DEFAULT_PERSIST_DIR = os.path.join(os.path.dirname(__file__), "faiss_db")
EMBED_MODEL_NAME    = "BAAI/bge-small-en-v1.5"
EMBED_DIM           = 384

FINANCIAL_PARSING_PROMPT = """You are an elite Vision-Language Data Extraction Expert. Your sole directive is to meticulously analyze images, documents, and visual data structures and extract their contents with 100% fidelity. You operate under a STRICT ZERO-HALLUCINATION policy. You must extract ONLY the data visibly present in the provided image. Do NOT infer missing data, perform unrequested calculations, or introduce outside knowledge.

Identify the type of visual structure(s) in the image and apply the following strict extraction protocols:

1. For Charts and Graphs (Stacked, Bar, Line, Pie, Donut, Scatter):
   - Read the title, axes labels, units, and legend. Map every color or marker to its exact category.
   - Create a structured Markdown table. Do NOT squash grouped or stacked numbers into a single line.
   - Rows represent distinct categories from the legend. Columns represent the varying axes.

2. For Complex and Dense Tables:
   - Reproduce the table in Markdown with absolute structural accuracy.
   - Preserve hierarchical row headers using clear labeling.
   - Capture all multi-level column headers and units exactly as shown.
   - Extract all footnotes and edge-case text below the table.

3. For Flowcharts, Trees, and Organizational Diagrams:
   - Capture the logical flow, hierarchy, and relationships between elements.
   - Use structured nested lists to represent parent-child nodes and directional steps.
   - Include all text within nodes and on connecting arrows.

4. For Maps, Infographics, and Spatial Data:
   - Extract geographical data points, callouts, and localized text.
   - Group logically related text and data pairs based on visual proximity.

5. For Standard Text Blocks:
   - Transcribe paragraphs perfectly, maintaining original spelling and punctuation.

Absolute Constraints:
- Do not summarize. Extract every visible data point.
- If a number or label is unreadable, output [Unreadable] rather than guessing.
- Deliver the final result in clean Markdown using appropriate headings to separate distinct visual elements.
"""


def process_pdf_to_nodes(pdf_path: str):
    """Parse a PDF with LlamaParse and split into searchable chunks."""
    logger.info(f"[PARSE] Sending {pdf_path} to LlamaParse...")
    t_start = time.time()

    llama_key = os.getenv("LLAMA_CLOUD_API_KEY")
    if not llama_key:
        raise EnvironmentError("LLAMA_CLOUD_API_KEY not set in .env")
    os.environ["LLAMA_CLOUD_API_KEY"] = llama_key

    parser = LlamaParse(
        result_type="markdown",
        use_vendor_multimodal_model=True,
        vendor_multimodal_model_name="openai-gpt4o",
        content_guideline_instruction=FINANCIAL_PARSING_PROMPT,
        language="en",
        verbose=False,
    )

    try:
        t0 = time.time()
        documents = parser.load_data(pdf_path)
        for page_num, doc in enumerate(documents, start=1):
            doc.metadata["page_label"] = str(page_num)
        logger.info(f"[PARSE] LlamaParse completed in {time.time()-t0:.2f}s — {len(documents)} pages extracted")
    except Exception as e:
        logger.exception(f"[PARSE] LlamaParse FAILED after {time.time()-t_start:.2f}s: {e}")
        return []

    t0 = time.time()
    node_parser = MarkdownNodeParser()
    nodes = node_parser.get_nodes_from_documents(documents)

    for i, node in enumerate(nodes, start=1):
        page = node.metadata.get("page_label", "Unknown")
        node.metadata["chunk_id"] = i
        node.metadata["formatted_citation"] = f"p{page}:c{i}"

    logger.info(f"[PARSE] Chunked into {len(nodes)} nodes in {time.time()-t0:.2f}s")
    logger.info(f"[PARSE] Total parse time: {time.time()-t_start:.2f}s")
    return nodes


def build_and_store_faiss_index(nodes, persist_dir: str | None = None):
    """Embed chunks and persist a FAISS index to disk."""
    persist_dir = persist_dir or DEFAULT_PERSIST_DIR
    t_start = time.time()
    logger.info(f"[FAISS] Building index → {persist_dir} ({len(nodes)} nodes)")

    t0 = time.time()
    logger.info(f"[FAISS] Loading embedding model: {EMBED_MODEL_NAME}")
    embed_model = HuggingFaceEmbedding(model_name=EMBED_MODEL_NAME)
    Settings.embed_model = embed_model
    logger.info(f"[FAISS] Embedding model loaded in {time.time()-t0:.2f}s")

    faiss_index  = faiss.IndexFlatL2(EMBED_DIM)
    vector_store = FaissVectorStore(faiss_index=faiss_index)
    storage_ctx  = StorageContext.from_defaults(vector_store=vector_store)

    t0 = time.time()
    logger.info(f"[FAISS] Embedding {len(nodes)} chunks...")
    index = VectorStoreIndex(nodes, storage_context=storage_ctx)
    logger.info(f"[FAISS] Embedding completed in {time.time()-t0:.2f}s")

    t0 = time.time()
    os.makedirs(persist_dir, exist_ok=True)
    index.storage_context.persist(persist_dir=persist_dir)
    logger.info(f"[FAISS] Index persisted in {time.time()-t0:.2f}s")
    logger.info(f"[FAISS] Total build time: {time.time()-t_start:.2f}s")

    return index


# ---------------------------------------------------------------------------
# CLI entry-point (unchanged for standalone use)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(" Usage: python ingest.py <path_to_pdf>")
        sys.exit(1)

    pdf_file = sys.argv[1]

    if not os.path.exists(pdf_file):
        print(f" File not found: {pdf_file}")
        sys.exit(1)

    nodes = process_pdf_to_nodes(pdf_file)

    if not nodes:
        print(" No nodes extracted. Exiting.")
        sys.exit(1)

    print("\n--- Sanity Check: First 5 Chunks ---")
    for node in nodes[:5]:
        print(f" Chunk: {node.metadata.get('formatted_citation')} | Preview: {node.text[:512]}")
        print("-" * 60)

    build_and_store_faiss_index(nodes)