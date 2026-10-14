"""Extract article metadata from CSLAW Markdown files without dependencies.

Run ``python3 scr/md_metadata.py --help`` for configurable input/output paths.
Missing sections remain null; recovery and uncertainty are recorded separately.
"""

from __future__ import annotations

import argparse
import calendar
from dataclasses import dataclass
import html
import json
from pathlib import Path
import re
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "2026-10-13_cslaw_articles.json"
LABEL = re.compile(
    r"^(abstract|ccs\s+concepts|keywords|additional key words and phrases|"
    r"acm reference format)\s*(?::\s*(.*))?$", re.IGNORECASE,
)
SECTION = re.compile(r"^\d{1,2}(?:\.\d{1,2})*[.\s]+[A-Z]\w")
INTRO = re.compile(r"^1[.\s]+Introduction\b", re.IGNORECASE)
DOI = re.compile(r"\b10\.\d{4,9}/[-._;()/A-Z0-9]+", re.IGNORECASE)
PLACEHOLDER = re.compile(r"insert\b|make sure\b|conference acronym|[’']XX\b|nnnn|xxxx", re.IGNORECASE)
FOOTER_START = re.compile(
    r"^(?:Permission to make|This work (?:is licensed|has been supported)|"
    r"This material is based upon|[∗*†‡§])", re.IGNORECASE,
)
AFFILIATION = re.compile(
    r"\b(?:University|Universität|Department|Institute|School|Faculty|Laboratory|College|"
    r"Center|Centre|Research \||USA|United States)\b", re.IGNORECASE,
)
PLAIN_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
LINK_TARGET = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
FOOTNOTE_MARKER = re.compile(r"\[(?:[∗*†‡§]+|\d+)\]")
HEADING_PREFIX = re.compile(r"^\s*#{1,6}\s+")
UNDERSCORE_EMPHASIS = re.compile(r"(?<!\w)_+|_+(?!\w)")
WHITESPACE = re.compile(r"\s+")
AUTHOR_SEPARATOR = re.compile(r",\s*(?:and\s+)?|\s+(?:and|&)\s+")
INVALID_NAME_CHARACTER = re.compile(r"[@\d:;!?]")
CITATION_PREFIX = re.compile(r"(.+?)\.\s+((?:19|20)\d{2})\.\s+(.+?)\.\s+In\s+", re.IGNORECASE)
PUBLISHER = re.compile(r"\bACM,")
PAGE_COUNT = re.compile(r"\b(\d+)\s+pages?\b", re.IGNORECASE)
PAGE_RANGE = re.compile(r"\bpp?\.\s*(\d+\s*[–-]\s*\d+)", re.IGNORECASE)
VOLUME = re.compile(r"\b(?:vol\.?|volume)\s+(\d+)\b", re.IGNORECASE)
DOI_MENTION = re.compile(r"doi\b|10\.\d{4,9}/", re.IGNORECASE)
DOI_URL = re.compile(r"doi\.org/10\.", re.IGNORECASE)
FOOTER_LINE = re.compile(r"^(?:©|ACM\s+(?:ISBN|\d)|CSLAW\b)", re.IGNORECASE)
DOI_LINE = re.compile(r"^(?:https?://doi\.org/|10\.\d{4,9}/)", re.IGNORECASE)
VENUE_MENTION = re.compile(r"\b(?:CSLAW|CS&Law)\b|\bACM,", re.IGNORECASE)
WORD = re.compile(r"\b[^\W\d_]+(?:-[^\W\d_]+)*\b")
HYPHENATED_LINE_END = re.compile(r"([^\W\d_]+)-$")
WORD_AT_START = re.compile(r"([^\W\d_]+)")
WORD_AT_END = re.compile(r"([^\W\d_]+)$")
PUBLICATION_DATE = re.compile(r",\s*(?:" + "|".join(calendar.month_name[1:]) + r")\b")
HYPHENATED_WRAP = re.compile(r"([^\W\d_]+)-\s+([^\W\d_]+)")
AMBIGUOUS_WORD_SPLIT = re.compile(r"\w-\s+\w")
COPYRIGHT_YEAR = re.compile(r"©\s*((?:19|20)\d{2})")
CONFERENCE_YEAR = re.compile(r"\b(?:CSLAW|CS&Law)\s*[’']\s*(\d{2})", re.IGNORECASE)
TITLE_HEADING = re.compile(r"^#\s+")
DAMAGED_ARROW = re.compile(r"\s!\s")
HIERARCHY_SEPARATOR = re.compile(r"\s*[~!]\s*")
SEMICOLON_SPACING = re.compile(r"\s*;\s*")
CCS_BULLET_SEPARATOR = re.compile(r"(?:^|\s)[•-]\s+")
LIST_BULLET = re.compile(r"^[•-]")
KEYWORD_SEPARATOR = re.compile(r"[,;]")
DAMAGED_CHARACTER = re.compile(r"[\ufffd\ufffe\uffff]")
# These words are the standard CCS category labels, not guesses about prose.
CCS_WORDS = set("applied computing computation theory social professional topics security privacy "
                "information systems software engineering mathematics methodologies organization".split())


