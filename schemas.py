

from datetime import date, datetime
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field, validator
from typing import List, Optional, Dict
from uuid import UUID

class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserOut(BaseModel):
    id: UUID
    username: str
    email: EmailStr
    is_verified: bool

    class Config:
        orm_mode = True

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: str | None = None

class FileInput(BaseModel):
    id: UUID
    original_name: str
    stored_name: str
    physical_path: str
    drive_path: str | None = None
    content_type: str | None = None
    s3_path: str | None = None
    s3_url: str | None = None
    owner_id: UUID
