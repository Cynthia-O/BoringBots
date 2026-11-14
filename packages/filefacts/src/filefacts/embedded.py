"""Metadata stored inside some document formats: authors and dates. Read-only.

The filesystem records when a file was created and modified on this disk, and
who owns it, which on a personal Mac is almost always the same person. Some
formats also store authors and dates inside the file, and these travel with
the file when it is copied or downloaded:

- Office Open XML (Word, Excel, PowerPoint): docProps/core.xml holds
  dc:creator and cp:lastModifiedBy, and dcterms:created and dcterms:modified.
- OpenDocument: meta.xml holds meta:initial-creator (who created it) and
  dc:creator (who last modified it), and meta:creation-date and dc:date.
- PDF: the document information holds /CreationDate and /ModDate (no authors
  are read from PDFs).

Every other format, and any file that cannot be read, gives empty values.
"""

import logging
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import NamedTuple
from xml.etree import ElementTree

OOXML_EXTENSIONS = frozenset(
    {".docx", ".docm", ".dotx", ".xlsx", ".xlsm", ".xltx", ".pptx", ".pptm", ".potx"}
)
ODF_EXTENSIONS = frozenset({".odt", ".ods", ".odp", ".odg"})
PDF_EXTENSIONS = frozenset({".pdf"})
DATED_EXTENSIONS = OOXML_EXTENSIONS | ODF_EXTENSIONS | PDF_EXTENSIONS

# The metadata part is tiny; anything bigger is malformed or hostile.
_MAX_METADATA_BYTES = 1024 * 1024
# Dates before this are placeholders (Office writes 1601 or 1980 for unknown
# dates), not real ones.
_EARLIEST_REAL_YEAR = 1985

_DC = "{http://purl.org/dc/elements/1.1/}"
_CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
_DCTERMS = "{http://purl.org/dc/terms/}"
_ODF_META = "{urn:oasis:names:tc:opendocument:xmlns:meta:1.0}"


class Authors(NamedTuple):
    created_by: str | None = None
    modified_by: str | None = None


class EmbeddedDates(NamedTuple):
    created: date | None = None
    modified: date | None = None


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


def read_dates(path: Path) -> EmbeddedDates:
    """Created and modified dates recorded inside the file, where its format
    stores them. Times are dropped: each is the calendar date as written."""
    suffix = path.suffix.lower()
    if suffix in OOXML_EXTENSIONS:
        root = _read_xml(path, "docProps/core.xml")
        if root is None:
            return EmbeddedDates()
        return EmbeddedDates(
            _iso_date(_text(root, f"{_DCTERMS}created")),
            _iso_date(_text(root, f"{_DCTERMS}modified")),
        )
    if suffix in ODF_EXTENSIONS:
        root = _read_xml(path, "meta.xml")
        if root is None:
            return EmbeddedDates()
        return EmbeddedDates(
            _iso_date(_text(root, f".//{_ODF_META}creation-date")),
            _iso_date(_text(root, f".//{_DC}date")),
        )
    if suffix in PDF_EXTENSIONS:
        return _pdf_dates(path)
    return EmbeddedDates()


def _pdf_dates(path: Path) -> EmbeddedDates:
    from pypdf import PdfReader  # imported here: only needed when a PDF is read

    # pypdf logs a warning for every malformed PDF it copes with; those are
    # expected in a real folder and not worth showing.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        info = PdfReader(path, strict=False).metadata
        if info is None:
            return EmbeddedDates()
        return EmbeddedDates(
            _real(_safe(lambda: info.creation_date)), _real(_safe(lambda: info.modification_date))
        )
    except Exception:  # noqa: BLE001 - a damaged or hostile PDF has no dates, whatever went wrong
        return EmbeddedDates()


def _safe(get) -> datetime | None:
    try:
        return get()
    except Exception:  # noqa: BLE001 - a malformed date string is no date
        return None


def _iso_date(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return _real(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _real(value: datetime | None) -> date | None:
    if value is None or value.year < _EARLIEST_REAL_YEAR:
        return None
    return value.date()


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