def plain(text: str) -> str:
    """Remove presentation syntax but preserve link labels and Unicode text."""
    text = PLAIN_LINK.sub(r"\1", text)
    text = FOOTNOTE_MARKER.sub("", text)
    text = HEADING_PREFIX.sub("", text)
    text = text.replace("*", "").replace("`", "")
    text = UNDERSCORE_EMPHASIS.sub("", text)
    return WHITESPACE.sub(" ", html.unescape(text).replace("\u00ad", "")).strip()


@dataclass
class Line:
    number: int
    raw: str
    text: str
    label: str | None = None
    payload: str = ""


@dataclass
class Citation:
    authors: list[str]
    year: int
    title: str
    publication: str
    pages: str | None
    volume: str | None
    start: int
    end: int


def scan(text: str) -> list[Line]:
    lines = []
    for number, raw in enumerate(text.splitlines(), 1):
        cleaned = plain(raw)
        match = LABEL.fullmatch(cleaned)
        label, payload = None, ""
        if match:
            label = match[1].lower()
            label = {"ccs concepts": "ccs_concepts", "additional key words and phrases": "keywords",
                     "acm reference format": "citation"}.get(label, label)
            payload = match[2] or ""
        lines.append(Line(number, raw, cleaned, label, payload))
    return lines


def names(text: str, headers: set[str]) -> list[str] | None:
    """Accept name lists, rather than prose preceding a displaced citation."""
    result = AUTHOR_SEPARATOR.split(text)
    particles = {"de", "del", "van", "von", "der", "da", "di", "dos", "la", "le", "ter"}
    for name in result:
        words = name.strip().split()
        if not 2 <= len(words) <= 8:
            return None
        if name.strip() not in headers and any(not (word[0].isupper() or word.lower() in particles) for word in words):
            return None
        if any(INVALID_NAME_CHARACTER.search(word) for word in words):
            return None
    return [name.strip() for name in result]


def find_citation(lines: list[Line], marker: int | None) -> Citation | None:
    if marker is None:
        return None
    limit = next((i for i in range(marker + 1, len(lines))
                  if SECTION.match(lines[i].text) or lines[i].text.lower() == "references"), len(lines))
    noise = noise_lines(lines[:limit], None)
    headers = {line.text for line in lines[:limit]}
    for start in range(marker + 1, limit):
        if not lines[start].text or lines[start].label:
            continue
        if start in noise:
            continue
        numbered = [(i, line.text) for i, line in enumerate(lines[start:limit], start)
                    if line.text and i not in noise]
        # Citation text is short; do not let a missing terminator consume a paper.
        numbered = numbered[:40]
        joined = " ".join(value for _, value in numbered)
        match = CITATION_PREFIX.match(joined)
        authors = names(match[1], headers) if match else None
        if not authors:
            continue
        # Author columns can interrupt the venue halfway through a citation.
        with_authors = noise_lines(lines[:limit], None, authors)
        numbered = [(i, line.text) for i, line in enumerate(lines[start:limit], start)
                    if line.text and i not in with_authors][:40]
        joined = " ".join(value for _, value in numbered)
        match = CITATION_PREFIX.match(joined)
        if not match:
            continue
        tail = joined[match.end():]
        publisher = PUBLISHER.search(tail)
        if not publisher:
            continue
        publication = tail[:publisher.start()].strip(" .")
        after = tail[publisher.start():]
        page_count = PAGE_COUNT.search(after)
        page_range = PAGE_RANGE.search(after)
        volume = VOLUME.search(tail)
        pages = page_range[1] if page_range else (page_count[1] + " pages" if page_count else None)
        finish = match.end() + publisher.start() + (page_count.end() if page_count else len("ACM,"))
        offset, end = 0, start
        for i, value in numbered:
            offset += len(value) + 1
            end = i + 1
            if offset >= finish:
                break
        # DOI links are noise for citation grammar, but still belong to its span.
        for i in range(end, limit):
            if not lines[i].text:
                continue
            if DOI_MENTION.search(lines[i].raw):
                end = i + 1
            else:
                break
        return Citation(authors, int(match[2]), match[3].strip(), publication,
                        pages, volume[1] if volume else None, start, end)
    return None


