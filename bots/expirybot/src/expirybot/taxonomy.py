"""The retention taxonomy: groups of documents, how long to keep each, and
which date a document's age is counted from. Plain Python, no LLM.

A taxonomy is a JSON file, for now written by hand (see examples/taxonomy.json):

    {
      "name": "Home documents",
      "groups": [
        {
          "name": "Tax",
          "description": "Returns and the forms behind them",
          "match": {"folders": ["tax"], "words": ["w-2", "1099"], "extensions": [".pdf"]},
          "keep_years": 7,
          "age_date": ["modified", "name"],
          "subgroups": [{"name": "Returns", "match": {"words": ["1040"]}, "keep_years": "forever"}]
        }
      ]
    }

Matching: every match rule a group gives must hold, and within a rule any one
term is enough. "folders" terms are looked for in each folder name on the
file's path, and "words" terms in the file name without its extension; both
ignore case and match anywhere in the name ("tax" matches "Taxes 2021").
"extensions" must match exactly. Groups are tried in order and the first match
wins. A file that matches a group is then tried against its subgroups the same
way, and stays in the group if none matches.

Inheritance: a subgroup without keep_years or age_date uses its parent's.
Top-level groups must give keep_years. age_date is an order of date sources:
the first one a file has is used. A top-level group without one uses
DEFAULT_AGE_DATE.
"""

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from expirybot.survey import DATE_SOURCES, FileFacts

FOREVER = "forever"
DEFAULT_AGE_DATE = ("modified",)
MATCH_KEYS = ("folders", "words", "extensions")
_GROUP_KEYS = frozenset(
    {"name", "description", "match", "keep_years", "age_date", "subgroups", "notes"}
)
_TOP_KEYS = frozenset({"name", "description", "groups", "notes"})


class TaxonomyError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass(frozen=True)
class Match:
    folders: tuple[str, ...] = ()  # lowercased
    words: tuple[str, ...] = ()  # lowercased
    extensions: tuple[str, ...] = ()  # lowercased, with the leading dot

    def matches(self, relpath: PurePosixPath) -> bool:
        if self.extensions and relpath.suffix.lower() not in self.extensions:
            return False
        if self.words:
            stem = relpath.stem.lower()
            if not any(w in stem for w in self.words):
                return False
        if self.folders:
            folders = [f.lower() for f in relpath.parent.parts]
            if not any(term in folder for term in self.folders for folder in folders):
                return False
        return True


@dataclass(frozen=True)
class Group:
    name: str
    match: Match
    description: str = ""
    keep_years: int | str | None = None  # years, FOREVER, or None to inherit
    age_date: tuple[str, ...] | None = None  # None to inherit
    subgroups: tuple["Group", ...] = ()


@dataclass(frozen=True)
class Placement:
    """Where a file landed, with the settings it inherits there."""

    path: tuple[Group, ...]  # top-level group first, deepest last
    keep_years: int | str
    age_date: tuple[str, ...]

    @property
    def label(self) -> str:
        return " / ".join(g.name for g in self.path)


@dataclass(frozen=True)
class Taxonomy:
    name: str
    groups: tuple[Group, ...]
    description: str = ""
    source: Path | None = None  # the file it was read from

    def place(self, file: FileFacts) -> Placement | None:
        """The group a file belongs to, or None if no group matches it."""
        path = _first_match(self.groups, file.relpath)
        if not path:
            return None
        keep = next(g.keep_years for g in reversed(path) if g.keep_years is not None)
        order = next((g.age_date for g in reversed(path) if g.age_date), DEFAULT_AGE_DATE)
        return Placement(tuple(path), keep, order)

    def walk(self):
        """Every group with its depth (0 for top level), parents before children."""

        def visit(groups, depth):
            for g in groups:
                yield g, depth
                yield from visit(g.subgroups, depth + 1)

        yield from visit(self.groups, 0)


def _first_match(groups: tuple[Group, ...], relpath: PurePosixPath) -> list[Group]:
    for g in groups:
        if g.match.matches(relpath):
            return [g, *_first_match(g.subgroups, relpath)]
    return []


