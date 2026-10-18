"""iStock (Getty ESP) controlled vocabulary.

ESP accepts only terms of its own vocabulary: "sleepers" stays red, "Railroad Tie" is accepted at once.
Candidate terms for our words and title suggestions come from ESP itself (the same requests the ESP page
makes for keyword disambiguation and "Get suggestions"). ESP needs a logged-in session, so the requests run
inside an open ESP tab in Google Chrome (View > Developer > Allow JavaScript from Apple Events).
Looked-up words are kept in istock_vocabulary.json, so known words need no ESP.
"""
import json
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlencode

VOCABULARY_PATH = Path(__file__).resolve().parent / 'istock_vocabulary.json'
ESP_URL_PREFIX = 'https://esp.gettyimages.com/'
# ESP adds orientation and country of shoot itself (Horizontal, Serbia); the limit is 50
MAX_ISTOCK_KEYWORDS = 48
LOOKUP_CHUNK = 40  # words per request
KEYWORD_PARAMS = {'asset_family': 'creative', 'asset_type': 'istock_creative_video', 'disambiguation': 'Partial',
                  'field': 'keywords', 'home_property': 'istock', 'media_type': 'video'}

# Runs a JS file in the first ESP tab of Google Chrome
_OSASCRIPT = '''on run argv
  set js to read POSIX file (item 1 of argv) as «class utf8»
  tell application "Google Chrome"
    repeat with w in windows
      repeat with t in tabs of w
        if URL of t starts with "%s" then return execute t javascript js
      end repeat
    end repeat
  end tell
  return "ESP_TAB_NOT_FOUND"
end run''' % ESP_URL_PREFIX


class EspUnavailable(Exception):
    """ESP tab is not open in Chrome, JavaScript from Apple Events is off, or a request failed"""


