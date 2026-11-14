from filefacts.clutter import is_clutter


def test_system_and_lock_files_are_clutter():
    for name in (".DS_Store", "Thumbs.db", "~$report.docx", ".~lock.report.odt#"):
        assert is_clutter(name)


def test_ordinary_files_are_not():
    for name in ("report.docx", "IMG_1234.jpg", ".env"):
        assert not is_clutter(name)
