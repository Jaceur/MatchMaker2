"""Pydantic request/response models for the API.

Lead rows have ~40 columns and are read straight from SQLAlchemy mappings, so the
lead endpoints return plain dicts (FastAPI serialises dates/Decimals for us)
rather than a rigid model. The schemas here are for the structured inputs/outputs
where a contract genuinely helps the frontend.
"""
from typing import Optional

from pydantic import BaseModel


# ---------- Auth ----------
class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    username: str
    role: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---------- Swipe actions ----------
class PassRequest(BaseModel):
    rejection_reason: str
    website_valid: bool = False
    linkedin_valid: bool = False
    corrected_website_url: Optional[str] = None
    corrected_linkedin_url: Optional[str] = None
    dwell_time_seconds: Optional[int] = None


class ApproveRequest(BaseModel):
    website_valid: bool = False
    linkedin_valid: bool = False
    corrected_website_url: Optional[str] = None
    corrected_linkedin_url: Optional[str] = None


# ---------- My Pipeline / classify ----------
class EmailVerdict(BaseModel):
    director_name: str
    pattern: str
    email: str
    selected: bool = False


class ClassifyRequest(BaseModel):
    crm_status: str
    email_verdicts: list[EmailVerdict] = []


class DirectorEmails(BaseModel):
    director_name: str
    candidates: list[dict]  # [{pattern, email}]


# ---------- Admin ----------
class QualifyPercentRequest(BaseModel):
    percent: int


class PipelineJobRequest(BaseModel):
    count: int = 100


class AllocationRequest(BaseModel):
    commit: bool = True
    target: Optional[int] = None
