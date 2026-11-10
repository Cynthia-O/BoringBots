import pytest

from dupebot.cleaning import clean_name, exclusion_reason


@pytest.mark.parametrize(
    ("filename", "cleaned"),
    [
        ("Budget Review.xlsx", "budget review"),
        ("budget_review_2023-01-15.xlsx", "budget review"),
        ("Budget Review 20230115.xlsx", "budget review"),
        ("budget review 01.15.2023.xlsx", "budget review"),
        ("Budget Review Jan 15, 2023.xlsx", "budget review"),
        ("budget_review_march_2023.xlsx", "budget review"),
        ("2023 Budget Review.xlsx", "budget review"),
        ("FINAL_budget_review.xlsx", "budget review"),
        ("budget_review_v1.0.xlsx", "budget review"),
        ("budget_review_1.0.xlsx", "budget review"),
        ("budget_review_version_2.xlsx", "budget review"),
        ("budget_review_final_v2_jb.xlsx", "budget review"),
        ("budget_review_co_edits.xlsx", "budget review"),
        ("Copy of Budget Review.xlsx", "budget review"),
        ("Budget Review (1).xlsx", "budget review"),
        ("Budget Review - Copy.xlsx", "budget review"),
        ("budget_review_DE.xlsx", "budget review"),
        ("USA_budget_review.xlsx", "budget review"),
        ("budget_review_for_executives.xlsx", "budget review"),
        ("budget review for new hires.xlsx", "budget review"),
    ],
)
def test_clean_name_strips_dates_and_markers(filename, cleaned):
    assert clean_name(filename).cleaned == cleaned


def test_clean_name_keeps_everything_it_strips():
    name = clean_name("FINAL_Budget_Review_2023-01-15_v2_DE_jb (1).docx")
    assert name.original == "FINAL_Budget_Review_2023-01-15_v2_DE_jb (1).docx"
    assert name.cleaned == "budget review"
    assert name.dates == ("2023-01-15",)
    assert name.version_markers == ("FINAL", "(1)", "v2", "jb")
    assert name.set_markers == ("DE",)


def test_clean_name_keeps_multiword_markers_with_their_separators():
    name = clean_name("handbook_for_technicians_co_edits.pdf")
    assert name.cleaned == "handbook"
    assert name.version_markers == ("co_edits",)
    assert name.set_markers == ("for_technicians",)


@pytest.mark.parametrize(
    ("filename", "cleaned"),
    [
        ("Form 1040 instructions.pdf", "form 1040 instructions"),  # not a year
        ("Chapter 3.pdf", "chapter 3"),  # bare numbers stay
        ("design_notes.md", "design notes"),  # "de" inside a word is not a marker
        ("recipes_de.txt", "recipes"),  # lowercase "de": treated as initials, still kept
        ("May newsletter.docx", "may newsletter"),  # month without a year stays
    ],
)
def test_clean_name_leaves_ordinary_words_alone(filename, cleaned):
    assert clean_name(filename).cleaned == cleaned


def test_lowercase_two_letter_word_is_not_a_set_marker():
    assert clean_name("recipes_de.txt").set_markers == ()


def test_name_with_only_markers_cleans_to_empty():
    assert clean_name("FINAL_2024-03-01.docx").cleaned == ""


@pytest.mark.parametrize(
    "filename",
    [
        ".DS_Store",
        "Thumbs.db",
        "~$report.docx",
        ".~lock.report.odt#",
        "IMG_1234.jpg",
        "DSC01234.jpg",
        "PXL_20230115_123456.jpg",
        "20261012_142703.jpg",
        "20261012_142703_01.jpg",
        "20261012_142703(1).jpg",
        "Screenshot 2023-01-15 at 10.15.32.png",
        "Scan 12.pdf",
        "index.html",
        "README.md",
        "Untitled 3.docx",
        "document1.docx",
        "index_v2_final.html",  # generic once cleaned
    ],
)
def test_excluded_names(filename):
    assert exclusion_reason(clean_name(filename)) == "excluded"


@pytest.mark.parametrize(
    "filename",
    [
        "Budget Review.xlsx",
        "index of suppliers.xlsx",
        "20250815_transfer_to_savings.pdf",  # dated, but named by a person
        "20261012_142703_receipt.jpg",
    ],
)
def test_ordinary_names_are_not_excluded(filename):
    assert exclusion_reason(clean_name(filename)) is None
