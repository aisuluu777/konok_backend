from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.modules.users.models import KycStatus, UserRole, UserStatus

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
# Без пакета email-validator: проверяем форму local@domain.tld, остальное — подтверждение почты.
Email = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
]
# E.164: «+», код страны и номер, только цифры.
Phone = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\+[1-9]\d{7,14}$")]
Password = Annotated[str, StringConstraints(min_length=8, max_length=128)]


class RegisterIn(BaseModel):
    first_name: Name
    last_name: Name
    email: Email
    phone: Phone
    password: Password
    terms_accepted: Literal[True]


class LoginIn(BaseModel):
    email: Annotated[str, Field(max_length=254)]
    password: Annotated[str, Field(max_length=128)]


class RefreshIn(BaseModel):
    refresh_token: Annotated[str, Field(max_length=512)]


class VerifyPhoneIn(BaseModel):
    code: Annotated[str, Field(max_length=16)]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: UserRole
    status: UserStatus
    first_name: str
    last_name: str
    email: str
    phone: str
    phone_verified_at: datetime | None
    kyc_status: KycStatus
    created_at: datetime


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"


class RegisterOut(TokenPair):
    user: UserOut


class OtpRequestedOut(BaseModel):
    expires_in: int


class PinOut(BaseModel):
    pin: str
