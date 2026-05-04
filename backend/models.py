"""
Pydantic models for request / response validation and MongoDB document shapes.
"""

from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Literal
from datetime import datetime


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=6, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    created_at: datetime


# ---------------------------------------------------------------------------
# PDFs
# ---------------------------------------------------------------------------
class PDFOut(BaseModel):
    id: str
    filename: str
    supabase_url: str
    page_count: int
    uploaded_at: datetime


# ---------------------------------------------------------------------------
# Chats
# ---------------------------------------------------------------------------
class MessageOut(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    citations: List[str] = []
    timestamp: datetime


class ChatOut(BaseModel):
    id: str
    pdf_id: str
    pdf_filename: str
    title: str
    messages: List[MessageOut] = []
    created_at: datetime
    updated_at: datetime


class ChatListItem(BaseModel):
    id: str
    pdf_id: str
    pdf_filename: str
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10000)


class RenameChatRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
