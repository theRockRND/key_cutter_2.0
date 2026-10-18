"""Tests for metadata generation, OpenAI request handling and batch processing (main.py)"""
import csv
import json
import os
import sys
import shutil
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import main  # noqa: E402
import istock_keywords  # noqa: E402

VOCAB_TMP = tempfile.TemporaryDirectory()
_module_patches = []
FIND_GLUED_WORDS = main.find_glued_words  # real spell check, tested in GluedWordsTest


def setUpModule():
    """Tests never reach the ESP tab in Chrome or the real istock_vocabulary.json,
    the macOS spell checker is used only by GluedWordsTest"""
    unavailable = mock.Mock(side_effect=istock_keywords.EspUnavailable("tests"))
    _module_patches.extend([
        mock.patch.object(istock_keywords, 'run_in_esp_tab', unavailable),
        mock.patch.object(main, 'esp_lookup_words', unavailable),
        mock.patch.object(main, 'esp_title_suggestions', unavailable),
        mock.patch.object(main, 'open_esp_tab', mock.Mock(return_value=False)),
        mock.patch.object(main, 'find_glued_words', mock.Mock(return_value={})),
        mock.patch.object(main, 'IstockVocabulary',
                          lambda: istock_keywords.IstockVocabulary(Path(VOCAB_TMP.name) / 'vocabulary.json')),
    ])
    for patch in _module_patches:
        patch.start()


def tearDownModule():
    for patch in _module_patches:
        patch.stop()

FFMPEG_AVAILABLE = shutil.which('ffmpeg') is not None and shutil.which('ffprobe') is not None

KEYWORDS = ("mountain river forest valley stream creek rapids rocks stones moss wilderness nature landscape "
            "scenic outdoors hiking trail travel adventure ecology environment freshwater current flowing "
            "motion pine evergreen woodland canopy fresh pristine tranquil serene calm peaceful daylight "
            "overcast season wild natural remote alpine highland terrain geology erosion splash foam cascade "
            "torrent ravine gorge spruce fir conifer boulders spring summer").split()

AI_RESPONSE = {
    "title": "Mountain River Flowing Through Green Forest Valley",
    "description": "Clear water rushes past mossy boulders under a cloudy summer sky",
    "keywords": ", ".join(KEYWORDS),
}

HD = {'width': 1920, 'height': 1080, 'duration': 12.0}
VERTICAL = {'width': 1080, 'height': 1920, 'duration': 12.0}


def words(text):
    return set(text.lower().replace(',', ' ').split())


LOGS_TMP = tempfile.TemporaryDirectory()


def make_thread(api_key="sk-test", input_folder=".", output_folder=".", country='', stocks=tuple(main.STOCKS)):
    thread = main.ProcessingThread(input_folder, output_folder, api_key=api_key, country=country, stocks=stocks)
    thread.logs_folder = Path(LOGS_TMP.name)
    thread.logs = []
    thread.log_message.connect(thread.logs.append)
    return thread


def stock_csv(csv_name):
    """Path of a CSV inside the output folder: its stock site folder and name"""
    return f"{main.STOCKS[main.CSV_STOCKS[csv_name]]}/{csv_name}"


def output_files(folder):
    return sorted(str(p.relative_to(folder)) for p in Path(folder).rglob('*') if p.is_file())


def generate(thread, response, video_info=None, rewrite=None):
    """Runs metadata generation with a fixed AI response and an optional fake description rewrite"""
    thread.analyze_image_with_ai = lambda path, info=None: dict(response)
    thread._rewrite_description = mock.Mock(return_value=rewrite)
    return thread.analyze_and_generate_metadata(Path("clip.mov"), video_info)


class FakeOpenAI:
    """Replaces openai.OpenAI: returns queued responses (str) or raises queued exceptions"""

    def __init__(self, queue):
        self.queue = list(queue)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        message = types.SimpleNamespace(content=item)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    def client(self, api_key=None):
        return types.SimpleNamespace(chat=types.SimpleNamespace(completions=self))


