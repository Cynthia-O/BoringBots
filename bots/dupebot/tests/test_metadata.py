import zipfile

from dupebot.metadata import Authors, read_authors

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
