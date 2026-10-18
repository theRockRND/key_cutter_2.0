import sys
import os
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QLabel, QLineEdit, QTextEdit, QProgressBar,
                             QCheckBox, QTableWidget, QTableWidgetItem,
                             QFileDialog, QMessageBox, QGroupBox, QHeaderView, QDialog,
                             QDialogButtonBox, QComboBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings
from PyQt6.QtGui import QPixmap, QIcon
from pathlib import Path
import csv
import re
import shutil
import subprocess
import base64
import json
import httpx
import time
import tempfile
from datetime import datetime
from PIL import Image
import piexif
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
try:
    import keyring
    KEYRING_AVAILABLE = True
except ImportError:
    KEYRING_AVAILABLE = False

class FatalAPIError(Exception):
    """API error that makes further processing pointless (wrong key, no quota)"""


def is_fatal_api_error(error_str):
    markers = ["401", "invalid_api_key", "Incorrect API key", "insufficient_quota", "403", "model_not_found"]
    return any(marker in error_str for marker in markers)


# Last used API key is stored in system keychain (macOS Keychain)
KEYRING_SERVICE = 'TagStock PhotoKey'
KEYRING_USER = 'openai_api_key'


def load_saved_api_key():
    """Returns API key saved in keychain or None"""
    if not KEYRING_AVAILABLE:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception as e:
        print(f"Failed to read API key from keychain: {e}")
        return None


def save_api_key(api_key):
    """Saves API key to keychain. Returns True on success"""
    if not KEYRING_AVAILABLE:
        return False
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, api_key)
        return True
    except Exception as e:
        print(f"Failed to save API key to keychain: {e}")
        return False

__version__ = "1.1.14"

# Local models removed - using only OpenAI API

from istock_keywords import (IstockVocabulary, EspUnavailable, esp_lookup_words, esp_title_suggestions,
                             finalize_terms, open_esp_tab, ESP_URL_PREFIX, MAX_ISTOCK_KEYWORDS)
from video_utils import (VIDEO_EXTENSIONS, is_video_file, probe_video, check_video_requirements,
                         get_orientation, extract_frames)

ANALYSIS_PROMPT = """Analyze this image carefully and describe what you see. Then provide metadata for stock photography.

IMPORTANT: Look at the image and describe what is actually shown - the main subject, objects, animals, people, scenery, colors, actions, etc.

Return ONLY a JSON object with this exact structure (no markdown, no code blocks):
{
  "title": "descriptive title based on what you see in the image, English only, no special characters",
  "description": "one natural English sentence about the image, 40-80 characters, no special characters",
  "keywords": "comma-separated list of 65 individual words describing the image content, English only, no special characters, only single words not phrases",
  "categories": "1 or 2 comma-separated categories, best fitting first, ONLY from: Animals/Wildlife, Arts, Backgrounds/Textures, Buildings/Landmarks, Business/Finance, Education, Food and drink, Healthcare/Medical, Holidays, Industrial, Nature, Objects, People, Religion, Science, Signs/Symbols, Sports/Recreation, Technology, Transportation"
}

CRITICAL RULES:
- Title: Describe the main subject/scene with 6 to 10 WORDS (e.g. "Deer Grazing On Green Grass In Field", "Sunset Over Mountains With Clouds"). Only English letters and spaces, no special characters.
- Description: ONE natural, grammatical sentence (e.g. "Young animal pauses near a wooden fence on a quiet summer morning"). 40-80 characters, English only, no special characters. It must NOT reuse any word from the title and should avoid words from the keywords - use synonyms instead.
- Keywords: List 65 individual REAL English words separated by commas (e.g. "deer, wildlife, nature, grass, animal, mammal, forest, outdoor, wild, grazing, brown, green, fence, trees, sky, sunny, natural, habitat"). Most relevant words first. Describe the main subject, objects, colors, textures, actions, environment, mood, season, time of day, weather, concepts. Only single words, no phrases, no invented words, no plural/singular duplicates of the same word, English only.

Analyze the image NOW and return ONLY the JSON object."""

# OpenAI model for image/video analysis
OPENAI_MODEL = 'gpt-6-luna'

VIDEO_CSV_NAME = 'adobe_stock_videos.csv'
# iStock (Getty ESP): Templates > Upload and apply
ISTOCK_VIDEO_CSV_NAME = 'istock_videos.csv'
# Columns and their order come from the ESP template (csv-templates/istock-MetadataTemplate.csv):
# replace that file if Getty changes the template
ISTOCK_TEMPLATE_PATH = Path(__file__).resolve().parent / 'csv-templates' / 'istock-MetadataTemplate.csv'
ISTOCK_DEFAULT_FIELDS = ['file name', 'description', 'title', 'country', 'poster timecode', 'keywords',
                         'shot speed', 'date created']
# ESP values for shot speed; one value for the whole run (Settings)
SHOT_SPEEDS = ['Real Time', 'Slow Motion', 'Time Lapse']
# Shutterstock: Portfolio > Not submitted > Upload CSV, photos and videos are separate tabs
SHUTTERSTOCK_PHOTO_CSV_NAME = 'shutterstock_photos.csv'
SHUTTERSTOCK_VIDEO_CSV_NAME = 'shutterstock_videos.csv'
# Columns, their order and yes/no values as in the Shutterstock sample (csv-templates/shutterstock-MetadataTemplate.csv)
SHUTTERSTOCK_CSV_FIELDS = ['Filename', 'Description', 'Keywords', 'Categories', 'Editorial', 'Mature content',
                           'illustration']
SHUTTERSTOCK_MAX_DESCRIPTION = 200
# Category 1 (required) and Category 2 (optional) on Shutterstock: names as in the site list for videos
# (photos have these and a few more). The AI picks them, ANALYSIS_PROMPT lists the same names
SHUTTERSTOCK_CATEGORIES = ['Animals/Wildlife', 'Arts', 'Backgrounds/Textures', 'Buildings/Landmarks', 'Business/Finance',
                           'Education', 'Food and drink', 'Healthcare/Medical', 'Holidays', 'Industrial', 'Nature',
                           'Objects', 'People', 'Religion', 'Science', 'Signs/Symbols', 'Sports/Recreation',
                           'Technology', 'Transportation']
SHUTTERSTOCK_MAX_CATEGORIES = 2
# Stock sites a CSV can be made for: key -> name in Settings and of its CSV folder in the output folder
STOCKS = {'adobe': 'Adobe Stock', 'istock': 'iStock', 'shutterstock': 'Shutterstock'}
CSV_STOCKS = {VIDEO_CSV_NAME: 'adobe', ISTOCK_VIDEO_CSV_NAME: 'istock', SHUTTERSTOCK_VIDEO_CSV_NAME: 'shutterstock',
              SHUTTERSTOCK_PHOTO_CSV_NAME: 'shutterstock'}


def free_csv_path(folder, csv_name):
    """folder/csv_name, or csv_name with _2, _3... if a CSV of an earlier run is already there"""
    path, number = Path(folder) / csv_name, 1
    while path.exists():
        number += 1
        path = Path(folder) / f"{Path(csv_name).stem}_{number}{Path(csv_name).suffix}"
    return path


def shutterstock_description(metadata):
    """Title and description as one text: Shutterstock has no title, and our description leaves out
    the title words. Title only if both do not fit in 200 characters"""
    title, description = metadata.get('title', ''), metadata.get('description', '')
    text = f"{title}. {description}" if title and description else title or description
    return text if len(text) <= SHUTTERSTOCK_MAX_DESCRIPTION else title[:SHUTTERSTOCK_MAX_DESCRIPTION]


def shutterstock_categories(ai_categories, keywords=''):
    """Up to 2 Shutterstock categories: the ones the AI picked (any case, a list or a comma-separated text),
    unknown names are dropped. Without a valid AI pick, the categories whose words are among the keywords
    (keyword order): 'train, rails, transportation' -> Transportation"""
    names = {name.lower(): name for name in SHUTTERSTOCK_CATEGORIES}
    picked = ai_categories if isinstance(ai_categories, list) else str(ai_categories or '').split(',')
    result = [names[p.strip().lower()] for p in picked if isinstance(p, str) and p.strip().lower() in names]
    if not result:
        category_words = {name: set(re.split(r'/| and ', name.lower())) for name in SHUTTERSTOCK_CATEGORIES}
        for keyword in (k.strip().lower() for k in keywords.split(',')):
            result += [name for name, words in category_words.items() if keyword in words or keyword + 's' in words]
    return list(dict.fromkeys(result))[:SHUTTERSTOCK_MAX_CATEGORIES]


def shutterstock_row(filename, metadata):
    keywords = ','.join(k.strip() for k in metadata.get('keywords', '').split(',') if k.strip())
    categories = ','.join(metadata.get('shutterstock_categories', []))
    return [filename, shutterstock_description(metadata), keywords, categories, 'no', 'no', 'no']


def load_istock_fields(template_path=ISTOCK_TEMPLATE_PATH):
    """Header row of the ESP template, ISTOCK_DEFAULT_FIELDS if the template is missing or empty"""
    try:
        with open(template_path, newline='', encoding='utf-8-sig') as f:
            header = [name.strip() for name in next(csv.reader(f), [])]
        return [name for name in header if name] or ISTOCK_DEFAULT_FIELDS
    except OSError:
        return ISTOCK_DEFAULT_FIELDS


ISTOCK_VIDEO_CSV_FIELDS = load_istock_fields()

# Shooting date in camera file names: DJI_20260525104306_..., VID_20260525_..., PXL_2026-05-25...
FILENAME_DATE_RE = re.compile(r'(?<!\d)(20\d{2})[-_]?(\d{2})[-_]?(\d{2})')


def shoot_date_from_filename(filename):
    """Shooting date from camera file name as MM/DD/YYYY, '' if the name has no date.
    File dates are not used: after editing (DaVinci etc.) they are the export date"""
    for match in FILENAME_DATE_RE.finditer(filename):
        try:
            date = datetime(*map(int, match.groups()))
        except ValueError:
            continue
        if date <= datetime.now():
            return date.strftime('%m/%d/%Y')
    return ''


def poster_timecode(video_info):
    """Middle frame of the clip as HH:MM:SS:FF, '' if duration or fps is unknown"""
    duration = (video_info or {}).get('duration')
    fps = (video_info or {}).get('fps')
    if not duration or not fps:
        return ''
    seconds, frame = divmod(int(duration * fps / 2), round(fps))
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}:{frame:02d}"

# Adobe Stock accepts up to 49 keywords
MAX_KEYWORDS = 49
MIN_KEYWORDS = 25  # below this a warning is logged

# Stop words: never used in title or keywords
STOP_WORDS = {
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "with", "is", "are", "was", "were",
    "and", "or", "but", "this", "that", "these", "those", "it", "its", "they", "them", "what", "you",
    "by", "from", "as", "be", "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "can", "up", "out", "so", "if", "no", "not", "only", "just",
    "more", "most", "very", "much", "many", "some", "any", "all", "each", "every", "both", "such",
    "than", "too", "also", "here", "there", "where", "when", "why", "how", "who", "which", "whose", "whom",
    "see", "image", "photography", "photo", "picture", "detailed", "describe", "scene", "happening",
    "showing", "view", "professional", "high", "quality"
}

# Generic words removed from description phrase (articles and prepositions are kept for grammar)
DESCRIPTION_BANNED_WORDS = {
    "see", "image", "photography", "photo", "picture", "detailed", "describe", "scene", "happening",
    "showing", "view", "professional", "high", "quality", "list", "main", "objects", "colors", "elements"
}

