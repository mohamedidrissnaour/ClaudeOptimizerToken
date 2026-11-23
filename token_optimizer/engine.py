"""Offline prompt optimizer and idempotent handling of the user's options."""

from dataclasses import dataclass
from enum import IntEnum
import re

from .counting import estimate
from .text_rules import PROTECTED, english_text, rewrite


class Strength(IntEnum):
    LIGHT = 0
    BALANCED = 1
    AGGRESSIVE = 2
    MAXIMUM = 3


@dataclass(frozen=True, slots=True)
class Options:
    reduce: bool = True
    strength: Strength = Strength.BALANCED
    clarify: bool = True
    examples: bool = False
    example_input: str = ''
    example_output: str = ''
    agent: bool = False
    custom: bool = False
    instructions: str = ''
    local_ai: bool = False


@dataclass(frozen=True, slots=True)
class Optimization:
    original: str
    text: str
    before: int
    after: int

    @property
    def changed(self) -> bool:
        return self.text != self.original

    @property
    def percent(self) -> int:
        return round(100 * (self.after - self.before) / self.before) if self.before else 0


_MISSION = re.compile(r'\A(?:mission[ \t]*:[ \t]*\r?\n(?:[ \t]*\r?\n)*)+', re.I)
_GENERIC_VALIDATION = re.compile(
    r'(?im)^[ \t]{0,3}validation[ \t]*:[ \t]*\r?\n'
    r'[ \t]{0,3}(?:Vérifie que le résultat respecte les contraintes et le format demandés\.|'
    r'Check the result against the requested constraints and format\.)[ \t]*'
    r'(?=\Z|(?:\r?\n[ \t]*)+(?:\Z|(?:validation|exemple|example|consignes supplémentaires|'
    r'additional instructions)[ \t]*:))'
)
_INTENT_HEADING = re.compile(r'\A(?:mission|objectif|contexte|task|goal|context)[ \t]*:', re.I)
_APPENDIX = re.compile(
    r'(?im)^(?P<title>exemple|example|consignes supplémentaires|additional instructions)[ \t]*:[ \t]*\r?\n'
)
_CUSTOM_TITLES = {'consignes supplémentaires', 'additional instructions'}
_NO_DASH = re.compile(
    r'(?i)[ \t]*(?:(?:pas de|aucun|sans|évite les|enlève les|supprime les)[ \t]+tirets?'
    r'|(?:no|without)[ \t]+(?:dashes|hyphens))'
    r'(?:[ \t]+(?:dans le (?:texte|prompt)(?: optimisé)?|de séparation|as separators))?[ \t.!–—-]*'
)


def cuts_protected_span(draft: str, start: int, end: int) -> bool:
    return any(match.start() < end and match.end() > start
               and (start > match.start() or end < match.end())
               for match in PROTECTED.finditer(draft))


def _outside_matches(pattern: re.Pattern, text: str):
    """Never match generated headings inside an example quote or code block."""
    spans = iter(PROTECTED.finditer(text))
    current = next(spans, None)
    for match in pattern.finditer(text):
        while current and current.end() <= match.start():
            current = next(spans, None)
        if current is None or match.end() <= current.start():
            yield match


def _remove_spans(text: str, matches) -> str:
    pieces, position = [], 0
    for match in matches:
        pieces.append(text[position:match.start()])
        position = match.end()
        # Preserve the boundary between surrounding paragraphs.
        while position < len(text) and text[position] in '\r\n':
            position += 1
    pieces.append(text[position:])
    return ''.join(pieces)


def _directives(text: str) -> tuple[bool, str]:
    remaining = []
    no_dashes = False
    for line in text.splitlines():
        if _NO_DASH.fullmatch(line):
            no_dashes = True
        else:
            remaining.append(line)
    return no_dashes, '\n'.join(remaining).strip()


def _split_appendices(text: str):
    first = next(_outside_matches(_APPENDIX, text), None)
    if first is None:
        return text, ''
    # Keep the whole suffix intact. A heading mentioned inside a multiline
    # example or custom instruction is not necessarily an app-owned boundary.
    return text[:first.start()].rstrip(), text[first.start():]


