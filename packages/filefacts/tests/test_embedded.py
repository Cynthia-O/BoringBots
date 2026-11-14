import zipfile
from datetime import date

from pypdf import PdfWriter

from filefacts.embedded import Authors, EmbeddedDates, read_authors, read_dates

OOXML_CORE = """<?xml version="1.0" encoding="UTF-8"?>
<cp:coreProperties
    xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
    xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:creator>Jane Brown</dc:creator>
  <cp:lastModifiedBy>Raj Patel</cp:lastModifiedBy>
</cp:coreProperties>"""

ODF_META = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-meta
    xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
    xmlns:dc="http://purl.org/dc/elements/1.1/">
  <office:meta>
    <meta:initial-creator>Jane Brown</meta:initial-creator>
    <dc:creator>Raj Patel</dc:creator>
  </office:meta>
</office:document-meta>"""


def make_zip(path, member, text):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(member, text)
    return path


def test_reads_office_authors(tmp_path):
    path = make_zip(tmp_path / "report.docx", "docProps/core.xml", OOXML_CORE)
    assert read_authors(path) == Authors("Jane Brown", "Raj Patel")


def test_reads_opendocument_authors(tmp_path):
    path = make_zip(tmp_path / "report.odt", "meta.xml", ODF_META)
    assert read_authors(path) == Authors("Jane Brown", "Raj Patel")


def test_unreadable_or_unsupported_files_give_no_authors(tmp_path):
    not_a_zip = tmp_path / "broken.docx"
    not_a_zip.write_text("not a zip")
    no_metadata = make_zip(tmp_path / "empty.xlsx", "xl/workbook.xml", "<x/>")
    bad_xml = make_zip(tmp_path / "bad.pptx", "docProps/core.xml", "<unclosed")
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    for path in (not_a_zip, no_metadata, bad_xml, pdf, tmp_path / "missing.docx"):
        assert read_authors(path) == Authors(None, None)


OOXML_DATES = """<?xml version="1.0" encoding="UTF-8"?>
<cp:coreProperties
    xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
    xmlns:dcterms="http://purl.org/dc/terms/">
  <dcterms:created>2019-03-04T10:00:00Z</dcterms:created>
  <dcterms:modified>2021-11-30T23:00:00Z</dcterms:modified>
</cp:coreProperties>"""

ODF_DATES = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-meta
    xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
    xmlns:dc="http://purl.org/dc/elements/1.1/">
  <office:meta>
    <meta:creation-date>2018-01-02T09:00:00</meta:creation-date>
    <dc:date>2020-05-06T12:30:00.123</dc:date>
  </office:meta>
</office:document-meta>"""


def test_reads_office_dates(tmp_path):
    path = make_zip(tmp_path / "report.docx", "docProps/core.xml", OOXML_DATES)
    assert read_dates(path) == EmbeddedDates(date(2019, 3, 4), date(2021, 11, 30))


def test_reads_opendocument_dates(tmp_path):
    path = make_zip(tmp_path / "report.odt", "meta.xml", ODF_DATES)
    assert read_dates(path) == EmbeddedDates(date(2018, 1, 2), date(2020, 5, 6))


def test_reads_pdf_dates(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(72, 72)
    writer.add_metadata({"/CreationDate": "D:20200115103000Z", "/ModDate": "D:20210203"})
    path = tmp_path / "w2.pdf"
    with path.open("wb") as f:
        writer.write(f)
    assert read_dates(path) == EmbeddedDates(date(2020, 1, 15), date(2021, 2, 3))


def test_placeholder_dates_are_ignored(tmp_path):
    text = OOXML_DATES.replace("2019-03-04", "1601-01-01")
    path = make_zip(tmp_path / "report.docx", "docProps/core.xml", text)
    assert read_dates(path).created is None


def test_unreadable_or_unsupported_files_give_no_dates(tmp_path):
    damaged = tmp_path / "damaged.pdf"
    damaged.write_bytes(b"%PDF-1.4 not really")
    not_a_zip = tmp_path / "broken.docx"
    not_a_zip.write_text("not a zip")
    text = tmp_path / "notes.txt"
    text.write_text("hello")
    for path in (damaged, not_a_zip, text, tmp_path / "missing.pdf"):
        assert read_dates(path) == EmbeddedDates(None, None)
