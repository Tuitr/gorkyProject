"""ZIP files with the same bytes on every run and every OS.

A DOCX is a ZIP too, so both the Word files and the bundles of submissions go through storedZip.
Three things would otherwise change the bytes between runs: the current time in every entry header,
the OS that wrote the entry (zipfile records Windows and Linux differently) and the deflate
implementation of the local zlib. Entries therefore carry a fixed time and OS and are stored
without compression; the order is the order of the given entries.
"""

import io
import zipfile
from collections.abc import Sequence

FIXED_TIME = (1980, 1, 1, 0, 0, 0)
"""The earliest time a ZIP header can hold; it says nothing about when the file was generated."""
UNIX = 3
REGULAR_FILE = 0o100644 << 16


def storedZip(entries: Sequence[tuple[str, bytes]]) -> bytes:
    names = [name for name, _ in entries]
    if len(set(names)) != len(names):
        raise ValueError("ZIP entry names repeat")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = UNIX
            info.external_attr = REGULAR_FILE
            archive.writestr(info, data)
    return buffer.getvalue()