def prose(text: str) -> bool:
    return len(text.split()) >= 8 and not AFFILIATION.search(text)


def author_line(text: str, authors: list[str]) -> bool:
    lowered = text.strip(" ∗†‡§").casefold()
    for author in authors:
        name = author.casefold()
        if WHITESPACE.sub("", lowered) == WHITESPACE.sub("", name):
            return True
        if lowered.startswith(name + ",") and AFFILIATION.search(text):
            return True
    # Some conversions combine names from several author columns on one line.
    remainder = lowered
    for author in authors:
        remainder = remainder.replace(author.casefold(), "")
    return remainder != lowered and not remainder.strip(" ,&∗†‡§")


def noise_lines(lines: list[Line], citation: Citation | None, authors: list[str] | None = None) -> set[int]:
    noise = set()
    in_footer = False
    if authors is None:
        authors = citation.authors if citation else []
    for i, line in enumerate(lines):
        if line.label:
            in_footer = False
        if FOOTER_START.match(line.text):
            in_footer = True
        if in_footer:
            noise.add(i)
            if DOI_URL.search(line.raw):
                in_footer = False
        if FOOTER_LINE.match(line.text):
            noise.add(i)
        if DOI_LINE.match(line.text):
            noise.add(i)
        if "@" in line.text or line.text.isdigit():
            noise.add(i)
        if author_line(line.text, authors):
            noise.add(i)
            for j in range(i + 1, len(lines)):
                candidate = lines[j]
                if (candidate.label or SECTION.match(candidate.text) or prose(candidate.text)
                        or VENUE_MENTION.search(candidate.text)):
                    break
                if FOOTER_START.match(candidate.text):
                    break
                noise.add(j)
    if citation:
        noise.update(range(citation.start, citation.end))
    return noise


