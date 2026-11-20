"""Requests and responses for teacher-confirmed upload bindings."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from contracts.v1.cabinet.classes import CreateStudent
from contracts.v1.events.checks import NonEmptyStr


class SelectStudentBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fileId: NonEmptyStr
    studentId: NonEmptyStr


class CreateStudentBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fileId: NonEmptyStr
    newStudent: CreateStudent


BindingRequest = SelectStudentBinding | CreateStudentBinding


class BindingConfirmed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fileId: NonEmptyStr
    studentId: NonEmptyStr
    status: Literal["matched", "bound"]