class MetadataRulesTest(unittest.TestCase):
    def setUp(self):
        self.thread = make_thread()

    def check_common_rules(self, metadata):
        title_words = words(metadata['title']) - main.TITLE_CONNECTORS
        desc_words = words(metadata['description'])
        keywords = metadata['keywords'].split(', ')
        self.assertLessEqual(len(keywords), main.MAX_KEYWORDS)
        self.assertEqual(len(keywords), len(set(keywords)), "duplicate keywords")
        self.assertFalse(title_words & desc_words, "title and description share words")
        self.assertFalse(desc_words & set(keywords), "description and keywords share words")
        self.assertTrue(15 <= len(metadata['description']) <= 80)
        self.assertTrue(6 <= len(metadata['title'].split()) <= 10)
        for kw in keywords:
            self.assertRegex(kw, r'^[a-z0-9]{3,}$')
            self.assertNotIn(kw + 's', keywords, f"plural duplicate of {kw}")

    def test_photo_metadata(self):
        metadata = generate(self.thread, AI_RESPONSE)
        self.check_common_rules(metadata)
        self.assertEqual(metadata['title'], "Mountain River Flowing Through Green Forest Valley")
        self.assertEqual(metadata['description'], AI_RESPONSE['description'])
        self.assertEqual(metadata['category'], 'Photography')
        self.assertEqual(len(metadata['keywords'].split(', ')), 49)
        self.assertFalse(set(metadata['keywords'].split(', ')) & words(metadata['title']))

    def test_video_category(self):
        self.assertEqual(generate(self.thread, AI_RESPONSE, HD)['category'], 'Video')

    def test_shutterstock_categories_from_ai(self):
        response = dict(AI_RESPONSE, categories='nature, Transportation')
        self.assertEqual(generate(self.thread, response, HD)['shutterstock_categories'], ['Nature', 'Transportation'])
        self.assertIn('SHUTTERSTOCK CATEGORIES: Nature, Transportation', '\n'.join(self.thread.logs))

    def test_shutterstock_categories_from_keywords_without_ai_pick(self):
        # AI_RESPONSE has no categories; 'nature' is among its keywords
        self.assertEqual(generate(self.thread, AI_RESPONSE, HD)['shutterstock_categories'], ['Nature'])

    def test_vertical_video_words(self):
        metadata = generate(self.thread, AI_RESPONSE, VERTICAL)
        self.check_common_rules(metadata)
        self.assertTrue(metadata['title'].endswith("Vertical Video"))
        self.assertEqual(metadata['keywords'].split(', ')[:2], ['vertical', 'video'])

    def test_vertical_title_stays_within_10_words(self):
        response = dict(AI_RESPONSE, title="One Two Three Four Five Six Seven Eight Nine Ten Eleven")
        metadata = generate(self.thread, response, VERTICAL)
        self.assertEqual(len(metadata['title'].split()), 10)
        self.assertTrue(metadata['title'].endswith("Vertical Video"))

    def test_linking_words_kept_in_title(self):
        response = dict(AI_RESPONSE, title="Snow Covered Park Path With Trees And Benches In Winter")
        metadata = generate(self.thread, response)
        self.assertEqual(metadata['title'], "Snow Covered Park Path With Trees and Benches in Winter")
        self.check_common_rules(metadata)

    def test_linking_words_allowed_in_description(self):
        response = dict(AI_RESPONSE, description="Clear water rushes through mossy boulders under a cloudy sky")
        metadata = generate(self.thread, response, HD)
        self.thread._rewrite_description.assert_not_called()
        self.assertEqual(metadata['description'], response['description'])
        self.assertNotIn('through', metadata['keywords'].split(', '))

    def test_no_generated_keyword_variations(self):
        response = dict(AI_RESPONSE, keywords="swings, swing, posts, post, cold, blue, river, winter")
        metadata = generate(self.thread, response)
        keywords = metadata['keywords'].split(', ')
        self.assertIn('swings', keywords)
        self.assertNotIn('swing', keywords)
        self.assertNotIn('post', keywords)
        for invented in ['colds', 'blues', 'wint', 'winters']:
            self.assertNotIn(invented, keywords)

    def test_phrases_in_keywords_are_skipped(self):
        response = dict(AI_RESPONSE, keywords="snowy park, bench, green grass, " + AI_RESPONSE['keywords'])
        keywords = generate(self.thread, response)['keywords'].split(', ')
        self.assertIn('bench', keywords)
        self.assertNotIn('snowy', keywords)

    def test_glued_keywords_are_split(self):
        response = dict(AI_RESPONSE, keywords="stationhouse, railwayline, " + AI_RESPONSE['keywords'])
        glued = {'stationhouse': ('station', 'house'), 'railwayline': ('railway', 'forest')}
        with mock.patch.object(main, 'find_glued_words', mock.Mock(return_value=glued)):
            keywords = generate(self.thread, response)['keywords'].split(', ')
        self.assertEqual(keywords[:3], ['station', 'house', 'railway'])  # 'forest' is a title word
        self.assertNotIn('stationhouse', keywords)
        self.assertNotIn('railwayline', keywords)
        self.assertIn("Glued keyword 'stationhouse' split into: station, house", '\n'.join(self.thread.logs))

    def test_compound_phrase_joined(self):
        response = dict(AI_RESPONSE, keywords="time lapse, night-scape, " + AI_RESPONSE['keywords'])
        keywords = generate(self.thread, response)['keywords'].split(', ')
        self.assertEqual(keywords[:2], ['timelapse', 'nightscape'])
        for part in ('time', 'lapse', 'night', 'scape'):
            self.assertNotIn(part, keywords)

    def test_short_title_padded_from_keywords_not_description(self):
        response = {"title": "Snowman With Stick Arms In Winter Park",
                    "description": "A carrot nosed figure stands amid deep drifts as the camera pulls back",
                    "keywords": "snowman, snow, sticks, twig, carrot, drifts, " + AI_RESPONSE['keywords']}
        metadata = generate(self.thread, response, HD)
        self.assertEqual(metadata['title'], "Snowman With Stick Arms in Winter Park")
        self.assertIn('carrot', metadata['description'])

        response = dict(response, title="Snowman Stick Arms")
        metadata = generate(self.thread, response, HD)
        # not "Sticks" (plural), not "Snow" (same root as Snowman), not "Drifts" (description word)
        self.assertEqual(metadata["title"], "Snowman Stick Arms Twig Mountain River")
        self.assertIn('drifts', metadata['description'])  # padding does not take description words

    def test_description_rewrite_when_title_words_used(self):
        response = dict(AI_RESPONSE, description="Clear river water rushes past mossy boulders in the forest")
        rewritten = "Clear water rushes past mossy boulders under a cloudy summer sky"
        metadata = generate(self.thread, response, HD, rewrite=rewritten)
        self.thread._rewrite_description.assert_called_once()
        forbidden = self.thread._rewrite_description.call_args[0][1]
        self.assertIn('river', forbidden)
        self.assertIn('scene', forbidden)  # all generic words are forbidden, not only the found ones
        self.assertEqual(metadata['description'], rewritten)

    def test_description_rewrite_when_too_long(self):
        response = dict(AI_RESPONSE, description="Clear water rushes past mossy boulders under a cloudy summer "
                                                 "sky on a bright morning")
        rewritten = "Clear water rushes past mossy boulders on a bright morning"
        metadata = generate(self.thread, response, HD, rewrite=rewritten)
        self.thread._rewrite_description.assert_called_once()
        self.assertEqual(metadata['description'], rewritten)

    def test_no_rewrite_for_clean_description(self):
        generate(self.thread, AI_RESPONSE, HD)
        self.thread._rewrite_description.assert_not_called()

    def test_title_words_removed_when_rewrite_fails(self):
        response = dict(AI_RESPONSE, description="Clear river water rushes past mossy boulders under a cloudy sky")
        metadata = generate(self.thread, response, HD, rewrite=None)
        self.check_common_rules(metadata)
        self.assertNotIn('river', words(metadata['description']))


class BuildTitleTest(unittest.TestCase):
    def setUp(self):
        self.thread = make_thread()

    def title(self, raw, padding=(), required=()):
        return self.thread.build_title(raw, list(padding), list(required))[0]

    def test_titles_from_real_run(self):
        self.assertEqual(self.title("Railway Tracks And White Car Through A Pine Forest"),
                         "Railway Tracks and White Car Through Pine Forest")
        self.assertEqual(self.title("Young Man Climbs Rocky Mound In Sunny Woodland"),
                         "Young Man Climbs Rocky Mound in Sunny Woodland")
        self.assertEqual(self.title("Historic Railway Yard With Signal Tower And Walking Man"),
                         "Historic Railway Yard With Signal Tower and Walking Man")

    def test_connectors_are_not_title_words(self):
        title, content = self.thread.build_title("Railway Tracks Lead Through Dark Tunnel Toward Daylight", [])
        self.assertEqual(title, "Railway Tracks Lead Through Dark Tunnel Toward Daylight")
        self.assertNotIn('through', content)
        self.assertNotIn('toward', content)

    def test_no_connector_at_edges_or_doubled(self):
        self.assertEqual(self.title("In The Forest Of And Pines Along Rocky Cliffs With", ['moss']),
                         "Forest of Pines Along Rocky Cliffs")

    def test_limit_10_words_without_trailing_connector(self):
        title = self.title("One Two Three Four Five Six Seven Eight Nine And Ten Eleven")
        self.assertEqual(title, "One Two Three Four Five Six Seven Eight Nine")
        title = self.title("One Two Three Four Five Six Seven And Eight Nine", required=['vertical', 'video'])
        self.assertEqual(title, "One Two Three Four Five Six Seven Vertical Video")