# Words that may not end a truncated description
DESCRIPTION_TRAILING_WORDS = STOP_WORDS | {"between", "under", "over", "near", "into", "through", "across",
                                           "behind", "along", "around", "against", "beside", "below", "above"}

# Words that start a new phrase: a description that is too long is cut before the last of them,
# so it does not end with half a phrase ("...past sunny woods and rocky")
DESCRIPTION_PHRASE_STARTS = {"and", "or", "but", "as", "while", "with", "through", "past", "under", "beneath",
                             "near", "between", "along", "across", "toward", "towards", "into", "onto", "behind",
                             "beside", "by", "from", "in", "on", "at", "of", "over", "around", "against", "below",
                             "above", "among", "amid", "inside", "for", "to"}

# Linking words kept inside a title so it reads as a phrase ("Tracks and White Car", "Man Climbs Rocks in Woodland").
# They are not counted as title words: description may use them, keywords never do
TITLE_CONNECTORS = {"and", "or", "at", "in", "on", "of", "for", "to", "with", "by", "from", "into", "onto",
                    "through", "toward", "towards", "past", "over", "under", "beneath", "beside", "along",
                    "across", "around", "near", "between", "behind", "above", "below", "inside", "among", "amid"}
# A title never ends with these: "Arch Framed by Pine Foliage Ahead" reads as cut off
TITLE_BAD_ENDINGS = {"ahead", "nearby", "beyond", "away", "forward", "back", "again", "together", "outside",
                     "overhead", "there", "here", "now", "today", "too", "also", "very"}


def same_root(a, b):
    """Words of one root: rail/railway, rock/rocky, forest/forested ("Railway Rails" repeats itself)"""
    short, long_word = sorted((a, b), key=len)
    stem = short[:-1] if short.endswith('s') else short
    return short != long_word and len(stem) >= 4 and long_word.startswith(stem)

# macOS spell checker: words it does not know that are two known words glued together ("stationhouse").
# Prints JSON {word: [first, second]}; other unknown words are not reported
GLUED_WORDS_JS = """ObjC.import('AppKit');
function run(argv) {
  const checker = $.NSSpellChecker.sharedSpellChecker;
  // en_US explicitly: plain 'en' also accepts rare spellings of other English variants
  const correct = w => checker.checkSpellingOfStringStartingAtLanguageWrapInSpellDocumentWithTagWordCount(
    w, 0, 'en_US', false, 0, null).location > w.length;
  // Keywords are lower case: proper names (poland, european) are known only capitalized
  const known = w => correct(w) || correct(w[0].toUpperCase() + w.slice(1));
  const glued = {};
  for (const word of argv) {
    if (known(word)) continue;
    let best = null;
    for (let i = 3; i <= word.length - 3; i++) {
      const parts = [word.slice(0, i), word.slice(i)];
      if ((!best || Math.min(i, word.length - i) > Math.min(best[0].length, best[1].length))
          && known(parts[0]) && known(parts[1])) best = parts;
    }
    if (best) glued[word] = best;
  }
  return JSON.stringify(glued);
}"""


# Stock terms written as one word that the spell checker may not know: nightscape, skyscape, hyperlapse.
# They are never split ("night, scape"), and a two-word AI keyword that makes one ("time lapse") is joined
COMPOUND_SUFFIXES = ('scape', 'scapes', 'lapse', 'lapses')


def find_glued_words(words, timeout=15):
    """{word: (first, second)} for words glued from two real words ("railwayline" -> railway, line).
    Stock sites reject such keywords. Empty dict if the macOS spell checker is not available"""
    candidates = sorted({w for w in words if len(w) >= 6 and not w.endswith(COMPOUND_SUFFIXES)})
    if not candidates:
        return {}
    try:
        result = subprocess.run(['osascript', '-l', 'JavaScript', '-e', GLUED_WORDS_JS, *candidates],
                                capture_output=True, text=True, timeout=timeout)
        return {word: tuple(parts) for word, parts in json.loads(result.stdout or '{}').items()}
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
        return {}


# Log of every run is saved here as run_YYYY-MM-DD_HH-MM-SS.log (next to main.py)
LOGS_DIR = Path(__file__).resolve().parent / 'logs'

VIDEO_PROMPT_INTRO = """The {count} images below are frames taken from the beginning, middle and end of ONE stock video clip. Treat them as a single video: describe the subject, action and motion over time, not separate photos.

"""


