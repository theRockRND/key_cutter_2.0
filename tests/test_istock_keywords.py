"""Tests for the iStock vocabulary matching (istock_keywords.py and ProcessingThread.refine_istock_keywords)"""
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import istock_keywords
from tests.test_main import FakeOpenAI, make_thread, main

TRAIN = [{'term': 'Sports Training', 'term_id': '84558'}, {'term': 'Train - Vehicle', 'term_id': '61957'}]
SLEEPERS = [{'term': 'Sleeping', 'term_id': '61858'}, {'term': 'Railroad Tie', 'term_id': '6344046'}]
TREES = [{'term': 'Tree', 'term_id': '62781'}]


class FinalizeTermsTest(unittest.TestCase):
    candidates = {'train': ['Sports Training', 'Train - Vehicle'], 'trees': ['Tree']}

    def test_only_offered_terms_in_keyword_order(self):
        terms = istock_keywords.finalize_terms(
            ['train', 'trees', 'gizmo'], {'trees': 'Tree', 'train': 'train - vehicle', 'gizmo': 'Gizmo'},
            ['Forest', 'Appalachians'], self.candidates, ['Forest', 'Tree'])
        # exact spelling from ESP, invented terms and terms not suggested are dropped, no duplicates
        self.assertEqual(terms, ['Train - Vehicle', 'Tree', 'Forest'])

    def test_null_choice_drops_word_and_unknown_words_are_kept(self):
        terms = istock_keywords.finalize_terms(['train', 'trees'], {'train': None, 'trees': 'Tree'}, [],
                                               self.candidates, [], keep_words=['newword'])
        self.assertEqual(terms, ['Tree', 'newword'])

    def test_limit(self):
        candidates = {f'w{i}': [f'Term {i}'] for i in range(60)}
        terms = istock_keywords.finalize_terms(list(candidates), {w: c[0] for w, c in candidates.items()}, [],
                                               candidates, [])
        self.assertEqual(len(terms), istock_keywords.MAX_ISTOCK_KEYWORDS)


class VocabularyTest(unittest.TestCase):
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'v.json'
            vocabulary = istock_keywords.IstockVocabulary(path)
            self.assertEqual(vocabulary.missing(['train', 'train', 'zzz']), ['train', 'zzz'])
            vocabulary.update({'train': TRAIN, 'zzz': []})
            vocabulary.save()
            loaded = istock_keywords.IstockVocabulary(path)
            self.assertEqual(loaded.candidates('train'), ['Sports Training', 'Train - Vehicle'])
            self.assertEqual(loaded.missing(['train', 'zzz', 'new']), ['new'])  # 'zzz' is known: no terms

    def test_broken_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'v.json'
            path.write_text('{broken', encoding='utf-8')
            self.assertEqual(istock_keywords.IstockVocabulary(path).words, {})


class EspRequestsTest(unittest.TestCase):
    def test_lookup_words_in_chunks(self):
        words = [f'w{i}' for i in range(istock_keywords.LOOKUP_CHUNK + 1)]

        def fake_get(urls, timeout=60):
            self.assertEqual(len(urls), 2)
            self.assertIn('disambiguation=Partial', urls[0])
            self.assertIn('asset_type=istock_creative_video', urls[0])
            return [{'keywords': {'w0': [{'term': 'Zero', 'term_id': '1'}]}}, {'keywords': {}}]

        with mock.patch.object(istock_keywords, 'esp_get_json', fake_get):
            result = istock_keywords.esp_lookup_words(words)
        self.assertEqual(result['w0'], [{'term': 'Zero', 'term_id': '1'}])
        self.assertEqual(result[words[-1]], [])

    def test_get_json_reads_result_in_parts(self):
        payload = json.dumps([json.dumps({'keywords': {}})])
        from urllib.parse import quote
        encoded = quote(payload, safe='')
        calls = []

        def fake_run(js, timeout=30):
            calls.append(js)
            if 'Promise.all' in js:
                self.assertIn('try {', js)  # injected JS never throws on the page
                self.assertIn('.catch(', js)
                return 'STARTED'
            if 'status ===' in js:
                return f'DONE {len(encoded)}'
            if 'slice(' in js:
                return encoded
            return 'OK'

        with mock.patch.object(istock_keywords, 'run_in_esp_tab', fake_run):
            self.assertEqual(istock_keywords.esp_get_json(['/api/x']), [{'keywords': {}}])

    def test_get_json_error(self):
        responses = iter(['STARTED', 'ERROR HTTP 401 /api/x'])
        with mock.patch.object(istock_keywords, 'run_in_esp_tab', lambda js, timeout=30: next(responses)):
            with self.assertRaises(istock_keywords.EspUnavailable):
                istock_keywords.esp_get_json(['/api/x'])


class RefineIstockKeywordsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vocab_path = Path(self.tmp.name) / 'vocabulary.json'
        self.thread = make_thread(output_folder=self.tmp.name, country='Serbia')
        self.thread._video_rows.append({'filename': 'a.mov', 'created': '', 'poster': '', 'metadata': {
            'title': 'Railway Track Through Pine Forest', 'description': 'The camera glides along a quiet route',
            'keywords': 'train, sleepers, trees, newword'}})
        patches = [
            mock.patch.object(main, 'IstockVocabulary', lambda: istock_keywords.IstockVocabulary(self.vocab_path)),
            mock.patch.object(main, 'esp_lookup_words',
                              mock.Mock(return_value={'train': TRAIN, 'sleepers': SLEEPERS, 'trees': TREES,
                                                         'newword': []})),  # as esp_lookup_words: [] if unknown
            mock.patch.object(main, 'esp_title_suggestions', mock.Mock(
                return_value={'Railway Track Through Pine Forest': ['Forest', 'Green Mountains - Appalachians']})),
            mock.patch.object(main, 'open_esp_tab', mock.Mock(return_value=False)),  # never the real Chrome
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def read_istock(self):
        with open(self.thread.csv_path(main.ISTOCK_VIDEO_CSV_NAME), newline='', encoding='utf-8') as f:
            return list(csv.DictReader(f))

    def test_ai_picks_meanings_and_suggestions(self):
        answer = json.dumps({'choices': {'train': 'Train - Vehicle', 'sleepers': 'Railroad Tie', 'trees': 'Tree'},
                             'suggested': ['Forest']})
        fake = FakeOpenAI([answer])
        with mock.patch.object(main, 'OpenAI', fake.client):
            self.thread.refine_istock_keywords()
        meta = self.thread._video_rows[0]['metadata']
        # 'newword' was looked up but ESP has no terms for it: dropped
        self.assertEqual(meta['istock_keywords'], 'Train - Vehicle, Railroad Tie, Tree, Forest')
        self.assertEqual(meta['keywords'], 'train, sleepers, trees, newword')  # Adobe keeps plain words
        self.assertEqual(self.read_istock()[0]['keywords'], 'Train - Vehicle, Railroad Tie, Tree, Forest')
        prompt = fake.calls[0]['messages'][0]['content']
        self.assertIn('Sports Training', prompt)
        self.assertIn('Green Mountains - Appalachians', prompt)
        self.assertEqual(fake.calls[0]['response_format'], {"type": "json_object"})
        # Looked-up words are saved, also those without terms
        saved = json.loads(self.vocab_path.read_text(encoding='utf-8'))['words']
        self.assertEqual(saved['sleepers'], SLEEPERS)
        self.assertEqual(saved['newword'], [])

    def test_known_words_are_not_looked_up_again(self):
        vocabulary = istock_keywords.IstockVocabulary(self.vocab_path)
        vocabulary.update({'train': TRAIN, 'sleepers': SLEEPERS, 'trees': TREES, 'newword': []})
        vocabulary.save()
        with mock.patch.object(main, 'OpenAI', FakeOpenAI(['{"choices": {}, "suggested": []}']).client):
            self.thread.refine_istock_keywords()
        main.esp_lookup_words.assert_not_called()

    def test_without_ai_only_single_meaning_words_are_replaced(self):
        self.thread.api_key = None
        self.thread.refine_istock_keywords()
        self.assertEqual(self.thread._video_rows[0]['metadata']['istock_keywords'], 'Tree')

    def test_without_esp_unknown_words_stay(self):
        main.esp_lookup_words.side_effect = istock_keywords.EspUnavailable("no ESP tab")
        self.thread.api_key = None
        self.thread.refine_istock_keywords()
        self.assertEqual(self.thread._video_rows[0]['metadata']['istock_keywords'],
                         'train, sleepers, trees, newword')
        log = '\n'.join(self.thread.logs)
        self.assertIn('ESP is not available', log)
        self.assertIn('4 words were not checked in ESP', log)

    def test_esp_tab_opened_when_missing(self):
        self.thread.api_key = None
        with mock.patch.object(main, 'open_esp_tab', return_value=True):
            self.thread.refine_istock_keywords()
        self.assertIn('No ESP tab was open: opened https://esp.gettyimages.com/ in Chrome', '\n'.join(self.thread.logs))
        main.esp_lookup_words.assert_called_once()

    def test_esp_sign_in_needed(self):
        self.thread.api_key = None
        with mock.patch.object(main, 'open_esp_tab', side_effect=istock_keywords.EspUnavailable("asks to sign in")):
            self.thread.refine_istock_keywords()
        main.esp_lookup_words.assert_not_called()
        self.assertIn('ESP is not available (asks to sign in). Sign in to esp.gettyimages.com', '\n'.join(self.thread.logs))


    def test_esp_retry_repeats_only_istock_step(self):
        self.thread.api_key = None
        self.thread.ask_esp_retry = True
        lookup = main.esp_lookup_words
        lookup.side_effect = [istock_keywords.EspUnavailable("asks to sign in"), lookup.return_value]
        asked = []
        self.thread.esp_unavailable.connect(lambda error: (asked.append(error), self.thread.answer_esp(True)))
        self.thread.refine_istock_keywords()
        self.assertEqual(asked, ['asks to sign in'])
        self.assertEqual(lookup.call_count, 2)
        self.assertEqual(self.thread._video_rows[0]['metadata']['istock_keywords'], 'Tree')
        log = '\n'.join(self.thread.logs)
        self.assertIn('ESP failed (asks to sign in), retrying...', log)
        self.assertNotIn('ESP is not available', log)

    def test_esp_retry_declined(self):
        self.thread.api_key = None
        self.thread.ask_esp_retry = True
        main.esp_lookup_words.side_effect = istock_keywords.EspUnavailable("asks to sign in")
        self.thread.esp_unavailable.connect(lambda error: self.thread.answer_esp(False))
        self.thread.refine_istock_keywords()
        main.esp_lookup_words.assert_called_once()
        self.assertIn('ESP is not available (asks to sign in)', '\n'.join(self.thread.logs))

    def test_no_question_when_started_without_esp(self):
        self.thread.api_key = None
        main.esp_lookup_words.side_effect = istock_keywords.EspUnavailable("x")
        asked = []
        self.thread.esp_unavailable.connect(asked.append)
        self.thread.refine_istock_keywords()
        self.assertEqual(asked, [])


class CheckEspTest(unittest.TestCase):
    def test_opens_tab_and_asks_one_word(self):
        with mock.patch.object(istock_keywords, 'open_esp_tab') as open_tab, \
                mock.patch.object(istock_keywords, 'esp_lookup_words') as lookup:
            istock_keywords.check_esp()
        open_tab.assert_called_once()
        lookup.assert_called_once_with(['tree'])

    def test_error_is_raised(self):
        with mock.patch.object(istock_keywords, 'open_esp_tab',
                               side_effect=istock_keywords.EspUnavailable("asks to sign in")):
            with self.assertRaises(istock_keywords.EspUnavailable):
                istock_keywords.check_esp()

class OpenEspTabTest(unittest.TestCase):
    def run_with(self, stdout, returncode=0):
        done = mock.Mock(stdout=stdout, stderr='', returncode=returncode)
        with mock.patch.object(istock_keywords.subprocess, 'run', return_value=done) as run:
            result = istock_keywords.open_esp_tab(wait=5)
        self.assertIn('https://esp.gettyimages.com/', run.call_args[0][0][2])
        return result

    def test_already_open(self):
        self.assertFalse(self.run_with('FOUND\n'))

    def test_opened(self):
        self.assertTrue(self.run_with('OPENED https://esp.gettyimages.com/submit\n'))

    def test_sign_in_page(self):
        with self.assertRaisesRegex(istock_keywords.EspUnavailable, 'asks to sign in'):
            self.run_with('OPENED https://auth.gettyimages.com/sign-in?x=1\n')

    def test_chrome_not_scriptable(self):
        with self.assertRaises(istock_keywords.EspUnavailable):
            self.run_with('', returncode=1)
        with mock.patch.object(istock_keywords.subprocess, 'run', side_effect=FileNotFoundError):
            with self.assertRaises(istock_keywords.EspUnavailable):
                istock_keywords.open_esp_tab()


if __name__ == '__main__':
    unittest.main()
