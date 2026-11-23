from dataclasses import replace
import unittest

from token_optimizer.engine import Options, estimate
from token_optimizer.workflow import Capture, EditorError, Mode, can_replace, locate_selection, prepare


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.capture = Capture(101, 202, (1, 2, 3), 'NOTE: Please describe it. END', 6, 25)

    def test_count_uses_the_entire_draft_and_never_produces_a_replacement(self):
        result = prepare(Mode.COUNT, self.capture, Options())
        self.assertEqual(result.count, estimate('NOTE: Please describe it. END'))
        self.assertIsNone(result.replacement)
        self.assertFalse(can_replace(result, self.capture))

    def test_replace_only_that_occurrence_of_identical_selected_text(self):
        snap = Capture(101, 202, (1, 2, 3), 'Please write. Please write.', 14, 27)
        result = prepare(Mode.OPTIMIZE, snap, Options())
        self.assertEqual(result.replacement, 'Write.')
        self.assertEqual(result.expected_draft, 'Please write. Write.')
        self.assertTrue(can_replace(result, snap))
        self.assertFalse(can_replace(result, replace(snap, start=0, end=13)))

    def test_each_change_to_the_editor_snapshot_blocks_paste(self):
        result = prepare(Mode.OPTIMIZE, self.capture, Options())
        for modified in (None, replace(self.capture, window=999), replace(self.capture, pid=222),
                         replace(self.capture, runtime=(1, 2, 4)), replace(self.capture, text=self.capture.text+'!'),
                         replace(self.capture, start=self.capture.start + 1)):
            with self.subTest(modified=modified):
                self.assertFalse(can_replace(result, modified))

    def test_empty_selection_does_not_become_a_whole_draft_replacement(self):
        with self.assertRaises(EditorError):
            prepare(Mode.OPTIMIZE, replace(self.capture, start=6, end=6), Options())

    def test_unchanged_text_has_no_paste_transaction(self):
        snap = replace(self.capture, text='Fais le test.', start=0, end=13)
        result = prepare(Mode.OPTIMIZE, snap, Options())
        self.assertIsNone(result.replacement)
        self.assertFalse(can_replace(result, snap))

    def test_offsets_follow_the_provider_prefix_not_a_text_search(self):
        document = '🧪 A\r\nexemple exemple\r\n'
        self.assertEqual(locate_selection(document, '🧪 A\r\nexemple ', 'exemple'), (12, 19))
        self.assertEqual(locate_selection(document, 'incorrect', 'exemple'), (0, 0))