class ProcessingThread(QThread):
    """Thread for processing images"""
    progress = pyqtSignal(int, int)  # current, total
    log_message = pyqtSignal(str)
    finished = pyqtSignal(list)  # list of data for CSV
    
    def __init__(self, input_folder, output_folder, generate_metadata=True, api_key=None, country='',
                 shot_speed=SHOT_SPEEDS[0], stocks=tuple(STOCKS)):
        super().__init__()
        self.stocks = set(stocks)  # Stock sites to make CSVs for (keys of STOCKS)
        self._photo_rows = []  # Photo CSV rows (Shutterstock): filename, metadata
        self._csv_paths = {}  # CSV name -> file of this run (chosen on first write)
        self.country = country  # Country of shoot for iStock CSV
        self.shot_speed = shot_speed  # Shot speed for iStock CSV (required by ESP)
        self._video_rows = []  # Video CSV rows: filename, metadata, created date, poster timecode
        self.input_folder = input_folder
        self.output_folder = output_folder
        self.generate_metadata = generate_metadata  # Enabled by default
        # API key from UI field, otherwise OPENAI_API_KEY environment variable
        self.api_key = api_key or os.getenv('OPENAI_API_KEY')
        self.previous_titles = set()  # Track previous titles to avoid duplicates
        self.previous_descriptions = set()  # Track previous descriptions
        self._stop_requested = False  # Flag for stopping processing
        self.logs_folder = LOGS_DIR
        self.log_file_path = None
        self._log_lines = []
        # Direct connection: lines are collected in the processing thread, in order, before the file is written
        self.log_message.connect(self._remember_log_line, Qt.ConnectionType.DirectConnection)
    
    def stop(self):
        """Stops processing"""
        self._stop_requested = True
        self.log_message.emit("Stopping processing...")
        
    def _remember_log_line(self, message):
        self._log_lines.append(f"{datetime.now().strftime('%H:%M:%S')} - {message}")
    
    def save_log_file(self):
        """Writes the log of this run to logs/run_YYYY-MM-DD_HH-MM-SS.log"""
        try:
            self.log_file_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_file_path.write_text('\n'.join(self._log_lines) + '\n', encoding='utf-8')
        except Exception as e:
            self.log_message.emit(f"⚠ [Processing] Failed to save log file {self.log_file_path}: {str(e)}")
    
    def run(self):
        started = datetime.now()
        self._log_lines = [f"TagStock PhotoKey {__version__}, model {OPENAI_MODEL}, run started "
                           f"{started.strftime('%Y-%m-%d %H:%M:%S')}"]
        self.log_file_path = Path(self.logs_folder) / f"run_{started.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        try:
            self._run()
        except Exception as e:
            import traceback
            self.log_message.emit(f"  ✗ [Processing] Unexpected error: {str(e)}")
            self.log_message.emit(traceback.format_exc())
        finally:
            self.save_log_file()
            self.finished.emit([])
    
    def _run(self):
        self._stop_requested = False  # Reset stop flag on start
        self.log_message.emit("=" * 80)
        self.log_message.emit("[Processing] Starting processing...")
        self.log_message.emit(f"[Processing] Input folder: {self.input_folder}")
        self.log_message.emit(f"[Processing] Output folder: {self.output_folder}")
        self.log_message.emit(f"[Processing] Generate metadata: {self.generate_metadata}")
        self.log_message.emit(f"[Processing] Using OpenAI API (GPT), model: {OPENAI_MODEL}")
        self.log_message.emit(f"[Processing] CSV for: {', '.join(n for k, n in STOCKS.items() if k in self.stocks) or 'none'}")
        self.log_message.emit(f"[Processing] Log file (written when the run ends): {self.log_file_path}")
        
        # Find JPEG and video files (suffix check is case-insensitive, no duplicates on macOS)
        image_extensions = ['.jpg', '.jpeg']
        
        self.log_message.emit(f"[Processing] Searching for JPEG and video files in folder: {self.input_folder}")
        all_files = sorted(p for p in Path(self.input_folder).iterdir() if p.is_file())
        image_files = [p for p in all_files if p.suffix.lower() in image_extensions + VIDEO_EXTENSIONS]
        video_count = sum(1 for p in image_files if is_video_file(p))
        
        total = len(image_files)
        self.log_message.emit(f"[Processing] Files found: {total - video_count} JPEG, {video_count} video ({', '.join(VIDEO_EXTENSIONS)})")
        
        if total == 0:
            self.log_message.emit("[Processing] ERROR: JPEG or video files not found!")
            return
        
        # Create output folder if it doesn't exist
        self.log_message.emit(f"[Processing] Creating output folder (if not exists): {self.output_folder}")
        Path(self.output_folder).mkdir(parents=True, exist_ok=True)
        self.log_message.emit(f"[Processing] Output folder ready")
        
        processed_count = 0
        rejected_videos = []  # (filename, [reasons]) - videos not meeting Adobe Stock requirements
        self._video_rows = []  # CSV rows of this run; each run writes new CSV files
        self._photo_rows = []
        self._csv_paths = {}
        failed_files = []  # (filename, error) - files skipped because metadata generation failed
        self.log_message.emit(f"[Processing] Starting processing of {total} files...")
        
        for idx, image_path in enumerate(image_files, 1):
            # Check for stop request
            if self._stop_requested:
                self.log_message.emit("[Processing] Processing stopped by user")
                self.log_message.emit(f"[Processing] Processed files: {processed_count} of {total}")
                return
            
            try:
                self.log_message.emit(f"[Processing] Processing {idx}/{total}: {image_path.name}")
                self.log_message.emit(f"[Processing] File size: {image_path.stat().st_size / 1024:.2f} KB")
                
                # Video: check Adobe Stock technical requirements before processing
                video_info = None
                if is_video_file(image_path) and not self.stocks:
                    # Video metadata goes only to CSVs: without a stock site there is nothing to write it to
                    self.log_message.emit(f"[Processing] No stock site selected for CSV, video skipped")
                    self.progress.emit(idx, total)
                    continue
                if is_video_file(image_path):
                    video_info, errors = self.check_video(image_path)
                    if errors:
                        rejected_videos.append((image_path.name, errors))
                        self.progress.emit(idx, total)
                        continue
                
                if video_info:
                    # Video is not modified, so it is not copied: metadata goes to the CSVs only
                    output_file_path = image_path
                else:
                    # Copy file to output folder
                    output_file_path = Path(self.output_folder) / image_path.name
                    self.log_message.emit(f"[Processing] Copying file to: {output_file_path}")
                    shutil.copy2(image_path, output_file_path)
                    self.log_message.emit(f"  ✓ File copied to output folder")
                
                # Check for stop request before analysis
                if self._stop_requested:
                    self.log_message.emit("[Processing] Processing stopped by user")
                    self.log_message.emit(f"[Processing] Processed files: {processed_count} of {total}")
                    return
                
                # Analyze file and generate metadata
                metadata = {}
                if self.generate_metadata:
                    # Small delay between requests to avoid rate limits (0.5 sec)
                    if idx > 1:
                        time.sleep(0.5)
                    
                    self.log_message.emit(f"[Processing] Generating metadata for file {idx}/{total}...")
                    try:
                        metadata = self.analyze_and_generate_metadata(output_file_path, video_info)
                        self.log_message.emit(f"[Processing] Metadata successfully generated for file {idx}/{total}")
                    except FatalAPIError as e:
                        # Wrong key / no quota: stop, other files would fail the same way
                        self.log_message.emit(f"    ❌ [Processing] CRITICAL ERROR: {str(e)}")
                        self.log_message.emit("[Processing] Processing stopped: check API key and balance")
                        self.log_message.emit(f"[Processing] Processed files: {processed_count} of {total}")
                        return
                    except Exception as e:
                        # Error for this file only: skip it and continue with others
                        self.log_message.emit(f"    ❌ [Processing] Failed to generate metadata for {image_path.name}: {str(e)}")
                        failed_files.append((image_path.name, str(e)))
                        if not video_info and output_file_path.exists():
                            output_file_path.unlink()  # do not leave a photo copy without metadata
                        self.progress.emit(idx, total)
                        continue
                    
                    # Check for stop request after analysis
                    if self._stop_requested:
                        self.log_message.emit("[Processing] Processing stopped by user")
                        self.log_message.emit(f"[Processing] Processed files: {processed_count} of {total}")
                        return
                    
                    self.log_message.emit(f"  ✓ Metadata generated:")
                    self.log_message.emit(f"    Title: {metadata.get('title', '')}")
                    self.log_message.emit(f"    Description: {metadata.get('description', '')}")
                    keywords_preview = metadata.get('keywords', '')
                    keywords_count = len(keywords_preview.split(',')) if keywords_preview else 0
                    self.log_message.emit(f"    Keywords: {keywords_preview[:50]}... ({keywords_count} words)")
                    
                    # Write metadata to copied file
                    try:
                        self.log_message.emit(f"[Processing] Writing metadata to file {idx}/{total}...")
                        if video_info:
                            self.write_video_csv_row(output_file_path.name, metadata, video_info)
                        else:
                            self.write_metadata_to_image(output_file_path, metadata)
                            self.add_photo_csv_row(output_file_path.name, metadata)
                        self.log_message.emit(f"  ✓ Metadata written to file")
                    except Exception as e:
                        self.log_message.emit(f"  ⚠ [Processing] Error writing metadata: {str(e)}")
                        import traceback
                        self.log_message.emit(f"  [Processing] Write error details: {traceback.format_exc()}")
                else:
                    self.log_message.emit(f"[Processing] Metadata generation disabled, skipping")
                
                processed_count += 1
                self.progress.emit(idx, total)
                self.log_message.emit(f"[Processing] File {idx}/{total} processed successfully")
                
            except Exception as e:
                self.log_message.emit(f"  ✗ [Processing] Error processing {image_path.name}: {str(e)}")
                import traceback
                self.log_message.emit(f"  [Processing] Error details: {traceback.format_exc()}")
        
        if not self._stop_requested:
            self.recheck_video_csvs()
            if 'istock' in self.stocks:
                self.refine_istock_keywords()
            self.log_message.emit("=" * 80)
            self.log_message.emit("[Processing] Processing completed!")
            self.log_message.emit(f"[Processing] Processed files: {processed_count} of {total}")
            self.log_message.emit(f"[Processing] Results saved to: {self.output_folder}")
            for csv_name in self.csv_names():
                self.log_message.emit(f"[Processing] Metadata CSV: {self.csv_path(csv_name)}")
            if self._video_rows and 'istock' in self.stocks:
                if not self.country:
                    self.log_message.emit("[Processing] ⚠ Country of shoot is empty in the iStock CSV: fill it in Settings or set it in ESP")
            if failed_files:
                self.log_message.emit(f"[Processing] ⚠ Files skipped (metadata generation failed, run again to retry): {len(failed_files)}")
                for name, error in failed_files:
                    self.log_message.emit(f"    ✗ {name}: {error}")
            if rejected_videos:
                self.log_message.emit(f"[Processing] ⚠ Videos skipped (do not meet Adobe Stock requirements): {len(rejected_videos)}")
                for name, reasons in rejected_videos:
                    self.log_message.emit(f"    ✗ {name}: {'; '.join(reasons)}")
            self.log_message.emit(f"[Processing] Log saved to: {self.log_file_path}")
            self.log_message.emit("=" * 80)
        else:
            self.log_message.emit("[Processing] Processing was stopped by user")
    
    def check_video(self, video_path):
        """Checks video against Adobe Stock technical requirements. Returns (video_info, errors)"""
        self.log_message.emit(f"[Video] Checking Adobe Stock technical requirements...")
        try:
            info = probe_video(video_path)
        except FileNotFoundError:
            return None, ["ffprobe not found. Install: brew install ffmpeg"]
        except Exception as e:
            return None, [f"cannot read video: {str(e)}"]
        
        audio = info['audio']
        audio_text = f"{audio['codec']} {audio['sample_rate']} Hz" if audio else "no audio"
        fps_text = f"{info['fps']:.3f}" if info['fps'] else "?"
        duration_text = f"{info['duration']:.2f}s" if info['duration'] is not None else "?"
        self.log_message.emit(f"[Video] {info['width']}x{info['height']}, {fps_text} fps, {info['codec']}, "
                              f"{duration_text}, {info['size_mb']:.1f} MB, {audio_text}")
        
        errors, warnings = check_video_requirements(info, video_path.suffix)
        for warning in warnings:
            self.log_message.emit(f"  ⚠ [Video] {warning}")
        if errors:
            for error in errors:
                self.log_message.emit(f"  ✗ [Video] {error}")
            self.log_message.emit(f"  ✗ [Video] Skipped: does not meet Adobe Stock requirements")
        else:
            self.log_message.emit(f"  ✓ [Video] Meets Adobe Stock technical requirements")
        return info, errors
    
    def clean_text(self, text):
        """Cleans text from special characters, keeps only English letters, numbers and spaces"""
        # Hyphens, dashes and slashes separate words: "forward-facing" -> "forward facing", not "forwardfacing"
        cleaned = re.sub(r'[-\u2010-\u2015/]', ' ', text)
        # Keep only English letters, numbers and spaces
        cleaned = re.sub(r'[^a-zA-Z0-9\s]', '', cleaned)
        # Remove multiple spaces
        cleaned = ' '.join(cleaned.split())
        return cleaned
    
    def clean_caption_from_prompts(self, caption):
        """Removes all prompts from caption"""
        prompts_to_remove = [
            "describe what you see in detail:",
            "what is happening in this image:",
            "describe the image in detail including all objects, colors, and details:",
            "what objects, animals, people, and details are visible in this image:",
            "describe the scene, environment, background, and foreground in detail:",
            "list all visible objects, colors, textures, and details:",
            "describe the main subject and all surrounding elements:",
            "what can you see in this photograph, describe everything in detail:",
            # Old prompts for compatibility
            "describe what you see:",
            "what is happening:",
            "describe the image in detail:",
            "what objects and details are visible:",
            "describe the scene and environment:"
        ]
        cleaned = caption
        for prompt in prompts_to_remove:
            cleaned = cleaned.replace(prompt, "")
        return cleaned.strip()
    
    def extract_words(self, text, max_words=50):
        """Extracts individual words from text (not phrases)"""
        # Clean from special characters
        cleaned = self.clean_text(text)
        # Split into words
        words = cleaned.lower().split()
        # Remove duplicates, preserving order
        unique_words = []
        seen = set()
        for word in words:
            if word not in seen and len(word) > 0:
                unique_words.append(word)
                seen.add(word)
                if len(unique_words) >= max_words:
                    break
        return ', '.join(unique_words)
    
    def analyze_image_with_ai(self, image_path, video_info=None):
        """Analyzes image (or video frames) content through OpenAI GPT API"""
        self.log_message.emit(f"    [AI] Starting image analysis through OpenAI GPT API...")
        
        # API analysis
        api_key = self.api_key or os.getenv('OPENAI_API_KEY')
        if not api_key:
            error_msg = "ERROR: API key not specified. Processing stopped."
            self.log_message.emit(f"    ❌ {error_msg}")
            raise Exception(error_msg)
        
        self.log_message.emit(f"    [AI] API key found, length: {len(api_key)} characters")
        
        # OpenAI API
        self.log_message.emit(f"    [AI] Using OpenAI GPT API")
        if video_info is not None:
            # Video: analyze several frames extracted from the clip
            with tempfile.TemporaryDirectory() as frames_dir:
                frames = extract_frames(image_path, frames_dir, video_info['duration'])
                self.log_message.emit(f"    [AI] Extracted {len(frames)} frames from video")
                if not frames:
                    raise Exception("ERROR: Failed to extract frames from video (check ffmpeg)")
                result = self._analyze_with_openai_api(frames, api_key, is_video=True)
        else:
            result = self._analyze_with_openai_api([image_path], api_key)
        if result is None:
            error_msg = "ERROR: No valid response from AI after 2 attempts (see log above)"
            self.log_message.emit(f"    ❌ {error_msg}")
            raise Exception(error_msg)
        self.log_message.emit(f"    [AI] OpenAI GPT API returned result: title={result.get('title', '')[:30] if result else 'None'}...")
        return result
    
    def _build_messages(self, images_data, is_video=False):
        """Builds OpenAI request messages for one image or several video frames"""
        prompt = ANALYSIS_PROMPT
        if is_video:
            prompt = VIDEO_PROMPT_INTRO.format(count=len(images_data)) + prompt.replace(
                "stock photography", "stock video footage")
        content = [{"type": "text", "text": prompt}]
        for image_data in images_data:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{image_data}"
                }
            })
        return [{"role": "user", "content": content}]
    
    def _analyze_with_openai_api(self, image_paths, api_key, is_video=False):
        """Analysis through OpenAI API"""
        self.log_message.emit(f"    [OpenAI] Starting analysis through OpenAI API...")
        
        if not OPENAI_AVAILABLE:
            error_msg = "ERROR: OpenAI not installed. Install: pip install openai"
            self.log_message.emit(f"    ❌ {error_msg}")
            raise Exception(error_msg)
        
        try:
            self.log_message.emit(f"    [OpenAI] Opening and encoding image...")
            # Open and encode images (one photo or several video frames)
            images_data = []
            for image_path in image_paths:
                with open(image_path, 'rb') as image_file:
                    images_data.append(base64.b64encode(image_file.read()).decode('utf-8'))
            
            self.log_message.emit(f"    [OpenAI] Encoded {len(images_data)} image(s), size: {sum(len(d) for d in images_data)} characters")
            
            # Use standard OpenAI endpoint
            self.log_message.emit(f"    [OpenAI] Using standard OpenAI endpoint")
            client = OpenAI(api_key=api_key)
            messages = self._build_messages(images_data, is_video)
        except Exception as e:
            self.log_message.emit(f"    ❌ Error preparing request: {str(e)}")
            return None
        
        # Two attempts: retry on invalid JSON, rate limit or temporary API error
        for attempt in (1, 2):
            try:
                self.log_message.emit(f"    [OpenAI] Sending request to API (model: {OPENAI_MODEL}, attempt {attempt}/2)...")
                response = client.chat.completions.create(
                    model=OPENAI_MODEL,
                    reasoning_effort="none",  # temperature is rejected when reasoning is on
                    messages=messages,
                    response_format={"type": "json_object"},  # guarantees valid JSON
                    max_completion_tokens=1500,  # Enough for complete JSON with 65 keywords
                    temperature=0.3
                )
            except Exception as e:
                error_str = str(e)
                if is_fatal_api_error(error_str):
                    # Wrong key or no money: other files will fail the same way
                    raise FatalAPIError(f"OpenAI API error: {error_str[:300]}")
                self.log_message.emit(f"    ⚠ [OpenAI] Request error: {error_str[:300]}")
                if attempt == 1:
                    if "rate limit" in error_str.lower() or "429" in error_str:
                        self.log_message.emit(f"    ⚠ Rate limit reached, waiting 60 seconds...")
                        time.sleep(60)
                    continue
                return None
            
            self.log_message.emit(f"    [OpenAI] Response received from API")
            content = (response.choices[0].message.content or '').strip()
            self.log_message.emit(f"    [OpenAI] AI response received, length: {len(content)} characters")
            result = self._parse_ai_response(content)
            if result:
                return result
            if attempt == 1:
                self.log_message.emit(f"    ⚠ [OpenAI] Invalid JSON in AI response, retrying...")
        return None
    
    def _rewrite_description(self, description, forbidden_words):
        """Text-only request: rewrites description without forbidden words. Returns None on failure.
        Up to 3 attempts; each retry is told what was wrong with the previous sentence.
        If no attempt is clean, returns the one with the fewest problems"""
        if not OPENAI_AVAILABLE or not self.api_key:
            return None
        prompt = (f"Rewrite this stock footage description as ONE natural, grammatical English sentence, "
                  f"40-80 characters, no special characters. Keep the meaning. "
                  f"Do NOT use any of these words or their forms: {', '.join(forbidden_words)}.\n\n"
                  f"Sentence: {description}\n\nReturn ONLY the new sentence.")
        best, best_score = None, None
        attempts = 3
        try:
            client = OpenAI(api_key=self.api_key)
            messages = [{"role": "user", "content": prompt}]
            for attempt in range(1, attempts + 1):
                response = client.chat.completions.create(
                    model=OPENAI_MODEL,
                    reasoning_effort="none",
                    messages=messages,
                    max_completion_tokens=100,
                    temperature=0.3 if attempt == 1 else 0.7
                )
                rewritten = self.clean_text(response.choices[0].message.content or '')
                if not rewritten:
                    continue
                used = sorted(set(self.normalize_words(rewritten)) & set(forbidden_words))
                too_long = len(rewritten) > 80
                if not used and not too_long:
                    return rewritten
                # Forbidden words are worse than length: length can be fixed by cutting a phrase off
                score = (len(used), max(0, len(rewritten) - 80))
                if best_score is None or score < best_score:
                    best, best_score = rewritten, score
                problems = []
                fixes = []
                if used:
                    problems.append(f"still uses {used}")
                    fixes.append(f"it uses the forbidden words {', '.join(used)}; use synonyms or leave that detail out")
                if too_long:
                    problems.append(f"is {len(rewritten)} characters")
                    fixes.append(f"it is {len(rewritten)} characters, it must be at most 80")
                self.log_message.emit(f"    [Metadata] Rewritten description {' and '.join(problems)}" +
                                      (", retrying..." if attempt < attempts else ", fixing it in code"))
                messages = messages + [
                    {"role": "assistant", "content": rewritten},
                    {"role": "user", "content": f"Not accepted: {'; '.join(fixes)}. "
                                                f"Write a different sentence. Return ONLY the new sentence."}]
            return best
        except Exception as e:
            self.log_message.emit(f"    ⚠ [Metadata] Description rewrite failed: {str(e)}")
            return best
    
    def _parse_ai_response(self, content):
        """Parses AI response and extracts metadata"""
        self.log_message.emit(f"    [Parser] Starting parsing of AI response, length: {len(content)} characters")
        
        # Strategy 1: Try to parse entire response as JSON
        try:
            metadata = json.loads(content.strip())
            self.log_message.emit(f"    [Parser] Entire response parsed as JSON: title={metadata.get('title', '')[:30] if metadata.get('title') else 'None'}..., keywords_count={len(metadata.get('keywords', '').split(',')) if metadata.get('keywords') else 0}")
            return metadata
        except json.JSONDecodeError:
            pass
        
        # Strategy 2: Search for JSON in markdown code blocks (```json ... ```)
        json_code_block = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
        if json_code_block:
            try:
                self.log_message.emit(f"    [Parser] JSON found in markdown block, parsing...")
                metadata = json.loads(json_code_block.group(1))
                self.log_message.emit(f"    [Parser] JSON parsed successfully: title={metadata.get('title', '')[:30] if metadata.get('title') else 'None'}..., keywords_count={len(metadata.get('keywords', '').split(',')) if metadata.get('keywords') else 0}")
                return metadata
            except json.JSONDecodeError as e:
                self.log_message.emit(f"    [Parser] Error parsing JSON from markdown block: {str(e)}")
        
        # Strategy 3: Search for JSON object using more reliable regex
        # Search for opening brace and try to find corresponding closing brace
        start_pos = content.find('{')
        if start_pos != -1:
            # Start with opening brace and search for corresponding closing brace
            brace_count = 0
            end_pos = start_pos
            in_string = False
            escape_next = False
            
            for i in range(start_pos, len(content)):
                char = content[i]
                
                if escape_next:
                    escape_next = False
                    continue
                
                if char == '\\':
                    escape_next = True
                    continue
                
                if char == '"' and not escape_next:
                    in_string = not in_string
                    continue
                
                if not in_string:
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            end_pos = i + 1
                            break
            
            # If closing brace not found, take all remaining text
            if brace_count > 0:
                end_pos = len(content)
            
            # If full JSON found (brace_count == 0) or truncated (brace_count > 0)
            if end_pos > start_pos:
                json_str = content[start_pos:end_pos]
                try:
                    self.log_message.emit(f"    [Parser] JSON found in response (position {start_pos}-{end_pos}), parsing...")
                    metadata = json.loads(json_str)
                    self.log_message.emit(f"    [Parser] JSON parsed successfully: title={metadata.get('title', '')[:30] if metadata.get('title') else 'None'}..., keywords_count={len(metadata.get('keywords', '').split(',')) if metadata.get('keywords') else 0}")
                    return metadata
                except json.JSONDecodeError as e:
                    self.log_message.emit(f"    [Parser] Error parsing JSON: {str(e)}")
                    # If JSON is truncated (missing closing braces) or cannot be parsed
                    # Try to restore it
                    if brace_count > 0 or '"keywords"' in json_str:
                        self.log_message.emit(f"    [Parser] JSON truncated (missing {brace_count} closing braces), trying to restore...")
                        # Trying to restore truncated JSON
                        json_str_fixed = json_str
                        
                        # If keywords are truncated, truncate them to last complete comma
                        if '"keywords"' in json_str_fixed:
                            keywords_start = json_str_fixed.find('"keywords"')
                            if keywords_start != -1:
                                # Find start of keywords value (after ": ")
                                colon_pos = json_str_fixed.find(':', keywords_start)
                                if colon_pos != -1:
                                    # Skip spaces after colon
                                    value_start = colon_pos + 1
                                    while value_start < len(json_str_fixed) and json_str_fixed[value_start] in ' \n\t':
                                        value_start += 1
                                    
                                    # If value starts with quote
                                    if value_start < len(json_str_fixed) and json_str_fixed[value_start] == '"':
                                        value_start += 1  # Skip opening quote
                                        
                                        # Find last comma in keywords (but not at the end)
                                        keywords_value = json_str_fixed[value_start:]
                                        # Search for last comma followed by at least one character
                                        last_comma = -1
                                        for i in range(len(keywords_value) - 1, -1, -1):
                                            if keywords_value[i] == ',':
                                                # Check that there is at least one character after comma
                                                if i + 1 < len(keywords_value) and keywords_value[i+1] not in ' \n\t"':
                                                    last_comma = i
                                                    break
                                        
                                        if last_comma > 0:
                                            # Truncate keywords to last comma and close quote
                                            json_str_fixed = json_str_fixed[:value_start + last_comma] + '"'
                                            self.log_message.emit(f"    [Parser] Keywords truncated to last complete comma")
                        
                        # Close JSON properly
                        json_str_fixed += '}'
                        
                        try:
                            metadata = json.loads(json_str_fixed)
                            self.log_message.emit(f"    [Parser] JSON restored and parsed: title={metadata.get('title', '')[:30] if metadata.get('title') else 'None'}..., keywords_count={len(metadata.get('keywords', '').split(',')) if metadata.get('keywords') else 0}")
                            return metadata
                        except json.JSONDecodeError as e2:
                            self.log_message.emit(f"    [Parser] Failed to restore JSON: {str(e2)}")
                            self.log_message.emit(f"    [Parser] Restoration attempt (first 300 characters): {json_str_fixed[:300]}")
                    
                    self.log_message.emit(f"    [Parser] JSON content (first 500 characters): {json_str[:500]}")
        
        # Strategy 4: Try to find JSON using greedy search (old method, but with improvements)
        json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content, re.DOTALL)
        if json_match:
            try:
                self.log_message.emit(f"    [Parser] JSON found by greedy search, parsing...")
                metadata = json.loads(json_match.group())
                self.log_message.emit(f"    [Parser] JSON parsed successfully: title={metadata.get('title', '')[:30] if metadata.get('title') else 'None'}..., keywords_count={len(metadata.get('keywords', '').split(',')) if metadata.get('keywords') else 0}")
                return metadata
            except json.JSONDecodeError as e:
                self.log_message.emit(f"    [Parser] Error parsing JSON (greedy search): {str(e)}")
        
        # If nothing helped
        self.log_message.emit(f"    ❌ Failed to find or parse JSON in response")
        self.log_message.emit(f"    [Parser] First 500 characters of response: {content[:500]}")
        return None
    
    def normalize_words(self, text):
        """Normalizes text and extracts word list (lowercase, without special characters)"""
        if not text:
            return []
        # Extract words: only letters and numbers
        words = re.findall(r'[a-z0-9]+', text.lower())
        return words
    
    def build_title(self, title_raw, padding_words, required_words=()):
        """Builds title from AI title: unique content words in AI order, linking words (and, in, through...)
        kept between them so the title reads as a phrase. Articles and other stop words are dropped.
        Padded with padding_words up to 6 words, limited to 10 words, required_words added at the end.
        A word of the same root as an earlier one is dropped, the title never ends with TITLE_BAD_ENDINGS.
        Returns (title, content_words)"""
        tokens = []  # (word, is_connector)
        seen = set(required_words)
        for word in self.normalize_words(title_raw):
            if word in TITLE_CONNECTORS:
                if tokens and not tokens[-1][1]:  # only right after a content word
                    tokens.append((word, True))
            elif (len(word) >= 3 and word not in STOP_WORDS and word not in seen and
                  not any(same_root(word, other) for other in seen)):
                tokens.append((word, False))
                seen.add(word)
        
        def strip_trailing_connectors():
            while tokens and (tokens[-1][1] or tokens[-1][0] in TITLE_BAD_ENDINGS):
                tokens.pop()
        
        strip_trailing_connectors()
        # Short title: supplement with AI keywords (not with description words, description keeps them)
        for word in padding_words:
            if len(tokens) >= 6:
                break
            plural_duplicate = (word + 's' in seen or word + 'es' in seen or
                                (word.endswith('s') and word[:-1] in seen) or
                                (word.endswith('es') and word[:-2] in seen))
            if (word not in seen and not plural_duplicate and len(word) >= 3 and word not in STOP_WORDS and
                    word not in TITLE_CONNECTORS and word not in TITLE_BAD_ENDINGS and
                    not any(same_root(word, other) for other in seen)):
                tokens.append((word, False))
                seen.add(word)
        del tokens[10 - len(required_words):]
        strip_trailing_connectors()
        tokens.extend((word, False) for word in required_words)
        # Title Case; short linking words (and, in, of...) stay lowercase
        title = ' '.join(w if is_connector and len(w) <= 3 else w.title() for w, is_connector in tokens)
        return title, [w for w, is_connector in tokens if not is_connector]
    
    def truncate_description(self, description, max_length=80):
        """Truncates description to max_length characters without ellipsis.
        Cuts before the last phrase that does not fit ("...sunny woods and rocky" -> "...sunny woods"),
        otherwise at a word boundary"""
        if len(description) <= max_length:
            return description
        
        # Truncate to max_length and find last space
        truncated = description[:max_length]
        last_space = truncated.rfind(' ')
        
        # If space found, truncate at it, otherwise truncate hard
        if last_space <= 15:  # Minimum 15 characters must remain
            return truncated
        words = truncated[:last_space].split()
        # Drop the phrase that was cut: back to the last phrase-starting word, keeping at least 6 words
        for i in range(len(words) - 1, 5, -1):
            if words[i].lower() in DESCRIPTION_PHRASE_STARTS:
                return ' '.join(words[:i])
        return ' '.join(words)
    
    @staticmethod
    def _strip_dangling(words):
        """Removes articles/prepositions left at the end and (except a/an/the) at the start"""
        words = list(words)
        while words and words[-1].lower() in DESCRIPTION_TRAILING_WORDS:
            words.pop()
        while words and words[0].lower() in DESCRIPTION_TRAILING_WORDS - {"a", "an", "the"}:
            words.pop(0)
        return words
    
    def build_description(self, description_raw, title_words_set):
        """Keeps AI sentence without title words and generic words. Returns None if result is unusable.
        A forbidden word is not cut out of the middle of the sentence (that breaks grammar):
        the sentence is cut before it, if at least 6 words remain"""
        def forbidden(word):
            return word.lower() in title_words_set or word.lower() in DESCRIPTION_BANNED_WORDS
        
        all_words = description_raw.split()
        first_bad = next((i for i, w in enumerate(all_words) if forbidden(w)), None)
        words = all_words
        if first_bad is not None:
            words = self._strip_dangling(all_words[:first_bad])
            if len(words) < 6:
                # Last resort: remove forbidden words from the sentence
                words = [w for w in all_words if not forbidden(w)]
        description = self.truncate_description(' '.join(words), max_length=80)
        words = self._strip_dangling(description.split())
        if len(words) < 6:
            return None
        description = ' '.join(words)
        description = description[0].upper() + description[1:]
        if len(description) < 15:
            return None
        return description
    
    def have_common_words(self, text1, text2):
        """Checks for common words between two texts"""
        words1 = set(self.normalize_words(text1))
        words2 = set(self.normalize_words(text2))
        return bool(words1 & words2)
    
    def analyze_and_generate_metadata(self, image_path, video_info=None):
        """Analyzes image (or video) file and generates metadata based on content analysis"""
        self.log_message.emit(f"    [Metadata] Starting metadata generation for {'video' if video_info else 'image'}...")
        
        # Adobe Stock: vertical clips must have "vertical" and "video" in title and keywords
        required_words = []
        if video_info and get_orientation(video_info['width'], video_info['height']) == 'vertical':
            required_words = ['vertical', 'video']
        category = 'Video' if video_info else 'Photography'
        
        # Пытаемся проаналfromировать через AI
        ai_metadata = self.analyze_image_with_ai(image_path, video_info)
        
        if ai_metadata:
            self.log_message.emit(f"    [Metadata] AI metadata received, starting processing...")
            title_raw = self.clean_text(ai_metadata.get('title', ''))
            description_raw = self.clean_text(ai_metadata.get('description', ''))
            keywords_str_raw = ai_metadata.get('keywords', '')
            
            self.log_message.emit(f"    [Metadata] Received from AI: title='{title_raw[:30]}...', description='{description_raw[:30]}...', keywords_count={len(keywords_str_raw.split(',')) if keywords_str_raw else 0}")
            
            # All content words from AI analysis (reserve source for title/keywords)
            all_text = f"{title_raw} {description_raw} {keywords_str_raw}".lower()
            all_words_filtered = list(dict.fromkeys(
                w for w in self.normalize_words(all_text)
                if len(w) >= 3 and w not in STOP_WORDS and w not in TITLE_CONNECTORS))
            
            # STEP 1: TITLE (6-10 words: unique content words + linking words between them)
            desc_raw_words = set(self.normalize_words(description_raw))
            ai_keywords = [w for kw in keywords_str_raw.split(',') for w in self.normalize_words(kw)[:1]]
            title, title_words = self.build_title(title_raw, [w for w in ai_keywords + all_words_filtered
                                                              if w not in desc_raw_words], required_words)
            title_words_set = set(title_words)
            title_length = len(title.split())
            if title_length <= 5:
                self.log_message.emit(f"    ⚠ WARNING: Title contains only {title_length} words (requires more than 5)")
            self.log_message.emit(f"    [Metadata] TITLE: '{title}' ({title_length} words)")
            
            # STEP 2: DESCRIPTION - AI sentence without title words and generic words
            forbidden = sorted(set(w.lower() for w in description_raw.split())
                               & (title_words_set | DESCRIPTION_BANNED_WORDS))
            too_long = len(description_raw) > 80
            if forbidden or too_long:
                # Ask the model to rewrite the sentence instead of cutting words out of it or truncating it
                reasons = []
                if forbidden:
                    reasons.append(f"uses title/generic words {forbidden}")
                if too_long:
                    reasons.append(f"is {len(description_raw)} characters (max 80)")
                self.log_message.emit(f"    [Metadata] Description {' and '.join(reasons)}, asking AI to rewrite...")
                rewritten = self._rewrite_description(description_raw, sorted(title_words_set | DESCRIPTION_BANNED_WORDS))
                if rewritten:
                    self.log_message.emit(f"    [Metadata] Rewritten description: '{rewritten}'")
                    description_raw = rewritten
            description = self.build_description(description_raw, title_words_set)
            if description is None:
                # Fallback: word list from AI analysis, without title words
                self.log_message.emit(f"    ⚠ WARNING: AI description unusable after removing title words, building from analysis words")
                fallback_words = [w for w in all_words_filtered if w not in title_words_set][:12]
                description = self.truncate_description(' '.join(fallback_words).capitalize(), max_length=80)
            desc_words_set = set(self.normalize_words(description))
            self.log_message.emit(f"    [Metadata] DESCRIPTION: '{description}' ({len(description)} characters)")
            
            # STEP 3: KEYWORDS - real words from AI only: not in title, not in description,
            # no singular/plural duplicates, up to MAX_KEYWORDS
            keywords_list = list(required_words)
            keywords_seen = set(keywords_list)
            ai_single_words = [w for kw in keywords_str_raw.split(',') for w in self.normalize_words(kw)
                               if len(self.normalize_words(kw)) == 1]
            glued = find_glued_words(ai_single_words + all_words_filtered)
            for word, parts in glued.items():
                self.log_message.emit(f"    [Metadata] Glued keyword '{word}' split into: {', '.join(parts)}")
            
            def add_keyword(word):
                if word in glued:
                    # Stock sites reject glued words: its parts are used instead
                    for part in glued[word]:
                        add_keyword(part)
                    return
                if (len(word) < 3 or word in STOP_WORDS or word in TITLE_CONNECTORS or word in keywords_seen or
                        word in title_words_set or word in desc_words_set):
                    return
                # Skip singular/plural duplicate of an existing keyword
                if (word + 's' in keywords_seen or word + 'es' in keywords_seen or
                        (word.endswith('s') and word[:-1] in keywords_seen) or
                        (word.endswith('es') and word[:-2] in keywords_seen)):
                    return
                keywords_list.append(word)
                keywords_seen.add(word)
            
            phrase_parts = set()  # "time lapse" became "timelapse": its parts are not added on their own
            for kw in keywords_str_raw.split(','):
                words = self.normalize_words(kw)
                if len(words) == 1:
                    add_keyword(words[0])
                elif len(words) == 2 and ''.join(words).endswith(COMPOUND_SUFFIXES):
                    add_keyword(''.join(words))
                    phrase_parts.update(words)
            from_ai = len(keywords_list)
            # Supplement from other AI words (title/description words are excluded by rules)
            for word in all_words_filtered:
                if word not in phrase_parts:
                    add_keyword(word)
            keywords_list = keywords_list[:MAX_KEYWORDS]
            
            self.log_message.emit(f"    [Metadata] KEYWORDS: {len(keywords_list)} (from AI keywords: {from_ai})")
            if len(keywords_list) < MIN_KEYWORDS:
                self.log_message.emit(f"    ⚠ WARNING: Only {len(keywords_list)} keywords (recommended {MIN_KEYWORDS}-{MAX_KEYWORDS})")
            keywords = ', '.join(keywords_list)
            
            # STEP 4: Final validation
            common_title_desc = title_words_set & desc_words_set
            if common_title_desc:
                raise Exception(f"❌ CRITICAL ERROR: Title and Description contain common words: {common_title_desc}")
            common_desc_kw = desc_words_set & set(keywords_list)
            if common_desc_kw:
                raise Exception(f"❌ CRITICAL ERROR: Description and Keywords contain common words: {common_desc_kw}")
            if len(description) < 15 or len(description) > 80:
                raise Exception(f"❌ CRITICAL ERROR: Description contains {len(description)} characters (requires 15-80)")
            
            self.log_message.emit(f"    ✓ AI analysis completed: title={title_length} words, description={len(description)} characters, keywords={len(keywords_list)} words")

            return {
                'title': title,
                'description': description,
                'keywords': keywords,
                'category': category,
                'shutterstock_categories': self.pick_shutterstock_categories(ai_metadata.get('categories'), keywords),
                'model_release': 'No'
            }
        
        # Fallback: use basic generation based on filename
        self.log_message.emit(f"    [Metadata] Use basic generation based on filename")
        stem = image_path.stem
        cleaned_stem = self.clean_text(stem)
        
        title = cleaned_stem.replace('_', ' ').replace('-', ' ').title()
        title = ' '.join(title.split())
        
        keywords = self.extract_words(cleaned_stem, max_words=MAX_KEYWORDS)
        
        keyword_set = set(keywords.lower().replace(', ', ',').split(','))
        description_words = [w for w in cleaned_stem.lower().split() if w not in keyword_set]
        
        if not description_words:
            # Используем wordsа from title
            title_words = cleaned_stem.split()
            if title_words and len(title_words) >= 6:
                description = ' '.join(title_words[:10]).capitalize()
            elif title_words:
                # Дополняем до 6 words, повторяя wordsа from title
                extended_words = title_words * 2  # Дублируем для получения больше words
                description = ' '.join(extended_words[:10]).capitalize()
            else:
                # Критическая ошибка - нет данных для description
                error_msg = "❌ CRITICAL ERROR: Не удалось сформировать description from имени файла"
                self.log_message.emit(f"    {error_msg}")
                raise Exception(error_msg)
        else:
            description = ' '.join(description_words[:10])
            description = description.capitalize()
            
        # Limit to 80 characters, truncating at word boundarysа (without ellipsis)
        description = self.truncate_description(description, max_length=80)

        return {
            'title': title,
            'description': description,
            'keywords': keywords,
            'category': category,
            'shutterstock_categories': self.pick_shutterstock_categories(None, keywords),
            'model_release': 'No'
        }

    def pick_shutterstock_categories(self, ai_categories, keywords):
        """Shutterstock categories for the CSV (see shutterstock_categories), logged"""
        categories = shutterstock_categories(ai_categories, keywords)
        if categories:
            self.log_message.emit(f"    [Metadata] SHUTTERSTOCK CATEGORIES: {', '.join(categories)}")
        else:
            self.log_message.emit(f"    ⚠ WARNING: No Shutterstock category (AI gave {ai_categories!r}): choose Category 1 on the site")
        return categories
    
    def write_metadata_to_image(self, image_path, metadata):
        """Writes metadata (title, description, keywords) в файл fromображения"""
        self.log_message.emit(f"    [Write] Starting metadata write to file...")
        
        try:
            # Подготовка метаданных для EXIF/IPTC
            title = metadata.get('title', '')
            description = metadata.get('description', '')
            keywords_str = metadata.get('keywords', '')
            
            self.log_message.emit(f"    [Write] Metadata: title='{title[:30]}...', description='{description[:30]}...', keywords_count={len(keywords_str.split(',')) if keywords_str else 0}")
            
            # Split keywords into list
            keywords_list = [kw.strip() for kw in keywords_str.split(',') if kw.strip()]
            
            # For JPEG use piexif to write EXIF/IPTC
            if image_path.suffix.lower() in ['.jpg', '.jpeg']:
                self.log_message.emit(f"    [Write] JPEG format, use piexif to write EXIF/IPTC...")
                
                # Load existing EXIF or create new
                try:
                    exif_dict = piexif.load(str(image_path))
                    self.log_message.emit(f"    [Write] Existing EXIF loaded")
                except:
                    exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
                    self.log_message.emit(f"    [Write] Создан новый EXIF wordsарь")
                
                # Write Title to ImageDescription (EXIF) - read by most programs
                if title:
                    exif_dict["0th"][piexif.ImageIFD.ImageDescription] = title.encode('utf-8')
                    # Windows XP Title (read by Windows and many programs)
                    try:
                        exif_dict["0th"][piexif.ImageIFD.XPTitle] = title.encode('utf-16le') + b'\x00\x00'
                    except:
                        pass
                    self.log_message.emit(f"    [Write] Title written to EXIF")
                
                # Write Description to UserComment (EXIF)
                if description:
                    # UserComment in format: encoding + text (read by many programs)
                    user_comment = b'unicode\x00\x00' + description.encode('utf-16le') + b'\x00\x00'
                    exif_dict["Exif"][piexif.ExifIFD.UserComment] = user_comment
                    # Windows XP Comment
                    try:
                        exif_dict["0th"][piexif.ImageIFD.XPComment] = description.encode('utf-16le') + b'\x00\x00'
                    except:
                        pass
                    self.log_message.emit(f"    [Write] Description written to EXIF")
                
                # Write Keywords (EXIF)
                if keywords_list:
                    # Windows XP Keywords (read by many programs)
                    try:
                        keywords_utf16 = b'\x00\x00'.join([kw.encode('utf-16le') for kw in keywords_list]) + b'\x00\x00'
                        exif_dict["0th"][piexif.ImageIFD.XPKeywords] = keywords_utf16
                        self.log_message.emit(f"    [Write] Keywords written to EXIF ({len(keywords_list)} words)")
                    except:
                        pass
                
                # Открываем fromображение для сохранения
                img = Image.open(image_path)
                
                # Save EXIF metadata
                exif_bytes = piexif.dump(exif_dict)
                img.save(str(image_path), "JPEG", exif=exif_bytes, quality=95, optimize=False)
                img.close()
                
                self.log_message.emit(f"    [Write] EXIF metadata saved to file")
                
                # Try to write IPTC metadata through exiftool (if available)
                # This is needed for macOS Photos and other programs, which read IPTC
                try:
                    self.write_iptc_with_exiftool(image_path, title, description, keywords_list)
                    self.log_message.emit(f"    [Write] IPTC metadata written through exiftool")
                except FileNotFoundError:
                    # If exiftool unavailable, continue with EXIF data
                    self.log_message.emit(f"    [Write] Note: exiftool not found. Install: brew install exiftool")
                    self.log_message.emit(f"    [Write] Use only EXIF metadata (some programs may not see)")
                except Exception as e:
                    # Другие ошибки игнорируем
                    self.log_message.emit(f"    [Write] Error writing IPTC through exiftool: {str(e)}")
                
                # Log what was written
                self.log_message.emit(f"    [Write] Written: Title='{title[:30]}...', Description='{description[:30]}...', Keywords={len(keywords_list)} words")
                self.log_message.emit(f"    [Write] Check file through: python check_metadata.py '{image_path}'")
                
            else:
                # For other formats use Pillow info
                self.log_message.emit(f"    [Write] Format {image_path.suffix}, use Pillow info...")
                img = Image.open(image_path)
                info = img.info.copy()
                
                if title:
                    info['title'] = title
                if description:
                    info['description'] = description
                if keywords_list:
                    info['keywords'] = keywords_list
                
                img.save(str(image_path), **info)
                img.close()
                self.log_message.emit(f"    [Write] Metadata сохранены через Pillow info")
            
        except Exception as e:
            raise Exception(f"Error writing metadata: {str(e)}")
    
    def write_video_csv_row(self, filename, metadata, video_info=None):
        """Adds a clip to the video CSVs of the selected stock sites: Adobe Stock (Filename, Title, Keywords,
        Category, Releases), iStock (ESP template columns), Shutterstock (Filename, Description, Keywords...)"""
        created = shoot_date_from_filename(filename)
        if not created and 'istock' in self.stocks:
            self.log_message.emit(f"    ⚠ [Write] No shooting date in file name {filename}: set Date Created in ESP")
        self._video_rows.append({'filename': filename, 'metadata': metadata, 'created': created,
                                 'poster': poster_timecode(video_info)})
        self.write_csvs()
        added = ', '.join(self.csv_label(name) for name in self.csv_names(photos=False))
        self.log_message.emit(f"    [Write] Video metadata added to {added} (video file is not copied)")
    
    def add_photo_csv_row(self, filename, metadata):
        """Adds a photo to the Shutterstock photo CSV (photo metadata is also written into the file itself)"""
        if 'shutterstock' not in self.stocks:
            return
        self._photo_rows.append({'filename': filename, 'metadata': metadata})
        self.write_csvs()
        self.log_message.emit(f"    [Write] Photo metadata added to {self.csv_label(SHUTTERSTOCK_PHOTO_CSV_NAME)}")
    
    def csv_names(self, photos=True, videos=True):
        """CSV files of this run: only selected stock sites, only kinds of files that were processed"""
        names = []
        if videos and self._video_rows:
            names += [name for key, name in (('adobe', VIDEO_CSV_NAME), ('istock', ISTOCK_VIDEO_CSV_NAME),
                                             ('shutterstock', SHUTTERSTOCK_VIDEO_CSV_NAME)) if key in self.stocks]
        if photos and self._photo_rows and 'shutterstock' in self.stocks:
            names.append(SHUTTERSTOCK_PHOTO_CSV_NAME)
        return names
    
    def csv_path(self, csv_name):
        """File of this CSV in the folder of its stock site (output/Shutterstock/...). CSVs of earlier runs
        are kept: this run gets the next free name (shutterstock_videos_2.csv...) and keeps it till the end"""
        if csv_name not in self._csv_paths:
            folder = Path(self.output_folder) / STOCKS[CSV_STOCKS[csv_name]]
            folder.mkdir(parents=True, exist_ok=True)
            self._csv_paths[csv_name] = free_csv_path(folder, csv_name)
        return self._csv_paths[csv_name]
    
    def csv_label(self, csv_name):
        """Stock folder and file name for the log: Shutterstock/shutterstock_videos_2.csv"""
        path = self.csv_path(csv_name)
        return f"{path.parent.name}/{path.name}"
    
    def write_csvs(self):
        """Writes all rows of this run to the CSVs of the selected stock sites"""
        adobe = [['Filename', 'Title', 'Keywords', 'Category', 'Releases']]
        istock = [ISTOCK_VIDEO_CSV_FIELDS]
        shutterstock_videos = [SHUTTERSTOCK_CSV_FIELDS]
        for row in self._video_rows:
            meta = row['metadata']
            adobe.append([row['filename'], meta.get('title', ''), meta.get('keywords', ''), '', ''])
            # Template columns we have no data for stay empty: set them in ESP
            values = {'file name': row['filename'], 'description': meta.get('description', ''),
                      'title': meta.get('title', ''), 'country': self.country, 'poster timecode': row['poster'],
                      'keywords': meta.get('istock_keywords') or meta.get('keywords', ''),
                      'shot speed': self.shot_speed, 'date created': row['created']}
            istock.append([values.get(name, '') for name in ISTOCK_VIDEO_CSV_FIELDS])
            shutterstock_videos.append(shutterstock_row(row['filename'], meta))
        shutterstock_photos = [SHUTTERSTOCK_CSV_FIELDS] + [shutterstock_row(row['filename'], row['metadata'])
                                                            for row in self._photo_rows]
        # iStock and Shutterstock: line endings as in their templates (\n)
        tables = {VIDEO_CSV_NAME: (adobe, '\r\n'), ISTOCK_VIDEO_CSV_NAME: (istock, '\n'),
                  SHUTTERSTOCK_VIDEO_CSV_NAME: (shutterstock_videos, '\n'),
                  SHUTTERSTOCK_PHOTO_CSV_NAME: (shutterstock_photos, '\n')}
        for csv_name in self.csv_names():
            rows, line_end = tables[csv_name]
            with open(self.csv_path(csv_name), 'w', newline='', encoding='utf-8') as f:
                csv.writer(f, lineterminator=line_end).writerows(rows)
    
    def recheck_video_csvs(self):
        """Re-checks video CSVs after all clips are described. Stock sites treat equal titles as duplicates,
        so a title already used by another clip is replaced (the first clip keeps it)"""
        if not self._video_rows:
            return
        self.log_message.emit(f"[Recheck] Checking {len(self._video_rows)} video rows for repeated titles...")
        taken = {row['metadata'].get('title', '').lower() for row in self._video_rows}
        seen = set()
        fixed, left = 0, []
        for row in self._video_rows:
            meta = row['metadata']
            title = meta.get('title', '')
            if title.lower() in seen:
                result = self._make_title_unique(meta, taken)
                if result:
                    meta['title'], meta['keywords'] = result
                    taken.add(meta['title'].lower())
                    fixed += 1
                    self.log_message.emit(f"    [Recheck] {row['filename']}: '{title}' -> '{meta['title']}'")
                else:
                    left.append(row['filename'])
                    self.log_message.emit(f"    ⚠ [Recheck] {row['filename']}: could not replace repeated title '{title}'")
            seen.add(meta.get('title', '').lower())
        if fixed:
            self.write_csvs()
        self.log_message.emit(f"[Recheck] Repeated titles replaced: {fixed}" +
                              (f", left as is: {len(left)}" if left else "") + ("" if fixed or left else " (none found)"))
    
    def refine_istock_keywords(self):
        """iStock accepts only terms of the Getty vocabulary: each keyword is replaced by the term it means
        in this clip ("sleepers" -> "Railroad Tie", "train" -> "Train - Vehicle"), relevant ESP title
        suggestions are added. Candidate terms come from ESP (open ESP tab in Chrome) and are saved
        to istock_vocabulary.json. Adobe Stock CSV keeps the plain keywords"""
        if not self._video_rows:
            return
        self.log_message.emit(f"[iStock] Matching keywords to the Getty vocabulary...")
        vocabulary = IstockVocabulary()
        all_words = [k.strip() for row in self._video_rows
                     for k in row['metadata'].get('keywords', '').split(',') if k.strip()]
        missing = vocabulary.missing(all_words)
        suggestions = {}
        try:
            if open_esp_tab():
                self.log_message.emit(f"    [iStock] No ESP tab was open: opened {ESP_URL_PREFIX} in Chrome")
            if missing:
                self.log_message.emit(f"    [iStock] Looking up {len(missing)} new words in ESP...")
                vocabulary.update(esp_lookup_words(missing))
                vocabulary.save()
            suggestions = esp_title_suggestions([row['metadata'].get('title', '') for row in self._video_rows])
        except EspUnavailable as e:
            self.log_message.emit(f"    ⚠ [iStock] ESP is not available ({e}). Sign in to esp.gettyimages.com in Chrome "
                                  f"and allow View > Developer > Allow JavaScript from Apple Events. "
                                  f"Words not in the saved vocabulary stay as is, no title suggestions")
        unknown_total = set()
        for row in self._video_rows:
            meta = row['metadata']
            keywords = [k.strip() for k in meta.get('keywords', '').split(',') if k.strip()]
            candidates = {w: vocabulary.candidates(w) for w in keywords if vocabulary.candidates(w)}
            unknown = [w for w in keywords if w not in vocabulary.words]
            unknown_total.update(unknown)
            title_suggestions = suggestions.get(meta.get('title', ''), [])
            chosen = self._choose_istock_terms(meta, candidates, title_suggestions)
            if chosen is None:
                # No AI: only words with a single meaning are replaced
                chosen = ({w: c[0] for w, c in candidates.items() if len(c) == 1}, [])
            choices, suggested = chosen
            terms = finalize_terms(keywords, choices, suggested, candidates, title_suggestions, unknown)
            meta['istock_keywords'] = ', '.join(terms)
            dropped = [w for w in keywords if w not in unknown and not (choices.get(w) and w in candidates)]
            self.log_message.emit(f"    [iStock] {row['filename']}: {len(terms)} terms"
                                  + (f", dropped (no fitting meaning): {', '.join(dropped)}" if dropped else ""))
        self.write_csvs()
        if unknown_total:
            self.log_message.emit(f"[iStock] ⚠ {len(unknown_total)} words were not checked in ESP and stay as is")
        self.log_message.emit(f"[iStock] Keywords matched to the Getty vocabulary")
    
    def _choose_istock_terms(self, metadata, candidates, suggestions):
        """AI picks the meaning of each keyword in this clip and relevant suggestion terms.
        Returns ({word: term or None}, [suggested terms]) or None without AI"""
        if not OPENAI_AVAILABLE or not self.api_key:
            return None
        prompt = (
            "You map stock footage keywords to the Getty Images / iStock controlled vocabulary.\n"
            f"Clip title: {metadata.get('title', '')}\n"
            f"Clip description: {metadata.get('description', '')}\n\n"
            "The keywords were already chosen from the clip frames: what they name IS in the clip, even if "
            "the title and description do not mention it. Do not judge whether a keyword belongs to the clip, "
            "only pick its meaning. For each keyword choose the ONE candidate term with the meaning the "
            "keyword has in this clip (e.g. 'train' in a railway clip is 'Train - Vehicle', not "
            "'Sports Training'; 'sleepers' is 'Railroad Tie'; 'forest' is 'Forest'; 'green' is "
            "'Green Color'). Use null ONLY when every candidate has a different meaning "
            "(e.g. 'Summer Olympic Games' for 'summer' would be wrong, but 'Summer' is right).\n"
            f"Keywords and candidates: {json.dumps(candidates, ensure_ascii=False)}\n\n"
            "Then pick from the suggested terms only those that clearly describe this clip "
            "(no wrong places, no things that are not in the clip).\n"
            f"Suggested terms: {json.dumps(suggestions, ensure_ascii=False)}\n\n"
            'Return JSON: {"choices": {"keyword": "Term or null"}, "suggested": ["Term"]}. '
            "Use the terms exactly as given.")
        try:
            client = OpenAI(api_key=self.api_key)
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                reasoning_effort="none",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                max_completion_tokens=3000,
                temperature=0.2
            )
            data = json.loads(response.choices[0].message.content or '{}')
            return data.get('choices') or {}, data.get('suggested') or []
        except Exception as e:
            self.log_message.emit(f"    ⚠ [iStock] Term choice failed: {str(e)}")
            return None
    
    def _make_title_unique(self, metadata, taken_titles):
        """New title not in taken_titles (lowercase) and without description words.
        Keywords are rebuilt for it (see retitle). Returns (title, keywords) or None"""
        desc_words = set(self.normalize_words(metadata.get('description', '')))
        for title_raw in self._title_candidates(metadata, taken_titles, sorted(desc_words | DESCRIPTION_BANNED_WORDS)):
            result = self.retitle(metadata, title_raw)
            if result and result[0].lower() not in taken_titles:
                return result
        return None
    
    def retitle(self, metadata, title_raw):
        """Builds a new title for described clip and rebuilds its keywords: new title words leave them,
        dropped title words come back. Returns (title, keywords) or None if the title uses description words
        or is shorter than 6 words"""
        old_title = metadata.get('title', '')
        desc_words = set(self.normalize_words(metadata.get('description', '')))
        keywords = [k.strip() for k in metadata.get('keywords', '').split(',') if k.strip()]
        required = ['vertical', 'video'] if old_title.lower().endswith('vertical video') else []
        title, title_words = self.build_title(title_raw, [k for k in keywords if k not in desc_words], required)
        if set(title_words) & desc_words or len(title_words) < 6:
            return None
        new_keywords = [k for k in keywords if k in required or k not in title_words]
        for word in self.normalize_words(old_title):
            if len(new_keywords) >= MAX_KEYWORDS:
                break
            if (len(word) < 3 or word in STOP_WORDS or word in TITLE_CONNECTORS or word in TITLE_BAD_ENDINGS or
                    word in title_words or word in desc_words or word in new_keywords or
                    word + 's' in new_keywords or (word.endswith('s') and word[:-1] in new_keywords)):
                continue
            new_keywords.append(word)
        return title, ', '.join(new_keywords)
    
    def _title_candidates(self, metadata, taken_titles, forbidden_words):
        """Title variants: up to 3 from AI, then the old title with one of the keywords added"""
        if OPENAI_AVAILABLE and self.api_key:
            prompt = (f"This stock footage title is already used by another clip: \"{metadata.get('title', '')}\".\n"
                      f"Clip description: {metadata.get('description', '')}\n"
                      f"Clip keywords: {metadata.get('keywords', '')}\n\n"
                      f"Write a DIFFERENT title for this clip: 6 to 10 words, English letters and spaces only, "
                      f"based on a detail from the keywords that sets this clip apart. "
                      f"Do NOT use any of these words: {', '.join(forbidden_words)}.\n"
                      f"It must not repeat any of these titles: {'; '.join(sorted(taken_titles))}.\n\n"
                      f"Return ONLY the title.")
            messages = [{"role": "user", "content": prompt}]
            try:
                client = OpenAI(api_key=self.api_key)
                for attempt in range(1, 4):
                    response = client.chat.completions.create(
                        model=OPENAI_MODEL,
                        reasoning_effort="none",
                        messages=messages,
                        max_completion_tokens=60,
                        temperature=0.5 if attempt == 1 else 0.9
                    )
                    title_raw = self.clean_text(response.choices[0].message.content or '')
                    if title_raw:
                        yield title_raw
                    messages = messages + [
                        {"role": "assistant", "content": title_raw},
                        {"role": "user", "content": "Not accepted: it repeats a used title, uses a forbidden word "
                                                    "or is shorter than 6 words. Write a different title. "
                                                    "Return ONLY the title."}]
            except Exception as e:
                self.log_message.emit(f"    ⚠ [Recheck] Title request failed: {str(e)}")
        old_title = metadata.get('title', '')
        for keyword in metadata.get('keywords', '').split(','):
            if keyword.strip():
                yield f"{old_title} {keyword.strip()}"
                yield f"{keyword.strip()} {old_title}"
    
    def write_iptc_with_exiftool(self, image_path, title, description, keywords_list):
        """Writes IPTC metadata through exiftool (for compatibility with macOS Photos)"""
        self.log_message.emit(f"    [Write IPTC] Starting IPTC metadata write through exiftool...")
        
        try:
            # Check for exiftool
            result = subprocess.run(['which', 'exiftool'], capture_output=True, text=True)
            if result.returncode != 0:
                raise FileNotFoundError("exiftool not found")
            
            self.log_message.emit(f"    [Write IPTC] exiftool found")
            
            # Form exiftool command
            cmd = ['exiftool', '-overwrite_original', '-q']
            
            # Title (IPTC ObjectName)
            if title:
                cmd.extend(['-IPTC:ObjectName=' + title])
                cmd.extend(['-XMP:Title=' + title])
                self.log_message.emit(f"    [Write IPTC] Title added to command")
            
            # Description (IPTC Caption-Abstract)
            if description:
                cmd.extend(['-IPTC:Caption-Abstract=' + description])
                cmd.extend(['-XMP:Description=' + description])
                self.log_message.emit(f"    [Write IPTC] Description added to command")
            
            # Keywords (IPTC Keywords)
            if keywords_list:
                for keyword in keywords_list:
                    cmd.extend(['-IPTC:Keywords+=' + keyword])
                cmd.extend(['-XMP:Subject=' + ','.join(keywords_list)])
                self.log_message.emit(f"    [Write IPTC] Keywords added to command ({len(keywords_list)} words)")
            
            # Add file path
            cmd.append(str(image_path))
            
            self.log_message.emit(f"    [Write IPTC] Executing exiftool command...")
            
            # Execute command
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                self.log_message.emit(f"    [Write IPTC] IPTC metadata successfully written")
            else:
                self.log_message.emit(f"    [Write IPTC] Warning: exiftool returned code {result.returncode}")
                if result.stderr:
                    self.log_message.emit(f"    [Write IPTC] exiftool error: {result.stderr[:200]}")
                    
        except FileNotFoundError:
            raise
        except subprocess.TimeoutExpired:
            self.log_message.emit(f"    [Write IPTC] Error: exiftool execution timeout")
            raise Exception("exiftool execution timeout")
        except Exception as e:
            self.log_message.emit(f"    [Write IPTC] Error writing IPTC: {str(e)}")
            raise


