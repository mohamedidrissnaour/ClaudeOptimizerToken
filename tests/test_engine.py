import unittest

from token_optimizer.engine import Options, Strength, estimate, optimize


class EstimationTests(unittest.TestCase):
    def test_whitespace_costs_zero_and_non_latin_text_is_counted(self):
        self.assertEqual(estimate(' \t\n'), 0)
        self.assertGreater(estimate('修复🐛'), 0)

    def test_more_content_has_higher_estimate(self):
        self.assertGreater(estimate('Analyse ce fichier et indique les trois causes.'), estimate('Analyse ce fichier.'))

    def test_reference_encoding_normalizes_unicode_and_reads_control_markers_as_text(self):
        self.assertEqual(estimate('Ａ'), estimate('A'))
        self.assertEqual(estimate('A'), 1)
        self.assertGreater(estimate('<SOS>'), 1)


class OptimizationTests(unittest.TestCase):
    def test_colloquial_french_folds_greeting_and_event_into_a_direct_instruction(self):
        source = ('bonjour tu vas bien aujourdhui cest - la fete de lindependance -  au maroc , '
                  'je veux que tu me cree un site pour-  feter ca avec des incones et une bonne animation')
        balanced = optimize(source, Options())
        aggressive = optimize(source, Options(strength=Strength.AGGRESSIVE))
        self.assertEqual(balanced.text,
                         'Crée un site pour la fête de l’indépendance au Maroc avec des icônes et une animation soignée.')
        self.assertEqual(aggressive.text,
                         'Crée un site pour la fête de l’indépendance au Maroc avec icônes et animation soignée.')
        self.assertLess(balanced.after, balanced.before)
        self.assertLess(aggressive.after, balanced.after)

    def test_direct_requests_generalize_without_removing_specific_constraints(self):
        text = ('Salut, je voudrais que tu me rédiges trois titres sans emojis pour le rapport, '
                'au format JSON et sans dépasser 42 caractères.')
        result = optimize(text, Options(strength=Strength.AGGRESSIVE))
        self.assertEqual(result.text, 'Rédige trois titres sans emojis pour le rapport, '
                                      'au format JSON et sans dépasser 42 caractères.')

    def test_preserves_pronoun_when_it_is_the_subject_being_compared(self):
        result = optimize('Je veux que tu nous compares aux concurrents.', Options())
        self.assertEqual(result.text, 'Compare-nous aux concurrents.')

    def test_legacy_agent_wrappers_are_repaired_and_do_not_reappear(self):
        boilerplate = 'Validation :\nVérifie que le résultat respecte les contraintes et le format demandés.'
        source = ('Mission :\nMission :\nCrée un site - sans images.\n\n' + boilerplate +
                  '\n\nConsignes supplémentaires :\npas de tirets -\n\n' + boilerplate)
        opts = Options(agent=True, custom=True, instructions='pas de tirets -')
        result = optimize(source, opts)
        self.assertEqual(result.text, 'Mission :\nCrée un site sans images.')
        self.assertEqual(optimize(result.text, opts).text, result.text)

    def test_real_validation_and_custom_content_survive_repeated_optimization(self):
        source = 'Mission :\nCrée un rapport.\n\nValidation :\nVérifie `sortie.csv` avant de répondre.'
        opts = Options(agent=True, custom=True, instructions='Réponds en YAML.')
        first = optimize(source, opts)
        self.assertIn('Validation :\nVérifie `sortie.csv` avant de répondre.', first.text)
        self.assertTrue(first.text.endswith('Consignes supplémentaires :\nRéponds en YAML.'))
        self.assertEqual(optimize(first.text, opts).text, first.text)

    def test_multiline_custom_instruction_keeps_its_own_headings_once(self):
        opts = Options(custom=True, instructions='Réponds en YAML.\nExemple :\nconclusion: OK')
        first = optimize('Crée un rapport.', opts)
        self.assertEqual(optimize(first.text, opts).text, first.text)

    def test_no_dash_instruction_removes_separators_not_dates_literals_or_negative_numbers(self):
        text = 'Fais une page - sans images. Garde `foo-bar`, 2026-11-18 et -15.'
        result = optimize(text, Options(reduce=False, clarify=False, custom=True, instructions='pas de tirets -'))
        self.assertEqual(result.text, 'Fais une page sans images. Garde `foo-bar`, 2026-11-18 et -15.')

    def test_keeps_subtraction_and_negative_value_with_spaces(self):
        text = 'Calcule x - y.\n- 15 est un exemple.'
        result = optimize(text, Options(strength=Strength.MAXIMUM, custom=True, instructions='pas de tirets'))
        self.assertEqual(result.text, text)

    def test_validation_with_real_steps_is_not_removed_with_boilerplate(self):
        text = ('Mission :\nCrée un rapport.\n\nValidation :\n'
                'Vérifie que le résultat respecte les contraintes et le format demandés.\n'
                'Compare les 3 lignes de `sortie.csv`.')
        self.assertEqual(optimize(text, Options(agent=True)).text, text)

    def test_removes_polite_redundancy_without_losing_negation_or_limits(self):
        source = 'S’il te plaît, explique de manière concise pourquoi il ne faut pas dépasser 120 caractères.'
        result = optimize(source, Options())
        self.assertEqual(result.text, 'Explique brièvement pourquoi il ne faut pas dépasser 120 caractères.')
        self.assertLess(result.after, result.before)

    def test_four_strengths_apply_progressively(self):
        source = 'Please explain in order to show a total of 4 results in the event that the test fails.'
        results = [optimize(source, Options(strength=level, clarify=False)) for level in Strength]
        self.assertEqual([r.text for r in results], [
            'Explain in order to show a total of 4 results in the event that the test fails.',
            'Explain to show a total of 4 results in the event that the test fails.',
            'Explain to show 4 results in the event that the test fails.',
            'Explain to show 4 results if the test fails.',
        ])

    def test_preserves_code_quotes_paths_and_selection_edges(self):
        text = ('  Please describe `C:\\Temp\\please.txt` and "in order to".\n'
                '```python\nprint("Please do not change 125")\n```  ')
        self.assertEqual(optimize(text, Options(strength=Strength.MAXIMUM)).text,
                         text.replace('Please describe', 'Describe', 1))

    def test_structures_explicit_sections_without_guessing_content(self):
        source = 'Contexte: tests manuels\nObjectif: 5 cas\nContraintes: sans API'
        result = optimize(source, Options(reduce=False, clarify=True))
        self.assertEqual(result.text, 'Contexte: tests manuels\n\nObjectif: 5 cas\n\nContraintes: sans API')

    def test_examples_are_used_only_if_provided_and_enabled(self):
        base = 'Traduis en français.'
        examples = Options(examples=True, example_input='Hello', example_output='Bonjour')
        self.assertEqual(optimize(base, examples).text, 'Traduis en français.\n\nExemple :\nEntrée : Hello\nSortie : Bonjour')
        self.assertEqual(optimize(base, Options(examples=True, example_input='Hello')).text, base)

    def test_agent_and_custom_instruction_are_independent(self):
        base = 'Décris le test.'
        result = optimize(base, Options(agent=True, custom=True, instructions='Réponds en YAML.'))
        self.assertIn('Mission :\nDécris le test.', result.text)
        self.assertIn('\nConsignes supplémentaires :\nRéponds en YAML.', result.text)
        self.assertGreater(result.after, result.before)

    def test_no_changes_when_options_off_or_text_is_concise(self):
        self.assertFalse(optimize('Génère trois tests.', Options()).changed)
        off = Options(reduce=False, clarify=False, instructions='Ne jamais ajouter ceci')
        self.assertEqual(optimize('Please describe the test.', off).text, 'Please describe the test.')

    def test_does_not_rewrite_raw_structured_data(self):
        data = '{\n  "note": "Please optimize in order to report",\n  "limit": 120\n}'
        self.assertEqual(optimize(data, Options(strength=Strength.MAXIMUM)).text, data)
