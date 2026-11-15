from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

class AnswerIn(StrictModel):
    answer: str = Field(min_length=1, max_length=64)

class AnalyzeIn(StrictModel):
    analysis_type: Literal['library_preference']

class SessionIn(StrictModel):
    target_name: str = Field(min_length=1, max_length=80)
    consent: bool

class Sender(StrictModel):
    name: str = Field(max_length=200)
    email: str = Field(min_length=3, max_length=320)

class EmailMeta(StrictModel):
    subject: str = Field(min_length=1, max_length=400)
    message_id: str = Field(min_length=1, max_length=200)
    received_at: datetime

class AttachmentMeta(StrictModel):
    file_id: str
    filename: str
    file_type: Literal['txt']
    validation_status: Literal['valid']

class EmailEventIn(StrictModel):
    event_id: str = Field(min_length=1, max_length=120)
    upload_id: str
    upload_code: str
    sender: Sender
    email: EmailMeta
    attachment: AttachmentMeta

class AttachmentIn(StrictModel):
    message_id: str = Field(min_length=1, max_length=200)
    attachment_id: str = Field(min_length=1, max_length=200)
    filename: str = Field(min_length=1, max_length=220)
    content_base64: str = Field(min_length=1)

class ApprovalIn(StrictModel):
    upload_id: str
    user_id: str
    identity_verification: dict
    analysis: dict

# ---------- 좌석 추천 ----------
class SeatRecommendationOptions(StrictModel):
    topK: int = Field(default=5, ge=1, le=20)
    availabilityPolicy: Literal['allowUnknown', 'availableOnly'] = 'allowUnknown'

class SeatRecommendationIn(StrictModel):
    roomId: str = Field(min_length=1, max_length=200)
    options: SeatRecommendationOptions = Field(default_factory=SeatRecommendationOptions)

class RecommendationEventIn(StrictModel):
    eventId: str = Field(min_length=1, max_length=100)
    occurredAt: datetime | None = None
    type: Literal['recommendationExposed', 'candidateSelected', 'candidateRejected', 'useConfirmed', 'useEnded', 'ratingSubmitted']
    visibleTargetIds: list[str] | None = Field(default=None, max_length=50)
    targetId: str | None = Field(default=None, max_length=200)
    origin: Literal['recommendation', 'outsideRecommendations'] | None = None
    reason: str | None = Field(default=None, max_length=500)
    excludeFromFuture: bool | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    comment: str | None = Field(default=None, max_length=300)

class RoomRecommendationOptions(StrictModel):
    topK: int = Field(default=3, ge=1, le=24)

class RoomRecommendationIn(StrictModel):
    libraryIds: list[str] | None = Field(default=None, max_length=20)
    options: RoomRecommendationOptions = Field(default_factory=RoomRecommendationOptions)

class PreferenceItemIn(StrictModel):
    featureId: str = Field(min_length=1, max_length=40)
    choice: str = Field(min_length=1, max_length=20)
    importance: int = Field(default=2, ge=1, le=3)

class PreferencesIn(StrictModel):
    expectedRevision: int = Field(ge=0)
    items: list[PreferenceItemIn] = Field(default_factory=list, max_length=20)
    laptopRequired: bool | None = None

# ---------- 회원가입·로그인 ----------
class EmailCodeIn(StrictModel):
    email: str = Field(min_length=3, max_length=254)
    purpose: Literal['signup', 'reset'] = 'signup'

class PasswordResetIn(StrictModel):
    email: str = Field(min_length=3, max_length=254)
    code: str = Field(min_length=6, max_length=6)
    password: str = Field(min_length=1, max_length=128)

class AccountDeleteIn(StrictModel):
    password: str = Field(min_length=1, max_length=128)

class SignupIn(StrictModel):
    email: str = Field(min_length=3, max_length=254)
    code: str = Field(min_length=6, max_length=6)
    nickname: str = Field(min_length=1, max_length=20)
    password: str = Field(min_length=1, max_length=128)

class LoginIn(StrictModel):
    nickname: str = Field(min_length=1, max_length=20)
    password: str = Field(min_length=1, max_length=128)