def run_in_esp_tab(js, timeout=30):
    """Runs JS in the ESP tab, returns its string result"""
    with tempfile.NamedTemporaryFile('w', suffix='.js', encoding='utf-8', delete=False) as f:
        f.write(js)
    try:
        result = subprocess.run(['osascript', '-e', _OSASCRIPT, f.name], capture_output=True, text=True,
                                timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise EspUnavailable(f"osascript failed: {e}")
    finally:
        Path(f.name).unlink(missing_ok=True)
    if result.returncode != 0:
        raise EspUnavailable(result.stderr.strip()[:300])
    output = result.stdout.rstrip('\n')
    if output == 'ESP_TAB_NOT_FOUND':
        raise EspUnavailable("no ESP tab open in Google Chrome")
    return output


def esp_get_json(urls, timeout=60):
    """GET requests from the ESP page (its session cookies). Returns parsed JSON per url.
    JS never throws on the page: everything is wrapped, promise errors are caught"""
    key = '__keyCutter_' + uuid.uuid4().hex
    start = '''(() => { try {
  window[%(key)s] = { status: 'pending' };
  const urls = %(urls)s;
  Promise.all(urls.map(u => fetch(u, { credentials: 'include' })
      .then(r => r.ok ? r.text() : Promise.reject('HTTP ' + r.status + ' ' + u))))
    .then(texts => { window[%(key)s] = { status: 'done', data: encodeURIComponent(JSON.stringify(texts)) }; })
    .catch(e => { window[%(key)s] = { status: 'error', error: String(e) }; });
  return 'STARTED';
} catch (e) { return 'ERR ' + e; } })()''' % {'key': json.dumps(key), 'urls': json.dumps(urls)}
    started = run_in_esp_tab(start)
    if started != 'STARTED':
        raise EspUnavailable(f"ESP request was not started: {started[:200]}")
    poll = '''(() => { try {
  const s = window[%s];
  if (!s) return 'MISSING';
  if (s.status === 'done') return 'DONE ' + s.data.length;
  return s.status === 'error' ? 'ERROR ' + s.error : 'PENDING';
} catch (e) { return 'ERR ' + e; } })()''' % json.dumps(key)
    deadline = time.time() + timeout
    while True:
        state = run_in_esp_tab(poll)
        if state.startswith('DONE '):
            length = int(state.split()[1])
            break
        if state != 'PENDING' or time.time() > deadline:
            raise EspUnavailable(f"ESP request failed: {state[:200]}")
        time.sleep(0.5)
    # Read in parts: AppleScript does not like very long strings
    chunks = []
    for offset in range(0, length, 200000):
        chunks.append(run_in_esp_tab('''(() => { try {
  const s = window[%s];
  return s && s.data ? s.data.slice(%d, %d) : 'MISSING';
} catch (e) { return 'ERR ' + e; } })()''' % (json.dumps(key), offset, offset + 200000)))
    run_in_esp_tab('(() => { try { delete window[%s]; return "OK"; } catch (e) { return "ERR " + e; } })()'
                   % json.dumps(key))
    return [json.loads(text) for text in json.loads(unquote(''.join(chunks)))]


def esp_lookup_words(words):
    """Candidate vocabulary terms for each word: {word: [{'term', 'term_id'}, ...]}.
    A word ESP knows nothing about gets an empty list"""
    words = list(dict.fromkeys(words))
    result = {}
    chunks = [words[i:i + LOOKUP_CHUNK] for i in range(0, len(words), LOOKUP_CHUNK)]
    urls = ['/api/submission/v2/keywords?' + urlencode(dict(KEYWORD_PARAMS, keywords=','.join(chunk)))
            for chunk in chunks]
    for chunk, data in zip(chunks, esp_get_json(urls)):
        found = {k.lower(): v for k, v in data.get('keywords', {}).items()}
        for word in chunk:
            result[word] = [{'term': c['term'], 'term_id': c['term_id']} for c in found.get(word.lower(), [])]
    return result


def esp_title_suggestions(titles):
    """ESP "Get suggestions" terms for each title: {title: [term, ...]}"""
    titles = list(dict.fromkeys(titles))
    urls = ['/api/submission/v2/title_keyword_suggestions?' +
            urlencode({'asset_type': 'istock_creative_video', 'title': title}) for title in titles]
    return {title: [item['term'] for item in data] for title, data in zip(titles, esp_get_json(urls, timeout=120))}


class IstockVocabulary:
    """Word -> ESP candidate terms, saved between runs"""

    def __init__(self, path=VOCABULARY_PATH):
        self.path = Path(path)
        self.words = {}
        if self.path.exists():
            try:
                self.words = json.loads(self.path.read_text(encoding='utf-8')).get('words', {})
            except (OSError, ValueError):
                self.words = {}

    def missing(self, words):
        return [w for w in dict.fromkeys(words) if w not in self.words]

    def candidates(self, word):
        return [c['term'] for c in self.words.get(word, [])]

    def update(self, looked_up):
        self.words.update(looked_up)

    def save(self):
        data = {'words': dict(sorted(self.words.items()))}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')


def finalize_terms(keywords, choices, suggested, candidates, suggestions, keep_words=()):
    """Builds the iStock keyword list from AI choices, only terms ESP offered.
    keywords: our words in order; choices: {word: term or None}; suggested: AI-picked suggestion terms;
    candidates: {word: [term, ...]}; suggestions: ESP suggestion terms for this title;
    keep_words: words not looked up in ESP yet, kept as is (ESP may still map them itself)"""
    terms, seen = [], set()

    def add(term):
        if term and term.lower() not in seen and len(terms) < MAX_ISTOCK_KEYWORDS:
            terms.append(term)
            seen.add(term.lower())

    for word in keywords:
        offered = {t.lower(): t for t in candidates.get(word, [])}
        choice = choices.get(word)
        if isinstance(choice, str) and choice.lower() in offered:
            add(offered[choice.lower()])
    for word in keep_words:
        add(word)
    offered = {t.lower(): t for t in suggestions}
    for term in suggested or []:
        if isinstance(term, str) and term.lower() in offered:
            add(offered[term.lower()])
    return terms
