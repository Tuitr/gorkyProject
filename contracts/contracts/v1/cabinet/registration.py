"""POST /v1/teachers/register creates a teacher whose login is an email address."""

from typing import Annotated, Final

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

TEACHER_LOGIN_CONFLICT_MESSAGE: Final = "Teacher login already exists"


class TeacherRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    email: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=254), Field(json_schema_extra={"format": "email"})
    ]
    displayName: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200, pattern=r"^[^\x00]+$")
    ]
    password: Annotated[str, Field(min_length=8, max_length=1024, repr=False)]

    @field_validator("email")
    @classmethod
    def validateEmail(cls, email: str) -> str:
        try:
            validate_email(email, check_deliverability=False)
        except EmailNotValidError:
            raise ValueError("Expected a valid email address") from None
        if len(email.casefold()) > 254:
            raise ValueError("Email exceeds the maximum login length")
        # Keep the address in the same form as the existing case-insensitive login lookup.
        return email
