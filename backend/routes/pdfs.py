"""
PDF upload, list, and delete routes.
Uploaded PDFs are stored in Supabase and ingested into a per-PDF FAISS index.
"""

import os
import uuid
import time
import logging
import tempfile
import shutil
import traceback
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, UploadFile, File, Depends
from bson import ObjectId

from database import pdfs_col, chats_col, get_supabase, get_supabase_bucket
from models import PDFOut
from auth import get_current_user
from ingest import process_pdf_to_nodes, build_and_store_faiss_index

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = logging.getLogger("chatpdf.pdfs")

router = APIRouter(prefix="/api/pdfs", tags=["pdfs"])

USER_INDEXES_DIR = os.path.join(os.path.dirname(__file__), "user_indexes")


def _get_persist_dir(user_id: str, pdf_id: str) -> str:
    return os.path.join(USER_INDEXES_DIR, user_id, pdf_id)


@router.post("/upload", response_model=PDFOut, status_code=201)
async def upload_pdf(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    total_start = time.time()

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    user_id = current_user["_id"]
    pdf_id = str(ObjectId())

    logger.info(f"[UPLOAD] ▶ START user={user_id}, pdf_id={pdf_id}, filename={file.filename}")

    # 1. Save uploaded file to a temp location
    tmp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(tmp_dir, file.filename)
    try:
        t0 = time.time()
        with open(tmp_path, "wb") as f:
            content = await file.read()
            f.write(content)
        file_size_mb = len(content) / (1024 * 1024)
        logger.info(f"[UPLOAD] Step 1 — saved to temp ({file_size_mb:.1f} MB) in {time.time()-t0:.3f}s")

        # 2. Upload to Supabase storage
        t0 = time.time()
        logger.info(f"[UPLOAD] Step 2 — uploading to Supabase...")
        bucket = get_supabase_bucket()
        storage_path = f"{user_id}/{pdf_id}/{file.filename}"
        supabase = get_supabase()

        try:
            supabase.storage.from_(bucket).upload(
                path=storage_path,
                file=content,
                file_options={"content-type": "application/pdf"},
            )
            logger.info(f"[UPLOAD] Step 2 — Supabase upload done in {time.time()-t0:.3f}s")
        except Exception as e:
            logger.exception(f"[UPLOAD] Step 2 — Supabase upload FAILED: {e}")
            raise HTTPException(status_code=500, detail=f"Supabase upload failed: {str(e)}")

        # Build the public URL
        supabase_url = f"{os.getenv('SUPABASE_URL')}/storage/v1/object/public/{bucket}/{storage_path}"

        # 3. Ingest PDF into FAISS — run in thread pool (this is CPU-bound / IO-heavy)
        persist_dir = _get_persist_dir(user_id, pdf_id)
        os.makedirs(persist_dir, exist_ok=True)

        # 3a. Parse PDF
        t0 = time.time()
        logger.info(f"[UPLOAD] Step 3a — parsing PDF via LlamaParse...")
        try:
            nodes = await asyncio.to_thread(process_pdf_to_nodes, tmp_path)
        except Exception as e:
            logger.exception(f"[UPLOAD] Step 3a — PDF parsing FAILED after {time.time()-t0:.2f}s: {e}")
            raise HTTPException(status_code=500, detail=f"PDF parsing failed: {str(e)}")

        if not nodes:
            logger.error(f"[UPLOAD] Step 3a — no nodes extracted from PDF")
            raise HTTPException(status_code=500, detail="Failed to parse PDF — no content extracted")
        logger.info(f"[UPLOAD] Step 3a — parsed {len(nodes)} nodes in {time.time()-t0:.2f}s")

        # 3b. Build FAISS index
        t0 = time.time()
        logger.info(f"[UPLOAD] Step 3b — building FAISS index at {persist_dir}...")
        try:
            await asyncio.to_thread(build_and_store_faiss_index, nodes, persist_dir)
        except Exception as e:
            logger.exception(f"[UPLOAD] Step 3b — FAISS index build FAILED after {time.time()-t0:.2f}s: {e}")
            raise HTTPException(status_code=500, detail=f"FAISS index build failed: {str(e)}")
        logger.info(f"[UPLOAD] Step 3b — FAISS index built in {time.time()-t0:.2f}s")

        # 4. Count pages (estimate from nodes or use a simple heuristic)
        page_labels = set()
        for node in nodes:
            page_labels.add(node.metadata.get("page_label", "1"))
        page_count = len(page_labels) if page_labels else 1
        logger.info(f"[UPLOAD] Step 4 — detected {page_count} pages")

        # 5. Save metadata to MongoDB
        t0 = time.time()
        pdf_doc = {
            "_id": ObjectId(pdf_id),
            "user_id": user_id,
            "filename": file.filename,
            "supabase_url": supabase_url,
            "storage_path": storage_path,
            "faiss_persist_dir": persist_dir,
            "page_count": page_count,
            "uploaded_at": datetime.now(timezone.utc),
        }
        await pdfs_col().insert_one(pdf_doc)
        logger.info(f"[UPLOAD] Step 5 — saved to MongoDB in {time.time()-t0:.3f}s")

        total_elapsed = time.time() - total_start
        logger.info(f"[UPLOAD] ✅ DONE pdf_id={pdf_id}, filename={file.filename} — total time: {total_elapsed:.2f}s")

        return PDFOut(
            id=pdf_id,
            filename=file.filename,
            supabase_url=supabase_url,
            page_count=page_count,
            uploaded_at=pdf_doc["uploaded_at"],
        )

    except HTTPException:
        raise  # Re-raise HTTP exceptions as-is
    except Exception as e:
        logger.exception(f"[UPLOAD] Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.get("/", response_model=list[PDFOut])
async def list_pdfs(current_user: dict = Depends(get_current_user)):
    user_id = current_user["_id"]
    logger.debug(f"[LIST_PDFS] user={user_id}")
    cursor = pdfs_col().find({"user_id": user_id}).sort("uploaded_at", -1)
    results = []
    async for doc in cursor:
        results.append(
            PDFOut(
                id=str(doc["_id"]),
                filename=doc["filename"],
                supabase_url=doc["supabase_url"],
                page_count=doc.get("page_count", 0),
                uploaded_at=doc["uploaded_at"],
            )
        )
    logger.debug(f"[LIST_PDFS] Returning {len(results)} PDFs")
    return results


@router.delete("/{pdf_id}", status_code=204)
async def delete_pdf(pdf_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["_id"]
    logger.info(f"[DELETE_PDF] pdf_id={pdf_id}, user={user_id}")
    pdf_doc = await pdfs_col().find_one({"_id": ObjectId(pdf_id), "user_id": user_id})
    if not pdf_doc:
        raise HTTPException(status_code=404, detail="PDF not found")

    # Delete from Supabase
    try:
        bucket = get_supabase_bucket()
        supabase = get_supabase()
        supabase.storage.from_(bucket).remove([pdf_doc["storage_path"]])
        logger.info(f"[DELETE_PDF] Removed from Supabase: {pdf_doc['storage_path']}")
    except Exception as e:
        logger.warning(f"[DELETE_PDF] Supabase deletion failed (best effort): {e}")

    # Delete FAISS index from disk
    persist_dir = pdf_doc.get("faiss_persist_dir")
    if persist_dir and os.path.exists(persist_dir):
        shutil.rmtree(persist_dir, ignore_errors=True)
        logger.info(f"[DELETE_PDF] Removed FAISS index: {persist_dir}")

    # Delete associated chats
    chat_result = await chats_col().delete_many({"pdf_id": pdf_id, "user_id": user_id})
    logger.info(f"[DELETE_PDF] Deleted {chat_result.deleted_count} associated chats")

    # Delete PDF document
    await pdfs_col().delete_one({"_id": ObjectId(pdf_id)})
    logger.info(f"[DELETE_PDF] ✅ DONE pdf_id={pdf_id}")


@router.get("/{pdf_id}/view")
async def view_pdf(pdf_id: str, token: str = ""):
    """
    Serve the PDF file for the in-app viewer.
    Accepts JWT via query param since iframes can't send Authorization headers.
    """
    from fastapi.responses import Response
    from auth import decode_access_token
    from bson import ObjectId as BsonObjectId

    if not token:
        raise HTTPException(status_code=401, detail="Token required")

    payload = decode_access_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    pdf_doc = await pdfs_col().find_one({"_id": BsonObjectId(pdf_id), "user_id": user_id})
    if not pdf_doc:
        raise HTTPException(status_code=404, detail="PDF not found")

    try:
        bucket = get_supabase_bucket()
        supabase = get_supabase()
        file_bytes = supabase.storage.from_(bucket).download(pdf_doc["storage_path"])
        return Response(
            content=file_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{pdf_doc["filename"]}"',
            },
        )
    except Exception as e:
        logger.exception(f"[VIEW_PDF] Failed to download PDF: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve PDF: {str(e)}")