def load_taxonomy(path: Path) -> Taxonomy:
    """Read and check a taxonomy file. Raises TaxonomyError listing every problem."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise TaxonomyError([f"cannot read {path}: {e.strerror}"]) from None
    except json.JSONDecodeError as e:
        raise TaxonomyError([f"{path} is not valid JSON: {e}"]) from None
    taxonomy = parse_taxonomy(data)
    return Taxonomy(taxonomy.name, taxonomy.groups, taxonomy.description, source=path)


def parse_taxonomy(data: object) -> Taxonomy:
    problems: list[str] = []
    if not isinstance(data, dict):
        raise TaxonomyError(["a taxonomy must be a JSON object"])
    _unknown_keys(data, _TOP_KEYS, "taxonomy", problems)
    groups_data = data.get("groups")
    if not isinstance(groups_data, list) or not groups_data:
        problems.append('"groups" must be a non-empty list')
        groups_data = []
    groups = tuple(
        _parse_group(g, f"groups[{i}]", top=True, problems=problems)
        for i, g in enumerate(groups_data)
    )
    _unique_names(groups, "the top level", problems)
    if problems:
        raise TaxonomyError(problems)
    return Taxonomy(
        str(data.get("name") or "Untitled taxonomy"), groups, str(data.get("description") or "")
    )


def _parse_group(data: object, where: str, top: bool, problems: list[str]) -> Group:
    if not isinstance(data, dict):
        problems.append(f"{where} must be an object")
        return Group("?", Match())
    _unknown_keys(data, _GROUP_KEYS, where, problems)
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        problems.append(f"{where} needs a name")
        name = "?"
    where = f'{where} ("{name}")'

    keep = data.get("keep_years")
    if keep is None:
        if top:
            problems.append(f'{where} needs keep_years (a whole number of years, or "{FOREVER}")')
    elif keep != FOREVER and (isinstance(keep, bool) or not isinstance(keep, int) or keep < 1):
        problems.append(f'{where}: keep_years must be a whole number of years, or "{FOREVER}"')

    age_date = data.get("age_date")
    if age_date is not None:
        if not isinstance(age_date, list) or not age_date:
            problems.append(f"{where}: age_date must be a non-empty list of date sources")
            age_date = None
        else:
            unknown = [s for s in age_date if s not in DATE_SOURCES]
            if unknown:
                problems.append(
                    f"{where}: unknown date source(s) {unknown}; use {list(DATE_SOURCES)}"
                )
            age_date = tuple(age_date)

    subgroups_data = data.get("subgroups", [])
    if not isinstance(subgroups_data, list):
        problems.append(f"{where}: subgroups must be a list")
        subgroups_data = []
    subgroups = tuple(
        _parse_group(g, f"{where}.subgroups[{i}]", top=False, problems=problems)
        for i, g in enumerate(subgroups_data)
    )
    _unique_names(subgroups, where, problems)
    return Group(
        name=name,
        match=_parse_match(data.get("match"), where, problems),
        description=str(data.get("description") or ""),
        keep_years=keep,
        age_date=age_date,
        subgroups=subgroups,
    )


def _parse_match(data: object, where: str, problems: list[str]) -> Match:
    if not isinstance(data, dict):
        problems.append(f"{where} needs match rules: {list(MATCH_KEYS)}")
        return Match()
    _unknown_keys(data, frozenset(MATCH_KEYS), f"{where}.match", problems)
    terms: dict[str, tuple[str, ...]] = {}
    for key in MATCH_KEYS:
        value = data.get(key, [])
        if not isinstance(value, list) or not all(isinstance(t, str) and t.strip() for t in value):
            problems.append(f"{where}.match.{key} must be a list of non-empty strings")
            value = []
        terms[key] = tuple(t.strip().lower() for t in value)
    terms["extensions"] = tuple(e if e.startswith(".") else f".{e}" for e in terms["extensions"])
    if not any(terms.values()):
        problems.append(f"{where} needs at least one match rule: {list(MATCH_KEYS)}")
    return Match(**terms)


def _unique_names(groups: tuple[Group, ...], where: str, problems: list[str]) -> None:
    seen: set[str] = set()
    for g in groups:
        if g.name in seen:
            problems.append(f'two groups named "{g.name}" in {where}')
        seen.add(g.name)


def _unknown_keys(data: dict, allowed: frozenset[str], where: str, problems: list[str]) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        problems.append(f"{where}: unknown key(s) {unknown}; allowed: {sorted(allowed)}")
