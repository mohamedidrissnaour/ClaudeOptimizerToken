"""Capture identity and plan a single selection replacement, independent of Windows."""

from dataclasses import dataclass, field
from enum import Enum

from .engine import Optimization, Options, cuts_protected_span, estimate, optimize


class Mode(Enum):
    COUNT = 'count'
    OPTIMIZE = 'optimize'


class EditorError(RuntimeError):
    """Action refused without a verified editor, selection or safe paste."""


def normalize_lines(text: str) -> str:
    return text.replace('\r\n', '\n').replace('\r', '\n')


def locate_selection(document: str, prefix: str, selected: str) -> tuple[int, int]:
    document, prefix, selected = map(normalize_lines, (document, prefix, selected))
    if not selected or not document.startswith(prefix + selected):
        return 0, 0
    return len(prefix), len(prefix) + len(selected)


def has_draft_text(text: str | None) -> bool:
    return bool((text or '').strip(' \t\r\n\u200b\ufeff'))


@dataclass(frozen=True, slots=True)
class DraftObservation:
    has_text: bool
    bounds: tuple[int, int, int, int] | None
    responding: bool | None = None


@dataclass(frozen=True, slots=True)
class Capture:
    window: int
    pid: int
    runtime: tuple[int, ...]
    text: str
    start: int = 0
    end: int = 0
    bounds: tuple[int, int, int, int] | None = field(default=None, compare=False)
    responding: bool | None = field(default=None, compare=False)

    @property
    def selected(self) -> str:
        if not 0 <= self.start < self.end <= len(self.text):
            return ''
        return self.text[self.start:self.end]


@dataclass(frozen=True, slots=True)
class Prepared:
    mode: Mode
    capture: Capture
    count: int
    optimization: Optimization | None = None

    @property
    def replacement(self) -> str | None:
        if self.optimization and self.optimization.changed:
            return self.optimization.text
        return None

    @property
    def expected_draft(self) -> str:
        original = self.capture
        replacement = self.replacement
        if replacement is None:
            return original.text
        return original.text[:original.start] + replacement + original.text[original.end:]


def prepare(mode: Mode, capture: Capture, options: Options) -> Prepared:
    if mode is Mode.COUNT:
        return Prepared(mode, capture, estimate(capture.text))
    if not capture.selected.strip():
        raise EditorError('Sélectionne du texte dans le champ de Claude, puis réessaie.')
    if cuts_protected_span(capture.text, capture.start, capture.end):
        raise EditorError('La sélection coupe une citation, un lien ou du code. Sélectionne le passage entier ou du texte libre.')
    result = optimize(capture.selected, options)
    return Prepared(mode, capture, result.before, result)


def can_replace(prepared: Prepared, current: Capture | None) -> bool:
    return (prepared.mode is Mode.OPTIMIZE and prepared.replacement is not None
            and bool(prepared.capture.selected.strip()) and current == prepared.capture)