def parse_markdown(text: str, source: str = "<memory>", idx: int = 1) -> tuple[dict, list[dict]]:
    """Return one schema-compatible article and diagnostics with source line ranges."""
    lines = scan(text)
    issues = []

    def note(field, kind, message, start=None, end=None):
        issues.append({"source": source, "field": field, "type": kind, "message": message,
                       "lines": [start + 1, (end if end is not None else start + 1)] if start is not None else []})

    markers = {}
    for i, line in enumerate(lines):
        if line.label and line.label not in markers:
            markers[line.label] = i
    citation = find_citation(lines, markers.get("citation"))
    if citation:
        # Repair split citation names only when the full name is printed elsewhere.
        for n, author in enumerate(citation.authors):
            compact = WHITESPACE.sub("", author).casefold()
            for line in lines[:citation.end]:
                if len(line.text.split()) < len(author.split()) and WHITESPACE.sub("", line.text).casefold() == compact:
                    citation.authors[n] = line.text
                    note("author", "recovered", "Matched a split citation name to its author heading.", citation.start, citation.end)
                    break

    anchor = max([citation.end - 1 if citation else 0, *markers.values()], default=0)
    following_intro = next((i for i in range(anchor + 1, len(lines)) if INTRO.match(lines[i].text)), None)
    if following_intro is not None:
        front_end = following_intro
    elif citation and anchor < citation.end:
        front_end = citation.end
    else:
        front_end = next((i for i in range(anchor + 1, len(lines)) if SECTION.match(lines[i].text)), len(lines))
    noise = noise_lines(lines[:front_end], citation)
    vocabulary = set(WORD.findall(plain(text).casefold()))

    def cleaned(field, start, end):
        selected = []
        removed = False
        for i in range(start, end):
            line = lines[i]
            if line.label:
                if line.label == field and line.payload:
                    selected.append((i, line.payload))
                elif line.label != field and line.label != "citation":
                    removed = True
                continue
            if i in noise or SECTION.match(line.text):
                removed = removed or bool(line.text)
                continue
            if line.text:
                selected.append((i, line.text))
        if removed and selected:
            note(field, "recovered", "Removed author, citation, heading, or copyright material.", start, end)
        return selected

    def join(field, selected, paragraphs=False):
        if not selected:
            return ""
        gaps = [b[0] - a[0] - 1 for a, b in zip(selected, selected[1:])]
        # PDF extraction often inserts one blank line between every wrapped line.
        inflated = len(gaps) >= 5 and sum(g > 0 for g in gaps) / len(gaps) > 0.5
        result = selected[0][1]
        for (previous_i, previous), (i, value) in zip(selected, selected[1:]):
            gap = i - previous_i - 1
            gap_is_blank = all(not lines[j].text for j in range(previous_i + 1, i))
            separator = "\n\n" if paragraphs and gap_is_blank and gap >= (2 if inflated else 1) else " "
            left = HYPHENATED_LINE_END.search(previous)
            right = WORD_AT_START.match(value)
            if left and right and separator == " " and i - previous_i <= 2:
                combined = (left[1] + right[1]).casefold()
                hyphenated = (left[1] + "-" + right[1]).casefold()
                field_words = vocabulary | CCS_WORDS if field == "ccs_concepts" else vocabulary
                if combined in field_words and hyphenated not in vocabulary:
                    result = result[:-1] + value
                    continue
                if hyphenated in vocabulary:
                    result += value
                    continue
                note(field, "uncertain", "Preserved an ambiguous hyphen at a line wrap.", previous_i, i + 1)
            elif right and separator == " " and i - previous_i <= 2:
                left = WORD_AT_END.search(previous)
                if left and len(left[1]) <= 5 and (left[1] + right[1]).casefold() in vocabulary:
                    note(field, "uncertain", "Preserved a possible split word at a line wrap.", previous_i, i + 1)
            result += separator + value
        return result

    record = dict.fromkeys(("idx", "author", "title", "journal", "volume", "pages", "year",
                            "abstract", "url", "id", "ccs_concepts", "keywords", "sources"))
    record.update(idx=idx, sources=[source])
    if citation:
        record.update(author=citation.authors, year=citation.year, pages=citation.pages, volume=citation.volume)
        if citation.publication and not PLACEHOLDER.search(citation.publication):
            publication = citation.publication.split("(", 1)[0].strip(" .")
            publication = PUBLICATION_DATE.split(publication, maxsplit=1)[0]
            normalized = HYPHENATED_WRAP.sub(
                lambda m: m[1] + m[2] if (m[1] + m[2]).casefold() in vocabulary else m[0], publication)
            record["journal"] = normalized
            if normalized != publication:
                note("journal", "recovered", "Joined a hyphenated word using another source occurrence.", citation.start, citation.end)
            if AMBIGUOUS_WORD_SPLIT.search(normalized):
                note("journal", "uncertain", "Preserved an ambiguous word split in the publication title.", citation.start, citation.end)
        else:
            note("journal", "uncertain", "Citation publication is empty or contains template placeholders.", citation.start, citation.end)
            if PLACEHOLDER.search(citation.publication):
                record["year"] = None
                note("year", "uncertain", "Year belongs to an incomplete template citation.", citation.start, citation.end)
        copyright_years = {int(m[1]) for line in lines[:front_end]
                           if (m := COPYRIGHT_YEAR.search(line.text))}
        conference_years = {2000 + int(m[1]) for line in lines[:front_end]
                            for m in CONFERENCE_YEAR.finditer(line.text)}
        if (copyright_years and copyright_years != {citation.year}) or (conference_years and conference_years != {citation.year}):
            record["year"] = None
            note("year", "conflict", "Citation, conference, or copyright years disagree.", citation.start, citation.end)

    heading = next((i for i, line in enumerate(lines) if TITLE_HEADING.match(line.raw)), None)
    if heading is not None:
        title = lines[heading].text
        after = next((line for line in lines[heading + 1:] if line.text), None)
        if citation and after and after.text.casefold() in citation.title.casefold() and not author_line(after.text, citation.authors):
            if after.text.casefold() not in title.casefold():
                title = title.rstrip(" :") + ": " + after.text
        record["title"] = title
    elif citation:
        record["title"] = citation.title
        note("title", "recovered", "Used the citation because no top-level title heading was found.", citation.start, citation.end)

    # Search article front matter only: bibliography DOIs belong to other papers.
    def doi_text(raw):
        # Prefer a valid link target; labels can be fragments of the same DOI.
        return LINK_TARGET.sub(
            lambda m: m[2] if DOI.search(m[2]) and not PLACEHOLDER.search(m[2]) else m[1], raw)

    doi_lines = list(lines[:front_end])
    # Some PDFs print the article DOI in a copyright footer after Introduction.
    in_copyright = False
    for line in lines[front_end:]:
        if SECTION.match(line.text) or line.label:
            in_copyright = False
        if line.text.startswith("©"):
            in_copyright = True
        if in_copyright and DOI.search(doi_text(line.raw)):
            doi_lines.append(line)
            in_copyright = False
    dois = {match[0].rstrip(".,;)") for line in doi_lines
            for match in DOI.finditer(doi_text(line.raw)) if not PLACEHOLDER.search(match[0])}
    if len(dois) == 1:
        record["id"] = next(iter(dois))
        record["url"] = "https://doi.org/" + record["id"]
        record["sources"].append(record["url"])
    elif len(dois) > 1:
        for field in ("url", "id"):
            note(field, "conflict", "Multiple article DOI candidates in front matter.", 0, front_end)

    boundaries = [citation.start] if citation else ([markers["citation"]] if "citation" in markers else [])
    ccs = markers.get("ccs_concepts")
    keywords = markers.get("keywords")
    abstract = markers.get("abstract")
    reversed_keywords = keywords is not None and ccs is not None and keywords < ccs

    if abstract is not None:
        stops = [i for i in [ccs, *boundaries] if i is not None and i > abstract]
        if keywords is not None and keywords > abstract and not reversed_keywords:
            stops.append(keywords)
        end = min(stops, default=front_end)
        abstract_lines = cleaned("abstract", abstract, end)
    elif ccs is not None and lines[ccs].payload:
        # The inline ACM template prints an unlabeled abstract after its authors.
        start = heading + 1 if heading is not None else 0
        abstract_lines = cleaned("abstract", start, ccs)
        end = ccs
        if abstract_lines and prose(abstract_lines[0][1]):
            note("abstract", "recovered", "Recovered an unlabeled abstract preceding inline CCS Concepts.", start, ccs)
        else:
            abstract_lines = []
    else:
        abstract_lines, end = [], front_end
    if abstract_lines:
        value = join("abstract", abstract_lines, paragraphs=True)
        unresolved_authors = citation is None and any("@" in line.text for line in lines[abstract or 0:end])
        if unresolved_authors or "@" in value or any(PLACEHOLDER.search(line) for line in value.splitlines()):
            note("abstract", "uncertain", "Abstract boundaries could not be established reliably.", abstract or 0, end)
        else:
            wrapped = []
            for paragraph in value.split("\n\n"):
                if wrapped:
                    wrapped.append("")
                wrapped.extend(textwrap.wrap(paragraph, width=100, break_long_words=False, break_on_hyphens=False))
            record["abstract"] = wrapped
    elif abstract is not None:
        note("abstract", "uncertain", "Abstract heading has no identifiable text.", abstract, end)

    if ccs is not None:
        stops = [i for i in [keywords, *boundaries] if i is not None and i > ccs]
        end = min(stops, default=front_end)
        selected = cleaned("ccs_concepts", ccs, end)
        # A copyright block can separate CCS from a displaced keyword list.
        if reversed_keywords:
            first_noise = next((i for i in range(ccs + 1, end) if i in noise and lines[i].text), end)
            selected = [pair for pair in selected if pair[0] < first_noise]
        value = join("ccs_concepts", selected)
        if "~" in value or DAMAGED_ARROW.search(value):
            value = HIERARCHY_SEPARATOR.sub(" → ", value)
            note("ccs_concepts", "recovered", "Normalized an extracted hierarchy separator to an arrow.", ccs, end)
        groups = [SEMICOLON_SPACING.sub("; ", group).strip(" ;.")
                  for group in CCS_BULLET_SEPARATOR.split(value) if group.strip(" ;.")]
        if groups and all("→" in group or len(group.split()) <= 10 for group in groups):
            record["ccs_concepts"] = groups
        else:
            note("ccs_concepts", "uncertain", "Could not isolate the CCS concept list.", ccs, end)

    if keywords is not None:
        start = ccs + 1 if reversed_keywords else keywords
        stops = [i for i in boundaries if i > start]
        end = min(stops, default=front_end)
        candidates = cleaned("keywords", start, end)
        if reversed_keywords:
            # Keywords in this template are printed after CCS and copyright text.
            candidates = [pair for pair in candidates if "→" not in pair[1] and not LIST_BULLET.match(pair[1])]
            after_noise = max((i for i in range(start, end) if i in noise and lines[i].text), default=start - 1)
            candidates = [pair for pair in candidates if pair[0] > after_noise]
            note("keywords", "recovered", "Located a keyword list after its displaced heading.", keywords, end)
        else:
            first_noise = next((i for i in range(start + 1, end) if i in noise and lines[i].text), end)
            candidates = [pair for pair in candidates if pair[0] < first_noise]
        value = join("keywords", candidates)
        if value and "→" not in value and "@" not in value and len(value.split()) <= 80:
            record["keywords"] = [part.strip(" ;.") for part in KEYWORD_SEPARATOR.split(value) if part.strip(" ;.")]
        else:
            note("keywords", "uncertain", "Could not isolate a keyword list.", keywords, end)

    for field, value in record.items():
        value_text = " ".join(value) if isinstance(value, list) else value
        if isinstance(value_text, str) and DAMAGED_CHARACTER.search(value_text):
            damaged_line = next((i for i in range(markers.get(field, 0), front_end)
                                 if DAMAGED_CHARACTER.search(lines[i].text)), None)
            note(field, "uncertain", "Preserved damaged characters from the source.", damaged_line)
        if value is None and not any(i["field"] == field for i in issues):
            note(field, "missing", "No source value found.")
    return record, issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-dir", type=Path, default=ROOT / "data" / "cslaw", help="Directory recursively containing Markdown files")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Combined JSON output path")
    parser.add_argument("--report", type=Path, help="Diagnostics path; defaults to <output_stem>_report.json")
    args = parser.parse_args(argv)
    input_dir = args.input_dir.expanduser().resolve()
    output = args.output.expanduser().resolve()
    report = args.report.expanduser().resolve() if args.report else output.with_name(output.stem + "_report.json")
    try:
        if not input_dir.is_dir():
            raise ValueError(f"Input directory does not exist: {input_dir}")
        paths = sorted((p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".md"),
                       key=lambda p: (p.name.casefold(), p.relative_to(input_dir).as_posix()))
        if not paths:
            raise ValueError(f"No Markdown files found in {input_dir}")
        if output == report or output in paths or report in paths:
            raise ValueError("Output and report must be distinct and must not overwrite Markdown inputs")
        articles, issues = [], []
        for idx, path in enumerate(paths, 1):
            source = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix()
            article, diagnostics = parse_markdown(path.read_text(encoding="utf-8"), source, idx)
            articles.append(article)
            issues.extend(diagnostics)
        for destination, value in ((output, articles), (report, {"files_parsed": len(paths), "issues": issues})):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Parsed {len(articles)} files into {output}; {len(issues)} diagnostics in {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
