"""POST /v1/sessions accepts a teacher email address and password.

200 answers with TeacherSession; the server sets a Secure, HttpOnly, SameSite=Strict cookie. Every refusal of credentials is the
same 401 ApiError {code: "unauthorized", message: LOGIN_REFUSED_MESSAGE}: an unknown email and a wrong
password cannot be told apart. Too many failed attempts give 429 rate_limited with Retry-After, also for
the right password, until the window passes. The password and its hash never appear in an answer or a log.
"""

from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field

from contracts.v1.cabinet.registration import TeacherEmail

LOGIN_REFUSED_MESSAGE: Final = "Wrong email or password"
LOGIN_THROTTLED_MESSAGE: Final = "Too many login attempts, try again later"


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    email: TeacherEmail
    """Compared without surrounding spaces and without case."""
    password: Annotated[str, Field(min_length=1, max_length=1024, repr=False)]


class TeacherSession(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    teacherId: str
    displayName: str
