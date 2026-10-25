from pydantic import BaseModel, EmailStr, Field


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=200)


class VerifyEmailIn(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$", description="6-значный код из письма")


class EmailIn(BaseModel):
    email: EmailStr


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
