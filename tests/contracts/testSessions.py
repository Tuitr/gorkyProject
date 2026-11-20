"""A2.1: examples of POST /v1/sessions are valid contract models."""

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from contracts.v1.cabinet.sessions import LOGIN_REFUSED_MESSAGE, LOGIN_THROTTLED_MESSAGE, LoginRequest, TeacherSession
from contracts.v1.common.errors import ApiError


@pytest.mark.parametrize(
    ("path", "model"),
    [
        ("cabinet/loginRequest.json", LoginRequest),
        ("cabinet/teacherSession.json", TeacherSession),
        ("cabinet/loginRefused.json", ApiError),
        ("cabinet/loginThrottled.json", ApiError),
    ],
)
def testExamplesRoundTrip(contractExample: Callable[[str], Any], path: str, model: type[BaseModel]) -> None:
    example = contractExample(path)

    assert model.model_validate(example).model_dump(mode="json") == example


def testRefusalExamplesUseTheFixedMessages(contractExample: Callable[[str], Any]) -> None:
    assert contractExample("cabinet/loginRefused.json")["message"] == LOGIN_REFUSED_MESSAGE
    assert contractExample("cabinet/loginThrottled.json")["message"] == LOGIN_THROTTLED_MESSAGE


def testPasswordIsHiddenFromRepr() -> None:
    request = LoginRequest(login="teacher.demo", password="stand-only-password")

    assert "stand-only-password" not in repr(request)
    with pytest.raises(ValidationError):
        LoginRequest.model_validate({"login": "teacher.demo", "password": "x", "teacherId": "t1"})
