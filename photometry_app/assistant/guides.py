"""Keyword search over the Markdown guides that ship with CAst."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from pathlib import Path
import re
import threading

from photometry_app.app_metadata import application_root_path
from photometry_app.assistant.program_map import mode_names_for_guide


DEFAULT_SECTION_CHARS = 8_000
_SNIPPET_CHARS = 500
_HEADING_PATTERN = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$")
_FENCE_PATTERN = re.compile(r"^\s*(```|~~~)")
_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_WORD_PATTERN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a all also an and any are as at be been but by can cast could do does each for from has have how i if in "
    "into is it its me more my no not of on one only or our so some such than that the their them then there "
    "these they this those to us use using want was we were what when where which will with would you your".split()
)
_HEADING_WEIGHT = 3
_BM25_K1 = 1.2
_BM25_B = 0.75


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 5 and word.endswith("ing"):
        word = word[:-3]
    elif len(word) > 4 and word.endswith("ed"):
        word = word[:-2]
    elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    if len(word) > 4 and word.endswith("e"):
        word = word[:-1]
    return word


def search_terms(text: str) -> list[str]:
    return [_stem(word) for word in _WORD_PATTERN.findall(text.lower()) if word not in _STOPWORDS]


def _slug(text: str) -> str:
    return "-".join(_WORD_PATTERN.findall(text.lower())) or "section"


def _clean_line(line: str) -> str:
    line = _IMAGE_PATTERN.sub("", line)
    line = _LINK_PATTERN.sub(r"\1", line)
    return _HTML_TAG_PATTERN.sub("", line).rstrip()


@dataclass(frozen=True, slots=True)
class GuideSection:
    section_id: str
    guide_id: str
    guide_title: str
    level: int
    breadcrumb: tuple[str, ...]
    text: str

    @property
    def heading(self) -> str:
        return self.breadcrumb[-1] if self.breadcrumb else self.guide_title

    @property
    def path_label(self) -> str:
        return " > ".join(self.breadcrumb)


@dataclass(frozen=True, slots=True)
class Guide:
    guide_id: str
    title: str
    sections: tuple[GuideSection, ...]


@dataclass(frozen=True, slots=True)
class GuideHit:
    section: GuideSection
    score: float
    snippet: str


def parse_guide(guide_id: str, markdown: str) -> Guide:
    title = guide_id.replace("_", " ").title()
    sections: list[GuideSection] = []
    used_ids: Counter[str] = Counter()
    stack: list[tuple[int, str]] = []
    current_level = 0
    current_lines: list[str] = []
    in_fence = False
    title_found = False

    def flush() -> None:
        if not stack:
            return
        text = "\n".join(current_lines).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        base_id = f"{guide_id}#{_slug(stack[-1][1])}"
        used_ids[base_id] += 1
        section_id = base_id if used_ids[base_id] == 1 else f"{base_id}-{used_ids[base_id]}"
        sections.append(
            GuideSection(
                section_id=section_id,
                guide_id=guide_id,
                guide_title=title,
                level=current_level,
                breadcrumb=tuple(heading for _level, heading in stack),
                text=text,
            )
        )

    for raw_line in markdown.splitlines():
        if _FENCE_PATTERN.match(raw_line):
            in_fence = not in_fence
            current_lines.append(raw_line)
            continue
        match = None if in_fence else _HEADING_PATTERN.match(raw_line)
        if match is None:
            if stack:
                current_lines.append(_clean_line(raw_line))
            continue
        flush()
        level = len(match.group(1))
        heading = _clean_line(match.group(2)).strip() or "Section"
        if level == 1 and not title_found:
            title = heading
            title_found = True
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, heading))
        current_level = level
        current_lines = []
    flush()
    return Guide(guide_id=guide_id, title=title, sections=tuple(sections))


class GuideLibrary:
    def __init__(self, guides_dir: Path | None = None) -> None:
        self._guides_dir = guides_dir
        self._lock = threading.Lock()
        self._guides: dict[str, Guide] | None = None
        self._index: _SearchIndex | None = None

    @property
    def guides_dir(self) -> Path:
        return self._guides_dir if self._guides_dir is not None else application_root_path() / "guides"

    def guides(self) -> list[Guide]:
        return list(self._load().values())

    def guide(self, guide_id: str) -> Guide | None:
        return self._load().get(guide_id)

    def section(self, section_id: str) -> GuideSection | None:
        guide_id = section_id.split("#", 1)[0]
        guide = self.guide(guide_id)
        if guide is None:
            return None
        for section in guide.sections:
            if section.section_id == section_id:
                return section
        return None

    def section_with_subsections(self, section_id: str, *, max_chars: int = DEFAULT_SECTION_CHARS) -> tuple[str, list[str]]:
        """Return the section and its subsections as Markdown, plus ids of subsections that did not fit."""
        section = self.section(section_id)
        if section is None:
            return "", []
        guide = self.guide(section.guide_id)
        assert guide is not None
        start = guide.sections.index(section)
        parts: list[str] = []
        omitted: list[str] = []
        used = 0
        for index, item in enumerate(guide.sections[start:]):
            if index > 0 and item.level <= section.level:
                break
            block = f"{'#' * item.level} {item.heading}\n\n{item.text}".strip()
            if index == 0 and len(block) > max_chars:
                block = block[:max_chars].rsplit(" ", 1)[0] + " ..."
            if index > 0 and (omitted or used + len(block) > max_chars):
                omitted.append(item.section_id)
                continue
            parts.append(block)
            used += len(block) + 2
        return "\n\n".join(parts), omitted

    def search(self, query: str, *, limit: int = 5, guide_id: str | None = None) -> list[GuideHit]:
        terms = search_terms(query)
        if not terms:
            return []
        index = self._search_index()
        hits = index.search(terms, limit=limit, guide_id=guide_id)
        return [GuideHit(section=section, score=score, snippet=_snippet(section.text, set(terms))) for section, score in hits]

    def _load(self) -> dict[str, Guide]:
        with self._lock:
            if self._guides is None:
                guides: dict[str, Guide] = {}
                folder = self.guides_dir
                if folder.is_dir():
                    for path in sorted(folder.glob("*.md")):
                        try:
                            markdown = path.read_text(encoding="utf-8")
                        except (OSError, UnicodeDecodeError):
                            continue
                        guides[path.stem] = parse_guide(path.stem, markdown)
                self._guides = guides
            return self._guides

    def _search_index(self) -> _SearchIndex:
        guides = self._load()
        with self._lock:
            if self._index is None:
                self._index = _SearchIndex([section for guide in guides.values() for section in guide.sections])
            return self._index


class _SearchIndex:
    """BM25 over section text, with the heading path and guide/mode names counted extra."""

    def __init__(self, sections: list[GuideSection]) -> None:
        self._sections: list[GuideSection] = []
        self._counts: list[Counter[str]] = []
        self._lengths: list[int] = []
        document_frequency: Counter[str] = Counter()
        for section in sections:
            if not section.text:
                continue
            names = " ".join((section.guide_title, *mode_names_for_guide(section.guide_id)))
            terms = (
                search_terms(section.text)
                + search_terms(section.heading) * _HEADING_WEIGHT
                + search_terms(" ".join(section.breadcrumb[:-1]))
                + search_terms(names)
            )
            counts = Counter(terms)
            self._sections.append(section)
            self._counts.append(counts)
            self._lengths.append(len(terms))
            document_frequency.update(counts.keys())
        total = len(self._sections)
        self._average_length = (sum(self._lengths) / total) if total else 1.0
        self._idf = {term: math.log(1 + (total - count + 0.5) / (count + 0.5)) for term, count in document_frequency.items()}

    def search(self, terms: list[str], *, limit: int, guide_id: str | None) -> list[tuple[GuideSection, float]]:
        unique_terms = list(dict.fromkeys(terms))
        scored: list[tuple[float, int]] = []
        for index, (section, counts) in enumerate(zip(self._sections, self._counts)):
            if guide_id is not None and section.guide_id != guide_id:
                continue
            length_norm = _BM25_K1 * (1 - _BM25_B + _BM25_B * self._lengths[index] / self._average_length)
            score = 0.0
            for term in unique_terms:
                frequency = counts.get(term, 0)
                if frequency:
                    score += self._idf.get(term, 0.0) * frequency * (_BM25_K1 + 1) / (frequency + length_norm)
            if score > 0:
                scored.append((score, index))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [(self._sections[index], round(score, 3)) for score, index in scored[: max(1, limit)]]


def _snippet(text: str, terms: set[str]) -> str:
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]
    if not paragraphs:
        return ""
    _matches, _position, best = max(
        (len(terms.intersection(search_terms(paragraph))), -position, paragraph) for position, paragraph in enumerate(paragraphs)
    )
    best = re.sub(r"\s+", " ", best)
    if len(best) <= _SNIPPET_CHARS:
        return best
    return best[:_SNIPPET_CHARS].rsplit(" ", 1)[0] + " ..."