class EditDialog(QDialog):
    def __init__(self, column_name, current_value, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit {column_name}")
        self.setModal(True)
        
        layout = QVBoxLayout()
        layout.addWidget(QLabel(f"{column_name}:"))
        
        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(current_value)
        layout.addWidget(self.text_edit)
        
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
        self.setLayout(layout)
    
    def get_value(self):
        return self.text_edit.text()


class KeyCutterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"TagStock PhotoKey {__version__} - Batch image processing for stock")
        self.setGeometry(100, 100, 1200, 800)
        
        # Set window icon
        self.set_window_icon()
        
        # Data for processing
        self.selected_folder = None
        self.output_folder = None
        self.processing_thread = None
        self._processing_stopped = False  # Flag for stopping processing
        
        self.create_ui()
    
    def set_window_icon(self):
        """Sets window icon from logo"""
        logo_path = self.get_logo_path()
        if logo_path and os.path.exists(logo_path):
            try:
                icon = QIcon(logo_path)
                self.setWindowIcon(icon)
            except Exception as e:
                print(f"Failed to load icon: {e}")
    
    def get_logo_path(self):
        """Returns path to logo"""
        # Try to find logo in different places
        possible_paths = [
            os.path.join(os.path.dirname(__file__), 'assets', 'logo.png'),
            os.path.join(os.path.dirname(__file__), 'assets', 'logo.jpg'),
            os.path.join(os.path.dirname(__file__), 'logo.png'),
            os.path.join(os.path.dirname(__file__), 'logo.jpg'),
        ]
        for path in possible_paths:
            if os.path.exists(path):
                return path
        return None
    
    def create_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        layout = QVBoxLayout()
        central_widget.setLayout(layout)
        
        # Create main interface
        self.create_main_interface(layout)
    
    def create_main_interface(self, layout):
        
        # Logo at top of interface
        logo_path = self.get_logo_path()
        if logo_path and os.path.exists(logo_path):
            try:
                logo_label = QLabel()
                pixmap = QPixmap(logo_path)
                # Scale logo to reasonable size (maximum width 300px)
                if pixmap.width() > 300:
                    pixmap = pixmap.scaledToWidth(300, Qt.TransformationMode.SmoothTransformation)
                logo_label.setPixmap(pixmap)
                logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(logo_label)
            except Exception as e:
                print(f"Failed to load logo: {e}")
        
        # Select folder with JPEG images
        folder_group = QGroupBox("Folder with source JPEG and video files (MOV, MP4, MPG)")
        folder_layout = QHBoxLayout()
        self.folder_path_edit = QLineEdit()
        self.folder_path_edit.setReadOnly(True)
        folder_btn = QPushButton("Select folder")
        folder_btn.clicked.connect(self.select_input_folder)
        folder_layout.addWidget(self.folder_path_edit)
        folder_layout.addWidget(folder_btn)
        folder_group.setLayout(folder_layout)
        layout.addWidget(folder_group)
        
        # Select folder for saving processed files
        output_group = QGroupBox("Folder for saving processed files")
        output_layout = QHBoxLayout()
        self.output_path_edit = QLineEdit()
        self.output_path_edit.setReadOnly(True)
        output_btn = QPushButton("Select folder")
        output_btn.clicked.connect(self.select_output_folder)
        output_layout.addWidget(self.output_path_edit)
        output_layout.addWidget(output_btn)
        output_group.setLayout(output_layout)
        layout.addWidget(output_group)

        # Last chosen folders, if they still exist
        settings = QSettings(KEYRING_SERVICE, 'app')
        input_folder = settings.value('input_folder', '', type=str)
        if input_folder and os.path.isdir(input_folder):
            self.folder_path_edit.setText(input_folder)
            self.selected_folder = input_folder
        output_folder = settings.value('output_folder', '', type=str)
        if output_folder and os.path.isdir(output_folder):
            self.output_path_edit.setText(output_folder)
            self.output_folder = output_folder

        # Processing settings
        settings_group = QGroupBox("Settings")
        settings_layout = QVBoxLayout()
        
        # API key (automatically set, can be changed)
        api_key_layout = QHBoxLayout()
        api_key_label = QLabel("API Key (GPT):")
        self.api_key_edit = QLineEdit()
        # Last used key from keychain, otherwise OPENAI_API_KEY environment variable
        self.api_key_edit.setText(load_saved_api_key() or os.getenv('OPENAI_API_KEY', ''))
        self.api_key_edit.setPlaceholderText("sk-... (OpenAI GPT API key)")
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        api_key_layout.addWidget(api_key_label)
        api_key_layout.addWidget(self.api_key_edit)
        settings_layout.addLayout(api_key_layout)
        
        # Country of shoot for iStock CSV, remembered between launches
        country_layout = QHBoxLayout()
        country_label = QLabel("Country of shoot (iStock):")
        self.country_edit = QLineEdit()
        self.country_edit.setText(QSettings(KEYRING_SERVICE, 'app').value('istock_country', '', type=str))
        self.country_edit.setPlaceholderText("e.g. Serbia (as in ESP Country of shoot list)")
        self.country_edit.textChanged.connect(
            lambda text: QSettings(KEYRING_SERVICE, 'app').setValue('istock_country', text.strip()))
        country_layout.addWidget(country_label)
        country_layout.addWidget(self.country_edit)
        settings_layout.addLayout(country_layout)
        
        # Shot speed for iStock CSV (ESP requires it). Not remembered: Real Time at every launch,
        # Slow Motion / Time Lapse are chosen for the run they apply to
        shot_speed_layout = QHBoxLayout()
        shot_speed_label = QLabel("Shot speed (iStock):")
        self.shot_speed_combo = QComboBox()
        self.shot_speed_combo.addItems(SHOT_SPEEDS)
        self.shot_speed_combo.setCurrentText(SHOT_SPEEDS[0])
        shot_speed_layout.addWidget(shot_speed_label)
        shot_speed_layout.addWidget(self.shot_speed_combo)
        settings_layout.addLayout(shot_speed_layout)
        
        # Stock sites to make CSVs for, remembered between launches
        stocks_layout = QHBoxLayout()
        stocks_layout.addWidget(QLabel("Make CSV for:"))
        saved_stocks = QSettings(KEYRING_SERVICE, 'app').value('csv_stocks', list(STOCKS), type=list)
        self.stock_checkboxes = {}
        for key, name in STOCKS.items():
            checkbox = QCheckBox(name)
            checkbox.setChecked(key in saved_stocks)
            checkbox.toggled.connect(self.save_stocks)
            stocks_layout.addWidget(checkbox)
            self.stock_checkboxes[key] = checkbox
        stocks_layout.addStretch()
        settings_layout.addLayout(stocks_layout)
        
        settings_group.setLayout(settings_layout)
        layout.addWidget(settings_group)
        
        # Processing control buttons
        buttons_layout = QHBoxLayout()
        self.start_btn = QPushButton("Start processing")
        self.start_btn.clicked.connect(self.start_processing)
        buttons_layout.addWidget(self.start_btn)
        
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_processing)
        self.stop_btn.setEnabled(False)  # Inactive by default
        buttons_layout.addWidget(self.stop_btn)
        
        layout.addLayout(buttons_layout)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        layout.addWidget(self.progress_bar)
        
        # Processing log
        log_group = QGroupBox("Processing log")
        log_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        layout.addWidget(log_group)
    
    def select_input_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder with JPEG and video files")
        if folder:
            self.folder_path_edit.setText(folder)
            self.selected_folder = folder
            QSettings(KEYRING_SERVICE, 'app').setValue('input_folder', folder)

    def select_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder for saving processed files")
        if folder:
            self.output_path_edit.setText(folder)
            self.output_folder = folder
            QSettings(KEYRING_SERVICE, 'app').setValue('output_folder', folder)

    def save_stocks(self):
        """Remembers the checked stock sites (Make CSV for) for the next launch"""
        stocks = [key for key, checkbox in self.stock_checkboxes.items() if checkbox.isChecked()]
        QSettings(KEYRING_SERVICE, 'app').setValue('csv_stocks', stocks)
        return stocks
    
    def log(self, message):
        """Adds message to log"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        self.log_text.append(f"{timestamp} - {message}")
    
    def start_processing(self):
        if not self.selected_folder or not self.output_folder:
            QMessageBox.critical(self, "Error", "Select input and output folders")
            return
        
        if self.processing_thread and self.processing_thread.isRunning():
            QMessageBox.warning(self, "Warning", "Processing already in progress")
            return
        
        # Clear log
        self.log_text.clear()
        
        # Manage buttons
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)  # Activate stop button
        self.progress_bar.setValue(0)
        
        # Create and start thread
        api_key = self.api_key_edit.text().strip() if hasattr(self, 'api_key_edit') and self.api_key_edit.text().strip() else None
        
        # Remember the key for next launch
        if api_key and api_key != load_saved_api_key():
            if save_api_key(api_key):
                self.log("[UI] API key saved to keychain, it will be used on next launch")
            else:
                self.log("[UI] ⚠ Failed to save API key to keychain")
        
        country = self.country_edit.text().strip()
        shot_speed = self.shot_speed_combo.currentText()
        stocks = self.save_stocks()
        
        self.log(f"[UI] Creating processing thread: api_type=openai (GPT), generate_metadata=True")
        
        # Metadata generation always enabled by default
        self.processing_thread = ProcessingThread(
            self.selected_folder,
            self.output_folder,
            generate_metadata=True,  # Always enabled
            api_key=api_key,  # Using automatic key, if no other specified
            country=country,
            shot_speed=shot_speed,
            stocks=stocks
        )
        self.processing_thread.progress.connect(self.update_progress)
        self.processing_thread.log_message.connect(self.log)
        self.processing_thread.finished.connect(self.processing_finished)
        self._processing_stopped = False  # Reset stop flag
        self.processing_thread.start()
    
    def update_progress(self, current, total):
        """Updates progress bar"""
        value = int((current / total) * 100) if total > 0 else 0
        self.progress_bar.setValue(value)
    
    def stop_processing(self):
        """Stops processing"""
        if self.processing_thread and self.processing_thread.isRunning():
            self._processing_stopped = True
            self.processing_thread.stop()
            self.log("Processing stop requested...")
            # Buttons will be updated in processing_finished
    
    def clean_text(self, text):
        """Cleans text from special characters, keeps only English letters, numbers and spaces"""
        cleaned = re.sub(r'[-\u2010-\u2015/]', ' ', text)
        cleaned = re.sub(r'[^a-zA-Z0-9\s]', '', cleaned)
        cleaned = ' '.join(cleaned.split())
        return cleaned
    
    def extract_words(self, text, max_words=50):
        """Extracts individual words from text (not phrases)"""
        cleaned = self.clean_text(text)
        words = cleaned.lower().split()
        unique_words = []
        seen = set()
        for word in words:
            if word not in seen and len(word) > 0:
                unique_words.append(word)
                seen.add(word)
                if len(unique_words) >= max_words:
                    break
        return ', '.join(unique_words)
    
    def processing_finished(self, data):
        """Called after processing completion"""
        self.log("[UI] Processing completed, updating interface...")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)  # Deactivate stop button
        self.progress_bar.setValue(100)
        
        # Show message only if processing was not stopped
        if not self._processing_stopped:
            self.log("[UI] Showing success completion message")
            QMessageBox.information(
                self,
                "Done",
                f"Processing completed!\nPhotos with metadata and CSVs ({', '.join(map(self.processing_thread.csv_label, self.processing_thread.csv_names())) or 'none'}) saved to output folder.\nSee the log for videos that do not meet Adobe Stock requirements.\n\nLog saved to:\n{self.processing_thread.log_file_path}"
            )
        else:
            self._processing_stopped = False  # Reset flag
            self.log("[UI] Processing was stopped by user")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = KeyCutterApp()
    window.show()
    sys.exit(app.exec())