class TitleQualityTest(unittest.TestCase):
    def setUp(self):
        self.thread = make_thread()

    def test_same_root(self):
        for a, b in (('rail', 'railway'), ('rails', 'railway'), ('rock', 'rocky'), ('forest', 'forested'),
                     ('snow', 'snowman'), ('track', 'tracks')):
            self.assertTrue(main.same_root(a, b), (a, b))
        for a, b in (('train', 'track'), ('sun', 'sunny'), ('pine', 'spine'), ('green', 'green')):
            self.assertFalse(main.same_root(a, b), (a, b))

    def test_word_of_same_root_is_dropped(self):
        title, words = self.thread.build_title("Railway Rails Curve Beside Pine Trees and Platform", [])
        self.assertEqual(title, "Railway Curve Beside Pine Trees and Platform")
        title, _ = self.thread.build_title("Rocky Gorge With Rock Walls and Forested Forest Slopes", [])
        self.assertEqual(title, "Rocky Gorge With Walls and Forested Slopes")

    def test_title_does_not_end_with_adverb(self):
        title, _ = self.thread.build_title("Sunlit Concrete Arch Framed by Pine Foliage Ahead", [])
        self.assertEqual(title, "Sunlit Concrete Arch Framed by Pine Foliage")
        title, _ = self.thread.build_title("Old Train Station Building Standing Nearby", ['ahead', 'platform'])
        self.assertEqual(title, "Old Train Station Building Standing Platform")

    def test_retitle_rebuilds_keywords(self):
        meta = {'title': "Railway Rails Curve Beside Pine Trees and Platform",
                'description': "A moving viewpoint follows a curved path past a rural station",
                'keywords': "train, bend, gravel, sleepers, summer"}
        title, keywords = self.thread.retitle(meta, "Train Rails Bend Beside Pine Trees and Platform")
        self.assertEqual(title, "Train Rails Bend Beside Pine Trees and Platform")
        self.assertEqual(keywords, "gravel, sleepers, summer, railway, curve")
        self.assertIsNone(self.thread.retitle(meta, "Train Rails Bend Past Rural Station Platform"))  # description words


class BuildDescriptionTest(unittest.TestCase):
    def setUp(self):
        self.thread = make_thread()

    def test_cuts_before_forbidden_word_instead_of_removing_it(self):
        cases = {
            ("The camera moves along a dim passage toward a sunlit forest", 'toward'):
                "The camera moves along a dim passage",
            ("A moving viewpoint enters sunlit woods as the tunnel fades behind it", 'tunnel'):
                "A moving viewpoint enters sunlit woods",
            ("A moving viewpoint follows a winding path beneath trees and into darkness", 'into'):
                "A moving viewpoint follows a winding path beneath trees",
        }
        for (text, word), expected in cases.items():
            self.assertEqual(self.thread.build_description(text, {word}), expected)

    def test_truncates_before_unfinished_phrase(self):
        cases = {
            "A forward moving shot follows a winding path through sunny woods and rocky hillsides":
                "A forward moving shot follows a winding path through sunny woods",
            "A historic train moves slowly by passengers under sunny skies and wooded hillsides":
                "A historic train moves slowly by passengers under sunny skies",
            "The distant opening grows smaller as the camera moves underground along the rails":
                "The distant opening grows smaller as the camera moves underground",
        }
        for text, expected in cases.items():
            self.assertEqual(self.thread.build_description(text, set()), expected)

    def test_removes_title_and_generic_words(self):
        result = self.thread.build_description("A quiet park scene with snowy trees beside the frozen lake",
                                               {'park', 'trees'})
        self.assertEqual(result, "A quiet with snowy beside the frozen lake")

    def test_strips_dangling_words(self):
        result = self.thread.build_description("Calm water flows past mossy rocks under the", set())
        self.assertEqual(result, "Calm water flows past mossy rocks")

    def test_capitalizes_first_letter(self):
        self.assertEqual(self.thread.build_description("calm water flows past mossy rocks", set()),
                         "Calm water flows past mossy rocks")

    def test_too_short_returns_none(self):
        self.assertIsNone(self.thread.build_description("Snowy park path", {'park'}))
        self.assertIsNone(self.thread.build_description("", set()))


class ParseResponseTest(unittest.TestCase):
    def setUp(self):
        self.thread = make_thread()

    def test_plain_json(self):
        self.assertEqual(self.thread._parse_ai_response(json.dumps(AI_RESPONSE)), AI_RESPONSE)

    def test_markdown_block(self):
        content = "Here you go:\n```json\n" + json.dumps(AI_RESPONSE) + "\n```"
        self.assertEqual(self.thread._parse_ai_response(content), AI_RESPONSE)

    def test_invalid_json(self):
        broken = json.dumps(AI_RESPONSE)[:-20] + '"oops" "x"}'
        self.assertIsNone(self.thread._parse_ai_response(broken))
        self.assertIsNone(self.thread._parse_ai_response("no json here"))


