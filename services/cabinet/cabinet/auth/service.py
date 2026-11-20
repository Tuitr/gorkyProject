"""Teacher accounts and the check of credentials behind POST /v1/sessions."""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from cabinet.auth.passwords import Passwords
from cabinet.auth.tables import TeacherRecord
from cabinet.auth.throttle import LoginThrottle, clientKey, loginKey
from contracts.v1.cabinet.registration import TEACHER_LOGIN_CONFLICT_MESSAGE
from contracts.v1.cabinet.sessions import LOGIN_REFUSED_MESSAGE, LOGIN_THROTTLED_MESSAGE, LoginRequest, TeacherSession
from shared.httperrors import ApiProblem


def normalizeLogin(login: str) -> str:
    return login.strip().casefold()


class TeacherAccounts:
    def __init__(self, sessionFactory: sessionmaker[Session], passwords: Passwords, throttle: LoginThrottle) -> None:
        self.sessionFactory = sessionFactory
        self.passwords = passwords
        self.throttle = throttle

    def create(self, login: str, displayName: str, password: str) -> TeacherSession:
        teacherId = str(uuid.uuid4())
        statement = (
            insert(TeacherRecord)
            .values(
                teacherId=teacherId,
                login=normalizeLogin(login),
                displayName=displayName,
                passwordHash=self.passwords.hash(password),
            )
            .on_conflict_do_nothing(index_elements=[TeacherRecord.login])
            .returning(TeacherRecord.teacherId)
        )
        with self.sessionFactory.begin() as session:
            if session.scalar(statement) is None:
                raise ApiProblem("conflict", TEACHER_LOGIN_CONFLICT_MESSAGE)
        return TeacherSession(teacherId=teacherId, displayName=displayName)

    def find(self, login: str) -> TeacherRecord | None:
        with self.sessionFactory() as session:
            return session.scalars(select(TeacherRecord).where(TeacherRecord.login == normalizeLogin(login))).first()

    def authenticate(self, request: LoginRequest, clientAddress: str) -> TeacherSession:
        """The teacher behind the credentials, or ApiProblem: one 401 for every refusal, 429 while blocked."""
        login = normalizeLogin(request.email)
        keys = [loginKey(login), clientKey(clientAddress)]
        retryAfterS = self.throttle.retryAfterS(keys)
        if retryAfterS is not None:
            raise ApiProblem("rate_limited", LOGIN_THROTTLED_MESSAGE, retryAfterS=retryAfterS)
        teacher = self.find(login)
        if not self.passwords.verify(teacher.passwordHash if teacher is not None else None, request.password):
            self.throttle.recordFailure(keys)
            raise ApiProblem("unauthorized", LOGIN_REFUSED_MESSAGE)
        assert teacher is not None
        self.throttle.clear(keys[0])
        if self.passwords.needsRehash(teacher.passwordHash):
            self.rehash(teacher.teacherId, request.password)
        return TeacherSession(teacherId=teacher.teacherId, displayName=teacher.displayName)

    def rehash(self, teacherId: str, password: str) -> None:
        """New hash parameters apply to an account at its next successful login."""
        with self.sessionFactory.begin() as session:
            record = session.get(TeacherRecord, teacherId)
            if record is not None:
                record.passwordHash = self.passwords.hash(password)
