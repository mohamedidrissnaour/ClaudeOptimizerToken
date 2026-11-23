import unittest

from token_optimizer.engine import Options, Strength
from token_optimizer.local_model import eligible, usable_candidate
from token_optimizer.workflow import Capture, Mode, prepare


class LocalProposalTests(unittest.TestCase):
    def test_requires_opt_in_and_skips_literal_payloads(self):
        def job(text):
            capture = Capture(42, 10, (1, 2), text, 0, len(text))
            return prepare(Mode.OPTIMIZE, capture, Options())

        prompt = 'Hello, please write a brief report with a clear conclusion.'
        self.assertFalse(eligible(job(prompt), Options()))
        self.assertTrue(eligible(job(prompt), Options(local_ai=True)))
        self.assertFalse(eligible(job('Please explain the meaning of `status.json`.'), Options(local_ai=True)))
        self.assertFalse(eligible(job('    Print the result in JSON format.'), Options(local_ai=True)))
        self.assertFalse(eligible(job(prompt), Options(strength=Strength.MAXIMUM, local_ai=True, custom=True)))

    def test_rejects_changed_numbers_negations_and_acronyms(self):
        original = 'Please write 3 short tests in YAML without changing any of the existing files.'
        self.assertTrue(usable_candidate(original, 'Write 3 tests in YAML without changing files.'))
        self.assertFalse(usable_candidate(original, 'Write 2 tests in YAML without changing files.'))
        self.assertFalse(usable_candidate(original, 'Write 3 tests in YAML; change files.'))
        self.assertFalse(usable_candidate(original, 'Write 3 tests in JSON without changing files.'))