def _repair_legacy_appendices(text: str) -> tuple[str, bool]:
    headers = list(_outside_matches(_APPENDIX, text))
    untouched = []
    no_dashes = False
    for i, header in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        if header['title'].casefold() in _CUSTOM_TITLES and _NO_DASH.fullmatch(text[header.end():end].strip()):
            no_dashes = True
            continue
        untouched.append(text[header.start():end])
    return (''.join(untouched).strip() if headers else text), no_dashes


def _append_once(suffix: str, block: str) -> str:
    if re.search(r'(?:\A|\n)' + re.escape(block) + r'(?=\n|\Z)', suffix):
        return suffix
    return f'{suffix}\n\n{block}' if suffix else block


def _rewrite_body(body: str, options: Options, no_dashes: bool) -> str:
    settings = dict(strength=int(options.strength), reduce=options.reduce, no_dashes=no_dashes)
    single = rewrite(body, clarify=options.clarify, **settings)
    if options.reduce and estimate(single) > estimate(body):
        single = rewrite(body, clarify=False, **settings)
        if estimate(single) > estimate(body):
            single = rewrite(body, strength=0, reduce=False, clarify=False, no_dashes=no_dashes)
    return single


def optimize(source: str, options: Options) -> Optimization:
    before = estimate(source)
    if not source.strip() or '\ufffc' in source:
        return Optimization(source, source, before, before)
    if not (options.reduce or options.clarify or options.examples or options.agent or options.custom):
        return Optimization(source, source, before, before)

    left = len(source) - len(source.lstrip())
    right = len(source.rstrip())
    core = source[left:right]
    indent = source[:left].rsplit('\n', 1)[-1]
    no_dashes, custom = _directives(options.instructions) if options.custom else (False, '')

    # Keep raw data and first-line Markdown indentation from being interpreted
    # as formatting injected by the app.
    literal = ''
    if indent.startswith(('    ', '\t')):
        head, newline, core = core.partition('\n')
        literal = head + newline

    frames = _MISSION.match(core)
    old_validations = list(_outside_matches(_GENERIC_VALIDATION, core)) if frames else []
    legacy = bool(old_validations)
    if legacy:
        core = _remove_spans(core, old_validations).rstrip()
    if frames and (legacy or options.agent):
        core = _MISSION.sub('', core, count=1)

    if core.startswith(('{', '[', '<?xml')):
        body, appendices = core, ''
    else:
        body, appendices = _split_appendices(core)
    if legacy and appendices:
        appendices, inherited_rule = _repair_legacy_appendices(appendices)
        no_dashes |= inherited_rule

    if body:
        result = literal + _rewrite_body(body, options, no_dashes)
    else:
        result = literal
    if not result.strip():
        return Optimization(source, source, before, before)

    english = english_text(body or result)
    if options.agent and not _INTENT_HEADING.match(result):
        result = ('Mission:\n' if english else 'Mission :\n') + result
    if options.examples and options.example_input.strip() and options.example_output.strip():
        sample, answer = options.example_input.strip(), options.example_output.strip()
        example = (f'Example:\nInput: {sample}\nOutput: {answer}' if english else
                   f'Exemple :\nEntrée : {sample}\nSortie : {answer}')
        appendices = _append_once(appendices, example)
    if custom:
        heading = 'Additional instructions:' if english else 'Consignes supplémentaires :'
        block = f'{heading}\n{custom}'
        appendices = _append_once(appendices, block)
    if appendices:
        result += '\n\n' + appendices
    result = source[:left] + result + source[right:]
    after = estimate(result)
    additions = bool(options.agent or custom or
                     (options.examples and options.example_input.strip() and options.example_output.strip()))
    if options.reduce and not (additions or no_dashes) and after > before:
        return Optimization(source, source, before, before)
    return Optimization(source, result, before, after)
