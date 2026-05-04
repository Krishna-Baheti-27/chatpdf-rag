"""
Chat routes: CRUD for chats, sending messages and getting AI responses.
"""

import os
import time
import logging
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends
from bson import ObjectId

from database import chats_col, pdfs_col
from models import (
    ChatOut,
    ChatListItem,
    MessageOut,
    SendMessageRequest,
    RenameChatRequest,
)
from auth import get_current_user
from chat_engine import get_chat_engine

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = logging.getLogger("chatpdf.chats")

router = APIRouter(prefix="/api/chats", tags=["chats"])

# In-memory cache for chat engines keyed by (user_id, pdf_id)
_engine_cache: dict[str, object] = {}


def _get_engine(persist_dir: str, cache_key: str):
    """Get or create a chat engine for a specific PDF's FAISS index."""
    if cache_key in _engine_cache:
        logger.info(f"[ENGINE] Cache HIT for key={cache_key}")
        return _engine_cache[cache_key]

    logger.info(f"[ENGINE] Cache MISS for key={cache_key} — building new engine from {persist_dir}")
    t0 = time.time()
    _engine_cache[cache_key] = get_chat_engine(persist_dir=persist_dir)
    elapsed = time.time() - t0
    logger.info(f"[ENGINE] Engine built in {elapsed:.2f}s for key={cache_key}")
    return _engine_cache[cache_key]


