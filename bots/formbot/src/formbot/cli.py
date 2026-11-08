"""Command-line entry point.

FormBot only advises. It never modifies the form; the user copies text in themselves.
"""

import argparse
from pathlib import Path

SUPPORTED_SUFFIXES = {".pdf", ".docx"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="formbot",
        description="Get help filling out a PDF or Word form, one field at a time.",
    )
    parser.add_argument("form", type=Path, help="Path to a .pdf or .docx form")
    args = parser.parse_args(argv)

    if not args.form.is_file():
        parser.error(f"not a file: {args.form}")
    if args.form.suffix.lower() not in SUPPORTED_SUFFIXES:
        parser.error(f"unsupported form type {args.form.suffix!r}; expected .pdf or .docx")

    print(f"Loaded {args.form.name}.")
    # TODO: 1) extract fields, 2) research unclear fields (session cache),
    #       3) walk the user through each field conversationally.
    return 0
