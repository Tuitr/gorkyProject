"""Find student candidates using the filename, DOCX signature and class roster."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import islice
from pathlib import PurePosixPath
from unicodedata import normalize
from xml.etree.ElementTree import Element

from cabinet.classes import StudentRecord

SIGNATURE = re.compile(
    r"^\s*(?:ученик|ученица|ф\.?\s*и\.?\s*о\.?|выполнил(?:а|\(а\))?)\s*[:：]\s*(.*)$",
    re.IGNORECASE,
)
WORDS = re.compile(r"[^\W\d_]+")
PHOTO_NAME_PART = r"[А-Яа-яЁё]+(?:-[А-Яа-яЁё]+)*"
PHOTO_STUDENT_NAME = re.compile(rf"{PHOTO_NAME_PART} {PHOTO_NAME_PART}")
PHOTO_PAGE_NUMBER = re.compile(r"0[1-9]|[1-9][0-9]|100")
TRANSLITERATION = str.maketrans(dict(zip(
    "абвгдежзийклмнопрстуфхцчшщъыьэюя",
    ("a", "b", "v", "g", "d", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o",
     "p", "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"),
    strict=True,
)))


@dataclass(frozen=True)
class NameMatch:
    exact: frozenset[str]
    candidates: frozenset[str]


@dataclass(frozen=True)
class BindingMatch:
    candidateIds: list[str]
    studentId: str | None = None
    reason: str | None = None


def signatureNames(document: Element) -> tuple[str, ...]:
    namespace = document.tag.removesuffix("document")
    paragraphTag = f"{namespace}p"
    textTag = f"{namespace}t"
    signatures = []
    for paragraph in islice(document.iter(paragraphTag), 16):
        text = "".join(node.text or "" for node in paragraph.iter(textTag))
        labelled = SIGNATURE.match(text)
        if labelled is not None:
            name = labelled.group(1).split(",", 1)[0].strip()
            signatures.append(name if len(name) <= 200 else "")
    return tuple(signatures)


def nameKey(name: str) -> tuple[str, ...]:
    return tuple(sorted(WORDS.findall(normalize("NFKC", name).casefold().replace("ё", "е"))))


@dataclass(frozen=True)
class PhotoPageName:
    groupKey: tuple[str, ...]
    studentName: str | None
    number: int | None  # None is unnumbered. Zero means a malformed suffix.


def photoPageName(filename: str) -> PhotoPageName:
    stem = normalize("NFKC", PurePosixPath(filename).stem)
    base, separator, suffix = stem.rpartition("__")
    name = base if separator else stem
    displayName = " ".join(name.replace("_", " ").split())
    studentName = displayName if len(displayName) <= 200 and PHOTO_STUDENT_NAME.fullmatch(displayName) else None
    key = nameKey(studentName) if studentName is not None else (displayName.casefold(),)
    number = None
    if separator:
        number = int(suffix) if PHOTO_PAGE_NUMBER.fullmatch(suffix) else 0
    return PhotoPageName(key, studentName, number)


def rosterMatch(name: tuple[str, ...], roster: Sequence[StudentRecord]) -> NameMatch:
    exact: set[str] = set()
    similar: set[str] = set()
    tokens = set(name)
    filenameUsesLatin = all(token.isascii() for token in name)
    for student in roster:
        recorded = nameKey(student.name)
        if filenameUsesLatin:
            recorded = tuple(sorted(word.translate(TRANSLITERATION) for word in recorded))
        if len(name) >= 2 and name == recorded:
            exact.add(student.studentId)
        elif tokens.intersection(recorded):
            similar.add(student.studentId)
    return NameMatch(frozenset(exact), frozenset(exact or similar))


def matchStudent(filename: str, signatures: tuple[str, ...], roster: Sequence[StudentRecord]) -> BindingMatch:
    stem = re.sub(r"\.docx$", "", filename, flags=re.IGNORECASE)
    filenameKey = nameKey(stem)
    named = rosterMatch(filenameKey, roster)
    signedKeys = {nameKey(signature) for signature in signatures}
    signedMatches = [rosterMatch(key, roster) for key in signedKeys]
    candidateIds = sorted(named.candidates.union(*(match.candidates for match in signedMatches)))

    if not signedMatches:
        if len(named.exact) == 1:
            return BindingMatch(candidateIds, next(iter(named.exact)))
    elif len(signedMatches) > 1:
        return BindingMatch(candidateIds, reason="В документе указаны разные подписи.")
    else:
        signed = signedMatches[0]
        if len(signed.exact) == 1:
            studentId = next(iter(signed.exact))
            filenameAgrees = (
                named.exact == signed.exact
                if len(filenameKey) >= 2
                else not named.candidates or studentId in named.candidates
            )
            if filenameAgrees:
                return BindingMatch(candidateIds, studentId)
            return BindingMatch(candidateIds, reason="Подпись и имя файла не совпадают.")
        if named.exact and signed.exact != named.exact:
            return BindingMatch(candidateIds, reason="Подпись и имя файла не совпадают.")

    if candidateIds:
        return BindingMatch(candidateIds, reason="Укажите ученика по полному имени.")
    return BindingMatch(candidateIds, reason="В классе нет совпадения по имени файла или подписи.")
