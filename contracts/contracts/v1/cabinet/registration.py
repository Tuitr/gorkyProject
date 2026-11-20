"""POST /v1/teachers/register creates a teacher whose login is an email address."""

from typing import Annotated, Final

from email_validator import EmailNotValidError, validate_email
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

TEACHER_LOGIN_CONFLICT_MESSAGE: Final = "Teacher email already exists"


def validateEmail(email: str) -> str:
    try:
        validate_email(email, check_deliverability=False)
    except EmailNotValidError:
        raise ValueError("Expected a valid email address") from None
    if len(email.casefold()) > 254:
        raise ValueError("Email exceeds the maximum login length")
    return email


TeacherEmail = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=254),
    Field(json_schema_extra={"format": "email"}),
    AfterValidator(validateEmail),
]


class TeacherRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    email: TeacherEmail
    displayName: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200, pattern=r"^[^\x00]+$")
    ]
    password: Annotated[str, Field(min_length=8, max_length=1024, repr=False)]
