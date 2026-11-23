"""Conservative FR/EN rules for turning conversational requests into direct prompts."""

import re
from unicodedata import normalize


PROTECTED = re.compile(
    r'(?ms:^[ \t]*(?:\x60{3,}|~{3,})[^\n]*\n.*?(?:^[ \t]*(?:\x60{3,}|~{3,})[ \t]*(?=\n|$)|\Z))'
    r'|(?m:^(?:    |\t|>)[^\n]*)'
    r'|\x60+[^\x60\n]+\x60+|«[^»]*»|“[^”]*”|"(?:\\.|[^"\\])*"'
    r"|(?<![\w’'])'(?:\\.|[^'\\\n]|(?<=\w)'(?=\w))*'(?!\w)"
    r'|\b(?:https?://|www\.)[^\s<>\x60]+|\b[A-Z]:[\\/][^\s<>\x60"]+'
    r'|(?<!\w)(?:\.\.?[\\/]|/)[\w.%-][\w./\\%-]*'
    r'|\b(?:[\w.-]+[\\/])+[\w.-]+|\b[\w.+-]+@[\w.-]+\.[A-Z]{2,}'
    r'|\b[\w.-]+\.(?:md|txt|json|ya?ml|csv|html|css|jsx?|tsx?|py|cs|png|svg|pdf|exe)\b|<[^<>\n]+>',
    re.IGNORECASE,
)

_SECTION = re.compile(
    r'(?im)(?<=\S\n)(?=(?:contexte|objectif|contraintes|format(?: attendu)?|livrables?|'
    r'context|goal|constraints|output(?: format)?|requirements)\s*:)'
)
_ENGLISH = re.compile(r'\b(?:please|write|create|provide|describe|summarize|explain|the|you|and|with)\b', re.I)
_FRENCH = re.compile(r'\b(?:explique|résume|écris|traduis|décris|crée|le|les|des|une|avec|sur|pour|dans|tu|je|que)\b', re.I)
_GREET = re.compile(
    r'^(?:bonjour|salut|hello|hey|hi)[ \t,.!?]+'
    r'(?:(?:tu vas bien|comment vas[- ]tu|comment ça va|how are you)(?!\w)[ \t,.!?]+)?',
    re.IGNORECASE,
)
_CONTEXT = re.compile(r"^(?:aujourd[’']hui[ \t,]+)?c[’']est\b", re.I)
_FR_FORMS = {
    'crée': 'cree crees creer creez',
    'rédige': 'redige rediges rediger redigez',
    'écris': 'ecris ecrive ecrives ecrire ecrivez',
    'explique': 'explique expliques expliquer expliquez',
    'décris': 'decris decrive decrives decrire decrivez',
    'propose': 'propose proposes proposer proposez',
    'génère': 'genere generes generer generez',
    'fais': 'fais fasse fasses faire faites',
    'réalise': 'realise realises realiser realisez',
    'conçois': 'concois concoive concoives concevoir concevez',
    'développe': 'developpe developpes developper developpez',
    'prépare': 'prepare prepares preparer preparez',
    'résume': 'resume resumes resumer resumez',
    'corrige': 'corrige corriges corriger corrigez',
    'traduis': 'traduis traduise traduises traduire traduisez',
    'compare': 'compare compares comparer comparez',
    'analyse': 'analyse analyses analyser analysez',
    'donne': 'donne donnes donner donnez',
    'ajoute': 'ajoute ajoutes ajouter ajoutez',
    'liste': 'liste listes lister listez',
    'vérifie': 'verifie verifies verifier verifiez',
    'montre': 'montre montres montrer montrez',
    'identifie': 'identifie identifies identifier identifiez',
    'affiche': 'affiche affiches afficher affichez',
}
_FR_VERBS = {word: verb for verb, forms in _FR_FORMS.items() for word in forms.split()}
_EN_VERBS = set('explain write describe show give list check create summarize translate review compare generate provide '
                'analyze analyse fix find test use design prepare build make draft report'.split())
_FR_REQUEST = (r"(?:je (?:veux|voudrais|souhaite) que (?:tu|vous)|"
               r"j[’']aimerais que (?:tu|vous)|(?:peux[- ]tu|pourrais[- ]tu|pouvez[- ]vous|pourriez[- ]vous))"
               r"[ \t]+")
_EN_REQUEST = (r"(?:i (?:would like|want|need) you to|(?:can|could|would) you(?: please)?)"
               r"[ \t]+")