class OpenAIRequestTest(unittest.TestCase):
    def setUp(self):
        self.thread = make_thread()
        self.tmp = tempfile.TemporaryDirectory()
        self.image = Path(self.tmp.name) / 'frame.jpg'
        self.image.write_bytes(b'\xff\xd8fake jpeg')
        sleep_patch = mock.patch.object(main.time, 'sleep')
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def run_request(self, queue, is_video=False):
        fake = FakeOpenAI(queue)
        with mock.patch.object(main, 'OpenAI', fake.client):
            result = self.thread._analyze_with_openai_api([self.image] * (3 if is_video else 1), "sk-test", is_video)
        return result, fake.calls

    def test_request_parameters(self):
        result, calls = self.run_request([json.dumps(AI_RESPONSE)], is_video=True)
        self.assertEqual(result, AI_RESPONSE)
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call['model'], 'gpt-6-luna')
        self.assertEqual(call['reasoning_effort'], 'none')
        self.assertEqual(call['response_format'], {"type": "json_object"})
        content = call['messages'][0]['content']
        self.assertEqual(sum(1 for part in content if part['type'] == 'image_url'), 3)
        self.assertIn('ONE stock video clip', content[0]['text'])

    def test_retry_on_invalid_json(self):
        result, calls = self.run_request(["{broken", json.dumps(AI_RESPONSE)])
        self.assertEqual(result, AI_RESPONSE)
        self.assertEqual(len(calls), 2)

    def test_none_after_two_invalid_responses(self):
        result, calls = self.run_request(["{broken", "{broken"])
        self.assertIsNone(result)
        self.assertEqual(len(calls), 2)

    def test_retry_on_temporary_error(self):
        result, calls = self.run_request([Exception("Error code: 500 - server error"), json.dumps(AI_RESPONSE)])
        self.assertEqual(result, AI_RESPONSE)
        self.assertEqual(len(calls), 2)

    def test_rate_limit_waits_and_retries(self):
        result, _ = self.run_request([Exception("Error code: 429 - Rate limit reached"), json.dumps(AI_RESPONSE)])
        self.assertEqual(result, AI_RESPONSE)
        main.time.sleep.assert_called_with(60)

    def test_fatal_error_is_raised(self):
        for error in ["Error code: 401 - Incorrect API key provided", "Error code: 429 - insufficient_quota"]:
            with self.assertRaises(main.FatalAPIError):
                self.run_request([Exception(error)])

    def test_is_fatal_api_error(self):
        self.assertTrue(main.is_fatal_api_error("Error code: 401 - invalid_api_key"))
        self.assertTrue(main.is_fatal_api_error("You exceeded your current quota: insufficient_quota"))
        self.assertFalse(main.is_fatal_api_error("Error code: 429 - Rate limit reached"))
        self.assertFalse(main.is_fatal_api_error("Error code: 500"))


