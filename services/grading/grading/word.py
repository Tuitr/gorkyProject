"""Shared access to the main DOCX document part and its visible text runs."""

from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class WordReadError(ValueError):
    pass


def documentBody(document: bytes) -> ET.Element:
    try:
        with ZipFile(BytesIO(document)) as archive, archive.open("word/document.xml") as part:
            body = ET.parse(part).getroot().find(W + "body")
    except (BadZipFile, KeyError, ET.ParseError) as error:
        raise WordReadError("cannot read the DOCX main document") from error
    if body is None:
        raise WordReadError("missing DOCX body")
    return body


def paragraphText(paragraph: ET.Element) -> str:
    fragments = []
    for node in paragraph.iter():
        if node.tag == W + "t":
            fragments.append(node.text or "")
        elif node.tag == W + "tab":
            fragments.append("\t")
        elif node.tag in (W + "br", W + "cr"):
            fragments.append("\n")
    return "".join(fragments)
