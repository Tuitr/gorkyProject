"""A minimal Word document: paragraphs of plain text, some of them bold.

The XML is written by hand instead of with a Word library so that nothing adds the current time,
a random id or a library version to the file. Word, LibreOffice and python-docx open the result.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from xml.sax.saxutils import escape

from dataset.archive import storedZip

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
HEADER = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

CONTENT_TYPES = (
    HEADER + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '<Override PartName="/docProps/core.xml" '
    'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    "</Types>"
)

PACKAGE_RELS = (
    HEADER + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    '<Relationship Id="rId2" '
    'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
    'Target="docProps/core.xml"/>'
    "</Relationships>"
)

DOCUMENT_RELS = (
    HEADER + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    "</Relationships>"
)

STYLES = (
    HEADER + f'<w:styles xmlns:w="{W}">'
    "<w:docDefaults><w:rPrDefault><w:rPr>"
    '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="Times New Roman" '
    'w:cs="Times New Roman"/><w:sz w:val="28"/><w:szCs w:val="28"/><w:lang w:val="ru-RU"/>'
    "</w:rPr></w:rPrDefault>"
    '<w:pPrDefault><w:pPr><w:spacing w:after="120"/></w:pPr></w:pPrDefault></w:docDefaults>'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
    "</w:styles>"
)

PAGE = (
    '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
    '<w:pgMar w:top="1134" w:right="850" w:bottom="1134" w:left="1701" w:header="708" w:footer="708" w:gutter="0"/>'
    "</w:sectPr>"
)
"""A4 with the usual Russian school margins."""


@dataclass(frozen=True)
class Paragraph:
    text: str
    bold: bool = False


def paragraphXml(paragraph: Paragraph) -> str:
    style = "<w:rPr><w:b/><w:bCs/></w:rPr>" if paragraph.bold else ""
    return f'<w:p><w:r>{style}<w:t xml:space="preserve">{escape(paragraph.text)}</w:t></w:r></w:p>'


def coreXml(title: str) -> str:
    """No dates and no author: the file must not depend on when or by whom it was generated."""
    return (
        HEADER
        + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{escape(title)}</dc:title>"
        "</cp:coreProperties>"
    )


def documentXml(paragraphs: Sequence[Paragraph]) -> str:
    body = "".join(paragraphXml(paragraph) for paragraph in paragraphs)
    return HEADER + f'<w:document xmlns:w="{W}"><w:body>{body}{PAGE}</w:body></w:document>'


def buildDocx(title: str, paragraphs: Sequence[Paragraph]) -> bytes:
    parts = [
        ("[Content_Types].xml", CONTENT_TYPES),
        ("_rels/.rels", PACKAGE_RELS),
        ("docProps/core.xml", coreXml(title)),
        ("word/document.xml", documentXml(paragraphs)),
        ("word/_rels/document.xml.rels", DOCUMENT_RELS),
        ("word/styles.xml", STYLES),
    ]
    return storedZip([(name, xml.encode("utf-8")) for name, xml in parts])