@router.post("/", response_model=ChatOut, status_code=201)
async def create_chat(
    pdf_id: str,
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["_id"]
    logger.info(f"[CREATE_CHAT] user={user_id}, pdf_id={pdf_id}")

    # Verify PDF belongs to user
    pdf_doc = await pdfs_col().find_one({"_id": ObjectId(pdf_id), "user_id": user_id})
    if not pdf_doc:
        logger.warning(f"[CREATE_CHAT] PDF not found: pdf_id={pdf_id}, user={user_id}")
        raise HTTPException(status_code=404, detail="PDF not found")

    now = datetime.now(timezone.utc)
    chat_doc = {
        "user_id": user_id,
        "pdf_id": pdf_id,
        "pdf_filename": pdf_doc["filename"],
        "title": f"Chat with {pdf_doc['filename']}",
        "messages": [],
        "created_at": now,
        "updated_at": now,
    }
    result = await chats_col().insert_one(chat_doc)
    chat_id = str(result.inserted_id)
    logger.info(f"[CREATE_CHAT] Created chat_id={chat_id} for pdf={pdf_doc['filename']}")

    return ChatOut(
        id=chat_id,
        pdf_id=pdf_id,
        pdf_filename=pdf_doc["filename"],
        title=chat_doc["title"],
        messages=[],
        created_at=now,
        updated_at=now,
    )


@router.get("/", response_model=list[ChatListItem])
async def list_chats(current_user: dict = Depends(get_current_user)):
    user_id = current_user["_id"]
    logger.debug(f"[LIST_CHATS] user={user_id}")
    cursor = chats_col().find({"user_id": user_id}).sort("updated_at", -1)
    results = []
    async for doc in cursor:
        results.append(
            ChatListItem(
                id=str(doc["_id"]),
                pdf_id=doc["pdf_id"],
                pdf_filename=doc.get("pdf_filename", "Unknown"),
                title=doc["title"],
                message_count=len(doc.get("messages", [])),
                created_at=doc["created_at"],
                updated_at=doc["updated_at"],
            )
        )
    logger.debug(f"[LIST_CHATS] Returning {len(results)} chats for user={user_id}")
    return results


@router.get("/{chat_id}", response_model=ChatOut)
async def get_chat(chat_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["_id"]
    logger.debug(f"[GET_CHAT] chat_id={chat_id}, user={user_id}")
    doc = await chats_col().find_one({"_id": ObjectId(chat_id), "user_id": user_id})
    if not doc:
        logger.warning(f"[GET_CHAT] Chat not found: chat_id={chat_id}, user={user_id}")
        raise HTTPException(status_code=404, detail="Chat not found")

    messages = [
        MessageOut(
            role=m["role"],
            content=m["content"],
            citations=m.get("citations", []),
            timestamp=m["timestamp"],
        )
        for m in doc.get("messages", [])
    ]
    logger.debug(f"[GET_CHAT] Returning chat with {len(messages)} messages")

    return ChatOut(
        id=str(doc["_id"]),
        pdf_id=doc["pdf_id"],
        pdf_filename=doc.get("pdf_filename", "Unknown"),
        title=doc["title"],
        messages=messages,
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


@router.delete("/{chat_id}", status_code=204)
async def delete_chat(chat_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["_id"]
    logger.info(f"[DELETE_CHAT] chat_id={chat_id}, user={user_id}")
    result = await chats_col().delete_one(
        {"_id": ObjectId(chat_id), "user_id": user_id}
    )
    if result.deleted_count == 0:
        logger.warning(f"[DELETE_CHAT] Chat not found: chat_id={chat_id}")
        raise HTTPException(status_code=404, detail="Chat not found")
    logger.info(f"[DELETE_CHAT] Deleted chat_id={chat_id}")


@router.patch("/{chat_id}", response_model=ChatOut)
async def rename_chat(
    chat_id: str,
    body: RenameChatRequest,
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["_id"]
    logger.info(f"[RENAME_CHAT] chat_id={chat_id}, new_title={body.title!r}")
    result = await chats_col().find_one_and_update(
        {"_id": ObjectId(chat_id), "user_id": user_id},
        {"$set": {"title": body.title, "updated_at": datetime.now(timezone.utc)}},
        return_document=True,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Chat not found")

    messages = [
        MessageOut(
            role=m["role"],
            content=m["content"],
            citations=m.get("citations", []),
            timestamp=m["timestamp"],
        )
        for m in result.get("messages", [])
    ]

    return ChatOut(
        id=str(result["_id"]),
        pdf_id=result["pdf_id"],
        pdf_filename=result.get("pdf_filename", "Unknown"),
        title=result["title"],
        messages=messages,
        created_at=result["created_at"],
        updated_at=result["updated_at"],
    )


@router.post("/{chat_id}/message", response_model=ChatOut)
async def send_message(
    chat_id: str,
    body: SendMessageRequest,
    current_user: dict = Depends(get_current_user),
):
    total_start = time.time()
    user_id = current_user["_id"]
    logger.info(f"[SEND_MSG] ▶ START chat_id={chat_id}, user={user_id}, query={body.content[:80]!r}")

    # 1. Get the chat
    t0 = time.time()
    chat_doc = await chats_col().find_one(
        {"_id": ObjectId(chat_id), "user_id": user_id}
    )
    if not chat_doc:
        logger.error(f"[SEND_MSG] Chat not found: chat_id={chat_id}")
        raise HTTPException(status_code=404, detail="Chat not found")
    logger.info(f"[SEND_MSG] Step 1 — fetched chat from DB in {time.time()-t0:.3f}s")

    # 2. Get the PDF's FAISS index directory
    t0 = time.time()
    pdf_doc = await pdfs_col().find_one(
        {"_id": ObjectId(chat_doc["pdf_id"]), "user_id": user_id}
    )
    if not pdf_doc:
        logger.error(f"[SEND_MSG] PDF not found: pdf_id={chat_doc['pdf_id']}")
        raise HTTPException(status_code=404, detail="PDF not found")

    persist_dir = pdf_doc["faiss_persist_dir"]
    if not os.path.exists(persist_dir):
        logger.error(f"[SEND_MSG] FAISS index missing at: {persist_dir}")
        raise HTTPException(status_code=500, detail="PDF index not found on disk")
    logger.info(f"[SEND_MSG] Step 2 — fetched PDF doc & verified index in {time.time()-t0:.3f}s (dir={persist_dir})")

    # 3. Get or create the chat engine
    t0 = time.time()
    cache_key = f"{user_id}:{chat_doc['pdf_id']}:{chat_id}"
    try:
        engine = _get_engine(persist_dir, cache_key)
    except Exception as e:
        logger.exception(f"[SEND_MSG] Failed to load AI engine: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load AI engine: {e}")
    logger.info(f"[SEND_MSG] Step 3 — engine ready in {time.time()-t0:.3f}s")

    # 4. Feed previous messages into engine context (replay history for stateless engine)
    existing_messages = chat_doc.get("messages", [])
    logger.info(f"[SEND_MSG] Step 4 — existing messages count: {len(existing_messages)}")

    # 5. Get AI response — run in thread pool to avoid blocking the event loop
    now = datetime.now(timezone.utc)
    t0 = time.time()
    logger.info(f"[SEND_MSG] Step 5 — calling engine.chat() via thread pool...")
    try:
        response = await asyncio.to_thread(engine.chat, body.content)

        elapsed_chat = time.time() - t0
        logger.info(f"[SEND_MSG] Step 5 — engine.chat() completed in {elapsed_chat:.2f}s")

        # Extract citations
        citations = []
        if hasattr(response, "source_nodes"):
            citations = [
                node.node.metadata.get("formatted_citation", "")
                for node in response.source_nodes
                if node.node.metadata.get("formatted_citation")
            ]
            logger.info(f"[SEND_MSG] Step 5 — extracted {len(citations)} citations")

        ai_content = response.response
        logger.info(f"[SEND_MSG] Step 5 — AI response length: {len(ai_content)} chars, preview: {ai_content[:120]!r}")
    except Exception as e:
        elapsed_chat = time.time() - t0
        logger.exception(f"[SEND_MSG] Step 5 — engine.chat() FAILED after {elapsed_chat:.2f}s: {e}")
        ai_content = f"I'm sorry, I encountered an error processing your request: {str(e)}"
        citations = []

    # 6. Build message documents
    user_msg = {
        "role": "user",
        "content": body.content,
        "citations": [],
        "timestamp": now,
    }
    assistant_msg = {
        "role": "assistant",
        "content": ai_content,
        "citations": citations,
        "timestamp": datetime.now(timezone.utc),
    }

    # 7. Auto-title on first message
    update_fields = {
        "updated_at": datetime.now(timezone.utc),
    }
    if len(existing_messages) == 0:
        # Use first 50 chars of user's message as title
        new_title = body.content[:50] + ("..." if len(body.content) > 50 else "")
        update_fields["title"] = new_title
        logger.info(f"[SEND_MSG] Step 7 — auto-title set to: {new_title!r}")

    # 8. Update MongoDB
    t0 = time.time()
    await chats_col().update_one(
        {"_id": ObjectId(chat_id)},
        {
            "$push": {"messages": {"$each": [user_msg, assistant_msg]}},
            "$set": update_fields,
        },
    )
    logger.info(f"[SEND_MSG] Step 8 — MongoDB updated in {time.time()-t0:.3f}s")

    # 9. Return updated chat
    t0 = time.time()
    updated = await chats_col().find_one({"_id": ObjectId(chat_id)})
    messages = [
        MessageOut(
            role=m["role"],
            content=m["content"],
            citations=m.get("citations", []),
            timestamp=m["timestamp"],
        )
        for m in updated.get("messages", [])
    ]
    logger.info(f"[SEND_MSG] Step 9 — re-fetched chat in {time.time()-t0:.3f}s, total messages: {len(messages)}")

    total_elapsed = time.time() - total_start
    logger.info(f"[SEND_MSG] ✅ DONE chat_id={chat_id} — total time: {total_elapsed:.2f}s")

    return ChatOut(
        id=str(updated["_id"]),
        pdf_id=updated["pdf_id"],
        pdf_filename=updated.get("pdf_filename", "Unknown"),
        title=updated["title"],
        messages=messages,
        created_at=updated["created_at"],
        updated_at=updated["updated_at"],
    )