class RewriteDescriptionTest(unittest.TestCase):
    def setUp(self):
        self.thread = make_thread()

    def rewrite(self, queue, forbidden=('park', 'scene')):
        fake = FakeOpenAI(queue)
        with mock.patch.object(main, 'OpenAI', fake.client):
            result = self.thread._rewrite_description("A quiet park scene in winter", list(forbidden))
        return result, fake.calls

    def test_clean_rewrite(self):
        result, calls = self.rewrite(["A quiet snowy garden on a calm winter day"])
        self.assertEqual(result, "A quiet snowy garden on a calm winter day")
        self.assertEqual(len(calls), 1)
        self.assertIn('park, scene', calls[0]['messages'][0]['content'])

    def test_retry_when_forbidden_word_remains(self):
        result, calls = self.rewrite(["A quiet winter scene", "A quiet snowy garden on a calm winter day"])
        self.assertEqual(result, "A quiet snowy garden on a calm winter day")
        self.assertEqual(len(calls), 2)

    def test_retry_when_too_long(self):
        long_text = "A quiet snowy garden " * 5
        result, calls = self.rewrite([long_text, "A quiet snowy garden on a calm winter day"])
        self.assertEqual(result, "A quiet snowy garden on a calm winter day")
        self.assertEqual(len(calls), 2)

    def test_three_attempts_with_feedback(self):
        result, calls = self.rewrite(["A quiet park", "A calm park in snow", "A quiet snowy garden on a calm winter day"])
        self.assertEqual(result, "A quiet snowy garden on a calm winter day")
        self.assertEqual(len(calls), 3)
        feedback = calls[1]['messages'][-1]['content']
        self.assertIn('park', feedback)
        self.assertEqual(calls[1]['messages'][-2], {"role": "assistant", "content": "A quiet park"})

    def test_best_attempt_returned_when_none_is_clean(self):
        too_long = "A quiet snowy garden with frosted hedges and benches on a calm and bright winter day"
        result, calls = self.rewrite(["A quiet park scene", too_long, "A park in snow"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(result, too_long)  # no forbidden words: only too long, cut in code later

    def test_error_returns_none(self):
        result, _ = self.rewrite([Exception("Error code: 500")])
        self.assertIsNone(result)

    def test_no_api_key(self):
        thread = make_thread(api_key=None)
        with mock.patch.dict(os.environ, {}, clear=True):
            thread.api_key = None
            self.assertIsNone(thread._rewrite_description("text", ['park']))


@unittest.skipUnless(sys.platform == 'darwin', "macOS spell checker")
class GluedWordsTest(unittest.TestCase):
    def test_real_spell_checker(self):
        self.assertEqual(FIND_GLUED_WORDS(['stationhouse', 'railwayline', 'freightcar', 'railway', 'smartphone',
                                           'countryside', 'sleepers', 'european', 'poland', 'xqzvbnmlkj']),
                         {'stationhouse': ('station', 'house'), 'railwayline': ('railway', 'line'),
                          'freightcar': ('freight', 'car')})

    def test_no_spell_checker(self):
        with mock.patch.object(main.subprocess, 'run', side_effect=FileNotFoundError):
            self.assertEqual(FIND_GLUED_WORDS(['railwayline']), {})
        self.assertEqual(FIND_GLUED_WORDS(['car', 'rail']), {})  # short words are not checked

    def test_compound_terms_not_split(self):
        # The spell checker does not know nightscape, but "night, scape" is not a stock keyword
        with mock.patch.object(main.subprocess, 'run') as run:
            self.assertEqual(FIND_GLUED_WORDS(['nightscape', 'hyperlapse', 'skyscapes']), {})
        run.assert_not_called()
        self.assertEqual(FIND_GLUED_WORDS(['nightscape', 'railwayline']), {'railwayline': ('railway', 'line')})


class CleanTextTest(unittest.TestCase):
    def test_hyphen_separates_words(self):
        thread = make_thread()
        self.assertEqual(thread.clean_text("A forward-facing shot of old\u2014fashioned cars/trains!"),
                         "A forward facing shot of old fashioned cars trains")


class KeychainTest(unittest.TestCase):
    def test_save_and_load(self):
        storage = {}
        fake_keyring = types.SimpleNamespace(
            set_password=lambda service, user, value: storage.__setitem__((service, user), value),
            get_password=lambda service, user: storage.get((service, user)))
        with mock.patch.object(main, 'keyring', fake_keyring, create=True), \
                mock.patch.object(main, 'KEYRING_AVAILABLE', True):
            self.assertIsNone(main.load_saved_api_key())
            self.assertTrue(main.save_api_key("sk-saved"))
            self.assertEqual(main.load_saved_api_key(), "sk-saved")
            self.assertEqual(list(storage), [(main.KEYRING_SERVICE, main.KEYRING_USER)])

    def test_keychain_errors_are_handled(self):
        def fail(*args):
            raise RuntimeError("keychain locked")
        fake_keyring = types.SimpleNamespace(set_password=fail, get_password=fail)
        with mock.patch.object(main, 'keyring', fake_keyring, create=True), \
                mock.patch.object(main, 'KEYRING_AVAILABLE', True):
            self.assertIsNone(main.load_saved_api_key())
            self.assertFalse(main.save_api_key("sk-x"))

    def test_keyring_not_installed(self):
        with mock.patch.object(main, 'KEYRING_AVAILABLE', False):
            self.assertIsNone(main.load_saved_api_key())
            self.assertFalse(main.save_api_key("sk-x"))

    def test_no_hardcoded_api_key(self):
        source = Path(main.__file__).read_text()
        self.assertNotRegex(source, r'sk-(proj-)?[A-Za-z0-9_-]{20,}')


class IstockTemplateTest(unittest.TestCase):
    def test_fields_come_from_esp_template(self):
        self.assertEqual(main.ISTOCK_VIDEO_CSV_FIELDS, ['file name', 'description', 'title', 'country',
                                                        'poster timecode', 'keywords', 'shot speed', 'date created'])

    def test_template_header_is_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / 'template.csv'
            template.write_text('\ufefftitle,file name,new column\n', encoding='utf-8')
            self.assertEqual(main.load_istock_fields(template), ['title', 'file name', 'new column'])
            self.assertEqual(main.load_istock_fields(Path(tmp) / 'missing.csv'), main.ISTOCK_DEFAULT_FIELDS)

    def test_shot_speed_values_are_esp_values(self):
        self.assertEqual(main.SHOT_SPEEDS, ['Real Time', 'Slow Motion', 'Time Lapse'])
        with tempfile.TemporaryDirectory() as tmp:
            thread = main.ProcessingThread('.', tmp, api_key=None, shot_speed='Time Lapse')
            thread._video_rows.append({'filename': 'x.mov', 'created': '', 'poster': '',
                                       'metadata': {'title': 'T', 'description': 'D', 'keywords': 'a'}})
            thread.write_csvs()
            with open(thread.csv_path(main.ISTOCK_VIDEO_CSV_NAME), newline='', encoding='utf-8') as f:
                self.assertEqual(next(csv.DictReader(f))['shot speed'], 'Time Lapse')

    def test_row_follows_template_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            thread = make_thread(output_folder=tmp, country='Serbia')
            meta = {'title': 'T', 'description': 'D', 'keywords': 'a, b'}
            thread._video_rows.append({'filename': 'x.mov', 'created': '05/25/2026', 'poster': '00:00:07:30',
                                       'metadata': meta})
            with mock.patch.object(main, 'ISTOCK_VIDEO_CSV_FIELDS', ['title', 'date created', 'unknown', 'file name']):
                thread.write_csvs()
            self.assertEqual(thread.csv_path(main.ISTOCK_VIDEO_CSV_NAME).read_text(encoding='utf-8'),
                             'title,date created,unknown,file name\nT,05/25/2026,,x.mov\n')


class ShutterstockCsvTest(unittest.TestCase):
    def test_sample_template_header(self):
        path = main.ISTOCK_TEMPLATE_PATH.parent / 'shutterstock-MetadataTemplate.csv'
        with open(path, newline='', encoding='utf-8') as f:
            self.assertEqual(next(csv.reader(f)), main.SHUTTERSTOCK_CSV_FIELDS)

    def test_row(self):
        meta = {'title': 'Railway Track Curving Through Pine Forest',
                'description': 'The camera glides along a quiet route', 'keywords': 'train, rails , tunnel'}
        self.assertEqual(main.shutterstock_row('a.jpg', meta),
                         ['a.jpg', 'Railway Track Curving Through Pine Forest. The camera glides along a quiet route',
                          'train,rails,tunnel', '', 'no', 'no', 'no'])
        meta['shutterstock_categories'] = ['Transportation', 'Nature']
        self.assertEqual(main.shutterstock_row('a.jpg', meta)[3], 'Transportation,Nature')

    def test_categories(self):
        self.assertEqual(main.shutterstock_categories('food AND drink, Sky, nature, people'),
                         ['Food and drink', 'Nature'])
        self.assertEqual(main.shutterstock_categories(['Animals/Wildlife']), ['Animals/Wildlife'])
        # No valid AI pick: categories whose words are keywords (singular too), in keyword order
        self.assertEqual(main.shutterstock_categories('Sky', 'train, transportation, animal, nature'),
                         ['Transportation', 'Animals/Wildlife'])
        self.assertEqual(main.shutterstock_categories(None, 'sky, cloud'), [])

    def test_prompt_lists_all_categories(self):
        for name in main.SHUTTERSTOCK_CATEGORIES:
            self.assertIn(name, main.ANALYSIS_PROMPT)

    def test_description_fits_200_characters(self):
        meta = {'title': 'Railway Track Curving Through Pine Forest', 'description': 'word ' * 40}
        self.assertEqual(main.shutterstock_description(meta), meta['title'])
        self.assertEqual(main.shutterstock_description({'description': 'Only description'}), 'Only description')


class StockSelectionTest(unittest.TestCase):
    META = {'title': 'T', 'description': 'D', 'keywords': 'a, b'}

    def write(self, stocks, videos=1, photos=1):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        thread = make_thread(output_folder=self.tmp.name, stocks=stocks)
        for i in range(videos):
            thread.write_video_csv_row(f'v{i}.mov', dict(self.META))
        for i in range(photos):
            thread.add_photo_csv_row(f'p{i}.jpg', dict(self.META))
        return thread, output_files(self.tmp.name)

    def test_all_stocks(self):
        thread, files = self.write(main.STOCKS)
        self.assertEqual(files, ['Adobe Stock/adobe_stock_videos.csv', 'Shutterstock/shutterstock_photos.csv',
                                 'Shutterstock/shutterstock_videos.csv', 'iStock/istock_videos.csv'])
        with open(Path(self.tmp.name) / stock_csv(main.SHUTTERSTOCK_PHOTO_CSV_NAME), newline='', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([r['Filename'] for r in rows], ['p0.jpg'])
        self.assertEqual(rows[0]['Description'], 'T. D')

    def test_only_selected_stocks(self):
        thread, files = self.write(['shutterstock'])
        self.assertEqual(files, ['Shutterstock/shutterstock_photos.csv', 'Shutterstock/shutterstock_videos.csv'])
        self.assertIn('Video metadata added to Shutterstock/shutterstock_videos.csv (video', '\n'.join(thread.logs))
        thread, files = self.write(['adobe'])
        self.assertEqual(files, ['Adobe Stock/adobe_stock_videos.csv'])  # photos have no Adobe Stock CSV

    def test_no_csv_for_kind_without_files(self):
        thread, files = self.write(main.STOCKS, videos=0)
        self.assertEqual(files, ['Shutterstock/shutterstock_photos.csv'])

    def test_earlier_csvs_are_kept_new_one_is_numbered(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Shutterstock'
            folder.mkdir()
            for name in ('shutterstock_videos.csv', 'shutterstock_videos_2.csv'):
                (folder / name).write_text('old')
            thread = make_thread(output_folder=tmp, stocks=['shutterstock'])
            thread.write_video_csv_row('v.mov', dict(self.META))
            thread.write_video_csv_row('w.mov', dict(self.META))  # same file till the end of the run
            self.assertEqual(output_files(tmp), ['Shutterstock/shutterstock_videos.csv',
                                                 'Shutterstock/shutterstock_videos_2.csv',
                                                 'Shutterstock/shutterstock_videos_3.csv'])
            self.assertEqual((folder / 'shutterstock_videos.csv').read_text(), 'old')
            with open(folder / 'shutterstock_videos_3.csv', newline='', encoding='utf-8') as f:
                self.assertEqual([r['Filename'] for r in csv.DictReader(f)], ['v.mov', 'w.mov'])

    def test_free_csv_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(main.free_csv_path(tmp, 'a.csv'), Path(tmp) / 'a.csv')
            (Path(tmp) / 'a.csv').write_text('')
            self.assertEqual(main.free_csv_path(tmp, 'a.csv'), Path(tmp) / 'a_2.csv')


class RecheckTitlesTest(unittest.TestCase):
    TITLE = "Railway Track Curving Through Sunny Pine Forest"

    def setUp(self):
        self.out = tempfile.TemporaryDirectory()
        self.addCleanup(self.out.cleanup)
        self.thread = make_thread(output_folder=self.out.name, country='Serbia')
        for name in ('a.mov', 'b.mov', 'c.mov'):
            self.thread._video_rows.append({'filename': name, 'created': '', 'poster': '', 'metadata': {
                'title': self.TITLE,
                'description': "The camera glides along a quiet route amid rugged slopes",
                'keywords': "train, rails, tunnel, bridge, cliff, river, gravel, summer"}})

    def recheck(self, queue):
        fake = FakeOpenAI(queue)
        with mock.patch.object(main, 'OpenAI', fake.client):
            self.thread.recheck_video_csvs()
        return [row['metadata'] for row in self.thread._video_rows], fake.calls

    def test_repeated_titles_are_replaced(self):
        rows, calls = self.recheck([
            self.TITLE,  # still the used title: retried
            "Railway Track Approaching Dark Tunnel in Pine Forest",
            "Train Rails Crossing Old Bridge Over Mountain River",
        ])
        self.assertEqual(rows[0]['title'], self.TITLE)  # first clip keeps its title
        self.assertEqual(rows[1]['title'], "Railway Track Approaching Dark Tunnel in Pine Forest")
        self.assertEqual(rows[2]['title'], "Train Rails Crossing Old Bridge Over Mountain River")
        self.assertEqual(len(calls), 3)
        self.assertIn(self.TITLE, calls[0]['messages'][0]['content'])
        # New title words leave keywords, dropped title words come back
        kw1 = rows[1]['keywords'].split(', ')
        self.assertNotIn('tunnel', kw1)
        self.assertIn('curving', kw1)
        self.assertIn('sunny', kw1)
        kw2 = rows[2]['keywords'].split(', ')
        self.assertNotIn('train', kw2)
        self.assertNotIn('bridge', kw2)
        self.assertIn('pine', kw2)
        # Both CSVs are rewritten
        with open(self.thread.csv_path(main.ISTOCK_VIDEO_CSV_NAME), newline='', encoding='utf-8') as f:
            istock = list(csv.DictReader(f))
        self.assertEqual([r['title'] for r in istock], [r['title'] for r in rows])
        self.assertEqual(istock[1]['keywords'], rows[1]['keywords'])
        with open(self.thread.csv_path(main.VIDEO_CSV_NAME), newline='', encoding='utf-8') as f:
            self.assertEqual([r['Title'] for r in csv.DictReader(f)], [r['title'] for r in rows])
        self.assertIn('Repeated titles replaced: 2', '\n'.join(self.thread.logs))

    def test_title_with_description_word_is_rejected(self):
        rows, calls = self.recheck([
            "Railway Track Along Quiet Route Through Pine Forest",  # 'quiet', 'route' are description words
            "Railway Track Approaching Dark Tunnel in Pine Forest",
            "Train Rails Crossing Old Bridge Over Mountain River",
        ])
        self.assertEqual(rows[1]['title'], "Railway Track Approaching Dark Tunnel in Pine Forest")

    def test_fallback_without_ai(self):
        self.thread.api_key = None
        rows, calls = self.recheck([])
        self.assertEqual(calls, [])
        self.assertEqual(len({r['title'] for r in rows}), 3)
        self.assertTrue(rows[1]['title'].startswith(self.TITLE))

    def test_no_repeats_no_requests(self):
        self.thread._video_rows[1]['metadata']['title'] = "Train Rails Crossing Old Bridge Over Mountain River"
        del self.thread._video_rows[2]
        rows, calls = self.recheck([])
        self.assertEqual(calls, [])
        self.assertIn('Repeated titles replaced: 0 (none found)', '\n'.join(self.thread.logs))


class IstockFieldsTest(unittest.TestCase):
    def test_shoot_date_from_filename(self):
        self.assertEqual(main.shoot_date_from_filename('DJI_20260525104306_0231_D01214551.mov'), '05/25/2026')
        self.assertEqual(main.shoot_date_from_filename('VID_20240131_120000.mp4'), '01/31/2024')
        self.assertEqual(main.shoot_date_from_filename('PXL_2025-12-03_clip.mov'), '12/03/2025')
        self.assertEqual(main.shoot_date_from_filename('a_ok.mp4'), '')
        self.assertEqual(main.shoot_date_from_filename('clip_20261399.mov'), '')  # not a real date
        self.assertEqual(main.shoot_date_from_filename('clip_20991231.mov'), '')  # future date

    def test_poster_timecode(self):
        self.assertEqual(main.poster_timecode({'duration': 14.948267, 'fps': 59.94005994005994}), '00:00:07:28')
        self.assertEqual(main.poster_timecode({'duration': 6.0, 'fps': 25.0}), '00:00:03:00')
        self.assertEqual(main.poster_timecode({'duration': 7300.0, 'fps': 30.0}), '01:00:50:00')
        self.assertEqual(main.poster_timecode({'duration': None, 'fps': 25.0}), '')
        self.assertEqual(main.poster_timecode(None), '')


@unittest.skipUnless(FFMPEG_AVAILABLE, "ffmpeg/ffprobe not installed")
class BatchProcessingTest(unittest.TestCase):
    """End-to-end run() with generated clips and a stubbed AI"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.input = Path(cls.tmp.name) / 'in'
        cls.input.mkdir()

        def clip(name, size, rate, duration):
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc=size={size}:rate={rate}',
                            '-t', str(duration), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(cls.input / name)],
                           check=True)

        clip('a_ok.mp4', '1920x1080', 25, 6)
        clip('b_vertical.mov', '1080x1920', 30, 6)
        clip('c_720p.mp4', '1280x720', 25, 6)
        clip('d_short.mp4', '1920x1080', 25, 3)
        from PIL import Image
        Image.new('RGB', (64, 48), 'red').save(cls.input / 'e_photo.jpg')
        (cls.input / 'notes.txt').write_text('ignored')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.output = Path(tempfile.mkdtemp(dir=self.tmp.name))
        sleep_patch = mock.patch.object(main.time, 'sleep')
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def run_batch(self, analyze, country='Serbia', stocks=tuple(main.STOCKS)):
        thread = make_thread(input_folder=str(self.input), output_folder=str(self.output), country=country,
                             stocks=stocks)
        thread.analyze_image_with_ai = analyze
        thread._rewrite_description = mock.Mock(return_value=None)
        thread._choose_istock_terms = mock.Mock(return_value=None)
        thread.write_iptc_with_exiftool = mock.Mock(side_effect=FileNotFoundError)
        thread.run()
        return thread

    def read_csv(self, name=main.VIDEO_CSV_NAME):
        with open(self.output / stock_csv(name), newline='', encoding='utf-8') as f:
            return list(csv.DictReader(f))

    def test_batch(self):
        before = {p.name: p.stat().st_mtime for p in self.input.iterdir()}
        thread = self.run_batch(lambda path, info=None: dict(AI_RESPONSE))

        rows = self.read_csv()
        self.assertEqual([r['Filename'] for r in rows], ['a_ok.mp4', 'b_vertical.mov'])
        self.assertEqual(list(rows[0].keys()), ['Filename', 'Title', 'Keywords', 'Category', 'Releases'])
        self.assertTrue(rows[1]['Title'].endswith('Vertical Video'))
        self.assertTrue(rows[1]['Keywords'].startswith('vertical, video'))

        istock = self.read_csv(main.ISTOCK_VIDEO_CSV_NAME)
        self.assertEqual(list(istock[0].keys()), main.ISTOCK_VIDEO_CSV_FIELDS)
        raw = (self.output / stock_csv(main.ISTOCK_VIDEO_CSV_NAME)).read_bytes()
        template = main.ISTOCK_TEMPLATE_PATH.read_bytes()
        self.assertTrue(raw.startswith(template))  # header byte for byte as in the ESP template
        self.assertNotIn(b'\r\n', raw)
        for adobe_row, istock_row in zip(rows, istock):
            self.assertEqual(istock_row['file name'], adobe_row['Filename'])
            self.assertEqual(istock_row['title'], adobe_row['Title'])
            # ESP not available in tests: words stay as is, up to the iStock limit
            self.assertEqual(istock_row['keywords'].split(', '),
                             adobe_row['Keywords'].split(', ')[:main.MAX_ISTOCK_KEYWORDS])
            self.assertTrue(istock_row['description'])
            self.assertEqual(istock_row['country'], 'Serbia')
            self.assertEqual(istock_row['poster timecode'], '00:00:03:00')  # middle of a 6 s clip
            self.assertEqual(istock_row['date created'], '')  # no date in test file names
            self.assertEqual(istock_row['shot speed'], 'Real Time')  # default

        shutterstock = self.read_csv(main.SHUTTERSTOCK_VIDEO_CSV_NAME)
        self.assertEqual(list(shutterstock[0].keys()), main.SHUTTERSTOCK_CSV_FIELDS)
        for adobe_row, row in zip(rows, shutterstock):
            self.assertEqual(row['Filename'], adobe_row['Filename'])
            self.assertTrue(row['Description'].startswith(adobe_row['Title'] + '. '))
            self.assertEqual(row['Keywords'], adobe_row['Keywords'].replace(', ', ','))
        self.assertEqual([r['Filename'] for r in self.read_csv(main.SHUTTERSTOCK_PHOTO_CSV_NAME)], ['e_photo.jpg'])

        # Videos are not copied, photo is copied with metadata
        self.assertEqual(output_files(self.output),
                         ['Adobe Stock/adobe_stock_videos.csv', 'Shutterstock/shutterstock_photos.csv',
                          'Shutterstock/shutterstock_videos.csv', 'e_photo.jpg', 'iStock/istock_videos.csv'])
        self.assertEqual(before, {p.name: p.stat().st_mtime for p in self.input.iterdir()})

        log = '\n'.join(thread.logs)
        self.assertIn('Files found: 1 JPEG, 4 video', log)
        # Log of the run is saved to a file in the logs folder, not in the output folder
        self.assertEqual(thread.log_file_path.parent, thread.logs_folder)
        self.assertRegex(thread.log_file_path.name, r'^run_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.log$')
        saved = thread.log_file_path.read_text(encoding='utf-8')
        self.assertIn(f'TagStock PhotoKey {main.__version__}', saved)
        self.assertIn('Files found: 1 JPEG, 4 video', saved)
        self.assertIn(f'Log saved to: {thread.log_file_path}', saved)
        self.assertIn('c_720p.mp4: width 1280px is below minimum 1920px', log)
        self.assertIn('d_short.mp4: duration 3.00s is out of range 5-60s', log)

    def test_failed_file_is_skipped(self):
        def analyze(path, info=None):
            if path.name == 'a_ok.mp4':
                raise Exception("ERROR: No valid response from AI after 2 attempts")
            return dict(AI_RESPONSE)

        thread = self.run_batch(analyze)
        self.assertEqual([r['Filename'] for r in self.read_csv()], ['b_vertical.mov'])
        log = '\n'.join(thread.logs)
        self.assertIn('Files skipped (metadata generation failed, run again to retry): 1', log)
        self.assertIn('Processed files: 2 of 5', log)

    def test_failed_photo_copy_is_removed(self):
        def analyze(path, info=None):
            if path.suffix == '.jpg':
                raise Exception("ERROR: No valid response from AI after 2 attempts")
            return dict(AI_RESPONSE)

        self.run_batch(analyze)
        self.assertFalse((self.output / 'e_photo.jpg').exists())

    def test_fatal_error_stops_batch(self):
        calls = []

        def analyze(path, info=None):
            calls.append(path.name)
            raise main.FatalAPIError("OpenAI API error: Error code: 401")

        finished = []
        thread = make_thread(input_folder=str(self.input), output_folder=str(self.output))
        thread.analyze_image_with_ai = analyze
        thread.finished.connect(finished.append)
        thread.run()
        self.assertEqual(calls, ['a_ok.mp4'])
        self.assertIn('Processing stopped: check API key and balance', '\n'.join(thread.logs))
        self.assertEqual(finished, [[]])  # finished is emitted once, after the log file is saved
        self.assertIn('Processing stopped: check API key and balance', thread.log_file_path.read_text())

    def test_empty_country_is_warned(self):
        thread = self.run_batch(lambda path, info=None: dict(AI_RESPONSE), country='')
        self.assertEqual({r['country'] for r in self.read_csv(main.ISTOCK_VIDEO_CSV_NAME)}, {''})
        log = '\n'.join(thread.logs)
        self.assertIn('Country of shoot is empty in the iStock CSV', log)
        self.assertIn('No shooting date in file name a_ok.mp4', log)

    def test_istock_not_selected(self):
        thread = self.run_batch(lambda path, info=None: dict(AI_RESPONSE), stocks=['adobe'])
        self.assertEqual(output_files(self.output), ['Adobe Stock/adobe_stock_videos.csv', 'e_photo.jpg'])
        log = '\n'.join(thread.logs)
        self.assertNotIn('[iStock]', log)
        self.assertNotIn('Country of shoot is empty', log)
        self.assertIn('CSV for: Adobe Stock', log)

    def test_no_stock_selected_skips_videos(self):
        calls = []

        def analyze(path, info=None):
            calls.append(path.name)
            return dict(AI_RESPONSE)

        thread = self.run_batch(analyze, stocks=[])
        self.assertEqual(calls, ['e_photo.jpg'])  # videos would get no output, so no AI requests for them
        self.assertEqual(output_files(self.output), ['e_photo.jpg'])
        self.assertIn('CSV for: none', '\n'.join(thread.logs))

    def test_new_run_writes_numbered_csvs(self):
        self.run_batch(lambda path, info=None: dict(AI_RESPONSE))
        thread = self.run_batch(lambda path, info=None: dict(AI_RESPONSE), stocks=['istock'])
        self.assertEqual(output_files(self.output),
                         ['Adobe Stock/adobe_stock_videos.csv', 'Shutterstock/shutterstock_photos.csv',
                          'Shutterstock/shutterstock_videos.csv', 'e_photo.jpg', 'iStock/istock_videos.csv',
                          'iStock/istock_videos_2.csv'])
        self.assertEqual(len(self.read_csv()), 2)  # earlier CSV is kept as is
        with open(self.output / 'iStock' / 'istock_videos_2.csv', newline='', encoding='utf-8') as f:
            self.assertEqual(len(list(csv.DictReader(f))), 2)
        self.assertIn(f"Metadata CSV: {self.output / 'iStock' / 'istock_videos_2.csv'}", '\n'.join(thread.logs))



class RememberedSettingsTest(unittest.TestCase):
    """Folders and settings survive a restart, shot speed is Real Time at every launch.
    Settings go to a temporary ini file, never to the user's real settings"""

    def setUp(self):
        from PyQt6.QtWidgets import QApplication
        self.qt_app = QApplication.instance() or QApplication([])
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        ini, settings_class = str(self.tmp / 'settings.ini'), main.QSettings
        for patch in (mock.patch.object(main, 'QSettings',
                                        lambda *args: settings_class(ini, settings_class.Format.IniFormat)),
                      mock.patch.object(main, 'load_saved_api_key', return_value=None)):
            patch.start()
            self.addCleanup(patch.stop)

    def open_window(self):
        window = main.KeyCutterApp()
        self.addCleanup(window.close)
        return window

    def test_restart_keeps_settings_except_shot_speed(self):
        source, output = self.tmp / 'source', self.tmp / 'output'
        source.mkdir()
        output.mkdir()
        window = self.open_window()
        with mock.patch.object(main.QFileDialog, 'getExistingDirectory', side_effect=[str(source), str(output)]):
            window.select_input_folder()
            window.select_output_folder()
        window.country_edit.setText('Serbia')
        window.stock_checkboxes['istock'].setChecked(False)
        window.shot_speed_combo.setCurrentText('Time Lapse')

        window = self.open_window()
        self.assertEqual((window.selected_folder, window.output_folder), (str(source), str(output)))
        self.assertEqual(window.folder_path_edit.text(), str(source))
        self.assertEqual(window.country_edit.text(), 'Serbia')
        self.assertEqual([k for k, c in window.stock_checkboxes.items() if c.isChecked()], ['adobe', 'shutterstock'])
        self.assertEqual(window.shot_speed_combo.currentText(), 'Real Time')

    def test_removed_folder_is_not_restored(self):
        main.QSettings().setValue('input_folder', str(self.tmp / 'gone'))
        window = self.open_window()
        self.assertIsNone(window.selected_folder)
        self.assertEqual(window.folder_path_edit.text(), '')


if __name__ == '__main__':
    unittest.main()