_DIRECT = re.compile(
    r'(?P<sep>^|[,.;?!\n])[ \t]*(?:(?P<fr>' + _FR_REQUEST + r")(?P<pronoun>(?:me|nous)[ \t]+|m[’'])?"
    r'|(?P<en>' + _EN_REQUEST + r'))'
    r'(?P<verb>[^\W\d_]+)\b', re.I,
)
_CREATION = {'crée', 'rédige', 'écris', 'fais', 'réalise', 'conçois', 'prépare', 'développe', 'génère'}
_ARTIFACT = re.compile(r'[ \t]+(?:(?:un|une|des|le|la|les|deux|trois|quatre|cinq|six)\b|\d+\b)', re.I)
_POLITE = re.compile(
    r'^(?P<indent>[ \t]*)(?:(?P<fr>s[’\']il (?:te|vous) pla[iî]t)|(?P<en>please))'
    r'[, \t]+(?P<verb>[^\W\d_]+)\b', re.I | re.M,
)
_FOLDED_EVENT = re.compile(
    r"(?i)^(?:aujourd[’']hui[ \t,]+)?c[’']est[ \t]+"
    r"(?P<topic>(?:(?:le|la|les|un|une|ce|cet|cette)[ \t]+|l[’'])[^,;.!?\n]{3,120}?)"
    r"[,;.!][ \t]+(?P<action>crée|conçois|fais|réalise|développe|prépare)[ \t]+"
    r"(?P<object>(?:un|une)[ \t]+[^,;.!?\n]{2,80}?)[ \t]+pour[ \t]+"
    r"(?:fêter|célébrer|marquer)[ \t]+(?:ça|cela|cette occasion|cet événement)\b"
    r"(?P<rest>[^\n]*)$",
)
_SPELLINGS = {
    'aujourdhui': 'aujourd’hui', 'cest': 'c’est', 'lindependance': 'l’indépendance',
    'independance': 'indépendance', 'incones': 'icônes', 'icones': 'icônes',
    'incone': 'icône', 'icone': 'icône', 'fete': 'fête', 'feter': 'fêter', 'maroc': 'Maroc',
}
_SPELL = re.compile(r'\b(' + '|'.join(map(re.escape, _SPELLINGS)) + r')\b', re.I)
_RULES = (
    (),
    (
        ('in order to', 'to'), ('for the purpose of', 'for'), ('a number of different', 'several distinct'),
        ('de manière concise', 'brièvement'), ('de façon concise', 'brièvement'),
        ('dans le but de', 'pour'), ('il est nécessaire de', 'il faut'),
        ('une bonne animation', 'une animation soignée'),
    ),
    (
        ('a total of', ''), ('at this point in time', 'now'), ('with reference to', 'about'),
        ('en ce qui concerne', 'quant à'), ('à l’heure actuelle', 'actuellement'),
        ("à l'heure actuelle", 'actuellement'), ('au préalable', 'd’abord'),
    ),
    (
        ('in the event that', 'if'), ('due to the fact that', 'because'), ('make use of', 'use'),
        ('dans le cas où', 'si'), ('pour la simple raison que', 'car'),
        ('dans la mesure du possible', 'si possible'),
    ),
)
_SUBSTITUTIONS = tuple(tuple(
    (re.compile(r'(?<!\w)' + re.escape(long) + (r'[ \t]+(?=\S)' if not short else r'(?!\w)'), re.I), short)
    for long, short in level
) for level in _RULES)

# Preserve numbers, minus signs, hyphenated words and literals. Only remove
# ornamental separators between words and clearly-marked prose list bullets.
_DASH = re.compile(r'(?i)(?<=[^\W\d_])[ \t]+[-‐‑–—]+[ \t]+(?=(?:un|une|le|la|les|au|aux|du|des|en|'
                   r'pour|avec|sans|dans|sur|et|then|with|without|for)\b)')
_ATTACHED_DASH = re.compile(r'(?i)\b(pour|avec|et|ou|sans|puis|before|after|with|to)[-–—]+[ \t]+(?=[^\W\d_])')
_BULLET_DASH = re.compile(r'(?m)^([ \t]{0,3})[-–—]+[ \t]+(?=[^\W\d_])')


def english_text(text: str) -> bool:
    return len(_ENGLISH.findall(text)) > len(_FRENCH.findall(text))


def _fold(word: str) -> str:
    return ''.join(c for c in normalize('NFD', word.casefold()) if not '\u0300' <= c <= '\u036f')


def _upper_first(text: str) -> str:
    return text[:1].upper() + text[1:]


def _start_is_request(text: str) -> bool:
    match = re.match(r'[^\W\d_]+', text)
    return bool(_CONTEXT.match(text) or _DIRECT.match(text) or
                (match and (_fold(match[0]) in _FR_VERBS or match[0].casefold() in _EN_VERBS)))


