"""Offline BPE estimate using Anthropic's public historical vocabulary."""

from base64 import b64decode
from functools import cache
from hashlib import sha256
import json
from pathlib import Path
from unicodedata import normalize


# This pins the public data, not the tokenizer used by current Claude models.
_VOCAB_SHA256 = '58dad83d85e9cd57be209172449ebfbc395df2b455c11fe8b7e5b661e6f462ad'


class CounterUnavailable(RuntimeError):
    """The local vocabulary or its runtime is unavailable."""


@cache
def _encoding():
    try:
        from tiktoken import Encoding

        data = Path(__file__).with_name('assets').joinpath('claude-historical.json').read_bytes()
        if sha256(data).hexdigest() != _VOCAB_SHA256:
            raise ValueError('Unexpected vocabulary file')
        vocab = json.loads(data)
        ranks: dict[bytes, int] = {}
        for line in vocab['bpe_ranks'].splitlines():
            if not line.strip():
                continue
            marker, offset, *tokens = line.split()
            if marker != '!':
                raise ValueError('Unexpected BPE record')
            for rank, token in enumerate(tokens, int(offset)):
                ranks[b64decode(token, validate=True)] = rank
        special = vocab['special_tokens']
        # The legacy JS loader uses the records themselves; its declared size
        # is stale (64739), while the rank table plus specials contains 65000.
        return Encoding(
            'claude-historical-offline', pat_str=vocab['pat_str'], mergeable_ranks=ranks,
            special_tokens=special, explicit_n_vocab=len(ranks) + len(special),
        )
    except (ImportError, OSError, ValueError, AssertionError, KeyError, TypeError) as exc:
        raise CounterUnavailable('Le tokenizer local est indisponible. Vérifie les fichiers de l’utilitaire.') from exc


def estimate(text: str) -> int:
    """Estimate draft text only; do not invent message, history or quota overhead."""
    if not text.strip():
        return 0
    return len(_encoding().encode_ordinary(normalize('NFKC', text)))
