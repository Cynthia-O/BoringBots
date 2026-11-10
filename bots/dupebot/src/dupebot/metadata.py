"""Author metadata stored inside some document formats. Read-only.

The filesystem does not record who modified a file, only who owns it, which on a
personal Mac is almost always the same person. Some formats store authors inside
the file itself, and those are read here:

- Office Open XML (Word, Excel, PowerPoint): docProps/core.xml holds
  dc:creator (who created it) and cp:lastModifiedBy.
- OpenDocument: meta.xml holds meta:initial-creator (who created it) and
  dc:creator (who last modified it).

Every other format, and any file that cannot be read, gives (None, None).
"""

import zipfile
from pathlib import Path
from typing import NamedTuple
from xml.etree import ElementTree

OOXML_EXTENSIONS = frozenset(
    {".docx", ".docm", ".dotx", ".xlsx", ".xlsm", ".xltx", ".pptx", ".pptm", ".potx"}
)
ODF_EXTENSIONS = frozenset({".odt", ".ods", ".odp", ".odg"})

# The metadata part is tiny; anything bigger is malformed or hostile.
_MAX_METADATA_BYTES = 1024 * 1024

_DC = "{http://purl.org/dc/elements/1.1/}"
_CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
_ODF_META = "{urn:oasis:names:tc:opendocument:xmlns:meta:1.0}"


class Authors(NamedTuple):
    created_by: str | None = None
    modified_by: str | None = None


def read_authors(path: Path) -> Authors:
    """Authors recorded inside the file, where its format stores them."""
    suffix = path.suffix.lower()
    if suffix in OOXML_EXTENSIONS:
        root = _read_xml(path, "docProps/core.xml")
        if root is None:
            return Authors()
        return Authors(_text(root, f"{_DC}creator"), _text(root, f"{_CP}lastModifiedBy"))
    if suffix in ODF_EXTENSIONS:
        root = _read_xml(path, "meta.xml")
        if root is None:
            return Authors()
        return Authors(
            _text(root, f".//{_ODF_META}initial-creator"), _text(root, f".//{_DC}creator")
        )
    return Authors()


def _read_xml(path: Path, member: str) -> ElementTree.Element | None:
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.getinfo(member).file_size > _MAX_METADATA_BYTES:
                return None
            return ElementTree.fromstring(archive.read(member))
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError, ValueError):
        return None


def _text(root: ElementTree.Element, path: str) -> str | None:
    element = root.find(path)
    if element is None or element.text is None:
        return None
    return element.text.strip() or None