def _remove_courtesy(piece: str) -> str:
    greeting = _GREET.match(piece)
    if greeting and _start_is_request(piece[greeting.end():]):
        piece = piece[greeting.end():]
        if not _CONTEXT.match(piece):
            piece = _upper_first(piece)

    def polite(match: re.Match) -> str:
        verb = match['verb']
        supported = _fold(verb) in _FR_VERBS if match['fr'] else verb.casefold() in _EN_VERBS
        return match['indent'] + _upper_first(verb) if supported else match[0]

    return _POLITE.sub(polite, piece)


def _direct_request(match: re.Match) -> str:
    verb = match['verb']
    if match['fr']:
        action = _FR_VERBS.get(_fold(verb))
    else:
        action = verb.casefold() if verb.casefold() in _EN_VERBS else None
    if action is None:
        return match[0]
    pronoun = (match['pronoun'] or '').strip().casefold()
    if pronoun:
        moi = pronoun != 'nous'
        # « Crée-moi un site » can be shortened to « Crée un site ». But in
        # « Compare-nous » or « Écris-moi demain » the pronoun carries meaning.
        redundant = (moi and action in _CREATION and _ARTIFACT.match(match.string, match.end()))
        if not redundant:
            action += '-moi' if moi else '-nous'
    separator = match['sep']
    indentation = match[0][len(separator):]
    padding = indentation[:len(indentation) - len(indentation.lstrip(' \t'))]
    if separator not in (',', ';'):
        action = _upper_first(action)
    return separator + (padding or (' ' if separator in (',', ';', '.', '?', '!') else '')) + action


def _spelling(match: re.Match) -> str:
    value = _SPELLINGS[match[0].casefold()]
    if match[0].isupper():
        return value.upper()
    return _upper_first(value) if match[0][0].isupper() else value


def _event(match: re.Match) -> str:
    result = f"{_upper_first(match['action'])} {match['object']} pour {match['topic'].strip()}{match['rest']}".rstrip()
    return result if not result or result[-1] in '.!?' else result + '.'


def _prose(piece: str, strength: int, reduce: bool, clarify: bool, no_dashes: bool,
           line_start: bool) -> str:
    # A chunk following a quote or a code span is not a new sentence.
    prefix = '' if line_start else '\x01'
    if prefix:
        piece = prefix + piece
    if clarify or (reduce and strength >= 1):
        piece = re.sub(r'(?<=\S)[ \t]{2,}(?=\S)', ' ', piece)
        piece = re.sub(r'[ \t]+,', ',', piece)
        if not english_text(piece):
            piece = _SPELL.sub(_spelling, piece)
            piece = re.sub(r'\b(fêter|célébrer|marquer) ca\b', r'\1 ça', piece)
    if no_dashes or (reduce and strength >= 1):
        piece = _ATTACHED_DASH.sub(r'\1 ', _DASH.sub(' ', piece))
    if no_dashes:
        piece = _BULLET_DASH.sub(r'\1', piece)
    if reduce:
        piece = _remove_courtesy(piece)
        if strength >= 1:
            piece = _DIRECT.sub(_direct_request, piece)
        for group in _SUBSTITUTIONS[:strength + 1]:
            for pattern, short in group:
                piece = pattern.sub(lambda m: _upper_first(short) if m[0][0].isupper() else short, piece)
        if strength >= 1:
            piece = _FOLDED_EVENT.sub(_event, piece)
        if strength >= 2:
            piece = re.sub(r'\bavec des (?=icônes\b|illustrations\b|graphiques\b)', 'avec ', piece, flags=re.I)
            if re.search(r'\bavec (?:icônes|illustrations|graphiques)\b', piece, re.I):
                piece = re.sub(r'\bet une (?=animation soignée\b)', 'et ', piece, flags=re.I)
    if clarify:
        piece = _SECTION.sub('\n', piece)
    return piece[len(prefix):]


def rewrite(text: str, *, strength: int, reduce: bool, clarify: bool, no_dashes: bool = False) -> str:
    if not (reduce or clarify or no_dashes) or text.startswith(('{', '[', '<?xml')) or '\ufffc' in text:
        return text
    chunks: list[str] = []
    cursor = 0
    for match in PROTECTED.finditer(text):
        chunks.append(_prose(text[cursor:match.start()], strength, reduce, clarify, no_dashes,
                             not cursor or text[cursor - 1] == '\n'))
        chunks.append(match[0])
        cursor = match.end()
    chunks.append(_prose(text[cursor:], strength, reduce, clarify, no_dashes,
                         not cursor or text[cursor - 1] == '\n'))
    return ''.join(chunks)
