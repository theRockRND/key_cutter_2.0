# TagStock PhotoKey - Batch image processing for stock

Application for batch image processing with **AI content analysis** and automatic metadata generation for uploading to stock sites (Adobe Stock, Shutterstock, Getty Images, etc.).

## Features

### Quick Mode
- **AI content analysis of images** - the program analyzes what is shown in the photo and generates metadata based on the content
- **Local analysis without API** - uses pre-trained models (BLIP) for image analysis directly on your computer
- **External API support** - optionally use OpenAI API or custom API endpoint
- Automatic processing of all JPEG files and video clips (MOV, MP4, MPG) in the selected folder
- Video clips are checked against Adobe Stock technical requirements (see [Video](#video-adobe-stock))
- Metadata generation (title, description, keywords) following strict stock site rules
- **Direct metadata writing to image files** (EXIF/IPTC) - stock sites automatically pull data when uploading
- Files are copied to a second folder with written metadata

### Table Mode
- Manual metadata editing in table
- Load and save CSV files
- Add individual images
- Double-click to edit cells

## Installation and Launch

### First Launch

1. Create a virtual environment (if not already created):
```bash
python3 -m venv venv
```

2. Activate the virtual environment:
```bash
source venv/bin/activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

### Launching the Application

**Method 1: Via script (recommended)**
```bash
./run.sh
```

**Method 2: Manually**
```bash
source venv/bin/activate
python main.py
```

**Method 3: Direct launch (if virtual environment is already activated)**
```bash
python main.py
```

## Usage

### Quick Mode

1. Select folder with source JPEG files
2. Select folder for saving processed JPEG files
3. Enable option "Generate metadata (AI image analysis)"
4. **Select analysis type:**
   - **Local (no API)** - uses pre-trained models on your computer (recommended, no API keys required)
   - **OpenAI API** - uses OpenAI Vision API (requires API key)
   - **Custom API** - uses your own API endpoint
5. Click "Start processing"
6. After completion you will get:
   - **Processed JPEG files with written metadata** (title, description, keywords embedded in files via EXIF/IPTC)
   - Metadata is generated based on AI analysis of image content
   
**Important:** 
- Metadata is written directly to image files, so when uploading to stock sites they are automatically pulled into the required fields!
- **Local analysis works without internet and API keys** - model is downloaded once on first use (~990MB)
- OpenAI API requires a key (can be obtained at https://platform.openai.com/api-keys)

### Table Mode

1. Load existing CSV file or add images manually
2. Edit metadata by double-clicking cells
3. Save result to CSV file

## Video (Adobe Stock)

The input folder may also contain video clips (MOV, MP4, MPG). Before processing, each clip is checked
against the [Adobe Stock technical requirements for video](https://helpx.adobe.com/stock/contributor/submit-your-content/submit-videos/technical-requirements-for-video-submissions.html)
(`video_utils.py`). Clips that fail are **not copied** to the output folder; the reasons are listed in the log and in the final summary.

| Parameter | Requirement |
|---|---|
| Format | MOV, MPG, MP4 |
| Duration | 5-60 s (10-20 s recommended - warning only) |
| File size | up to 3900 MB |
| Frame rate | 23.976, 24, 25, 29.97, 30, 50, 59.94, 60 fps |
| Codec | ProRes, H.264 (recommended); MPEG-2, MPEG-4, Motion JPEG, PNG (accepted, warning) |
| Horizontal | 1920x1080, 2048x1080, 3840x2160, 4096x2160, 4096x2304; width 1920-4096 with wide-screen ratios (e.g. 2.39:1) |
| Vertical | 1080x1920, 2160x3840, 2160x4096, 2304x4096 |
| Square | 1080x1080, 2160x2160 |
| Audio (optional) | 48 kHz, 16-bit uncompressed PCM |

For clips that pass:
- 3 frames (at 25%, 50%, 75% of the clip) are sent to the AI as one video; metadata rules are the same as for photos
- vertical clips get the words `vertical` and `video` in the title and at the start of keywords (Adobe Stock rule)
- video files are **not copied and not modified** - their metadata goes only to the CSVs of the checked stock sites: `Adobe Stock/adobe_stock_videos.csv` (Filename, Title, Keywords, Category, Releases) and `iStock/istock_videos.csv` in the ESP template format (`csv-templates/istock-MetadataTemplate.csv`: file name, description, title, country, poster timecode, keywords, shot speed, date created)
- after all clips are described, the CSVs are re-checked: a title repeated by several clips is replaced for every clip after the first (stock sites treat equal titles as duplicates); see `[Recheck]` in the log
- iStock keywords are Getty vocabulary terms (`sleepers` -> `Railroad Tie`): stay signed in to **esp.gettyimages.com** in Chrome (if no ESP tab is open, the app opens one itself) and enable **View > Developer > Allow JavaScript from Apple Events** so new words can be looked up (requests only read from ESP); known words are taken from `istock_vocabulary.json`; see `[iStock]` in the log
- Adobe Stock: upload the clips to Adobe Stock Contributor, then in Uploaded Files > New click **Upload CSV**; filenames must match exactly (extension and case)
- Shutterstock: `shutterstock_videos.csv` and, for photos, `shutterstock_photos.csv` (format of `csv-templates/shutterstock-MetadataTemplate.csv`, Categories: 1-2 categories of the Shutterstock list picked by the AI, flags `no`); upload the files, then in Portfolio > Not submitted open the Photos or Footage tab and click **Upload CSV**
- the last input and output folders, API key, Country of shoot and Make CSV for are remembered between launches
- **Settings > Make CSV for** selects the stock sites (Adobe Stock, iStock, Shutterstock; all by default, remembered); CSVs are made only for the checked ones
- each stock site has its own CSV folder in the output folder (`Adobe Stock/`, `iStock/`, `Shutterstock/`); CSVs of earlier runs are kept, a new CSV with a taken name gets `_2`, `_3`... (`shutterstock_videos_2.csv`); run logs are saved to the project `logs` folder
- iStock: upload the clips to the ESP batch, then in the metadata panel open **Templates > Upload and apply** and choose `istock_videos.csv`
- iStock CSV fields: columns and their order are taken from the ESP template, replace `csv-templates/istock-MetadataTemplate.csv` if Getty changes it; `shot speed` comes from **Settings > Shot speed (iStock)** (Real Time at every launch, not remembered; ESP requires it); `date created` is the shooting date from the camera file name (`DJI_20260525...` -> `05/25/2026`, empty if the name has no date); `country` comes from **Settings > Country of shoot (iStock)** (use the name from the ESP list, e.g. `Serbia`); `poster timecode` is the middle frame of the clip

Video support requires **ffmpeg** (`brew install ffmpeg`).

## Metadata Format

### Metadata Generation Rules:

**Title:**
- English text only (a-z, A-Z, 0-9, spaces), no special characters
- 6-10 words, reads as a phrase: linking words between content words are kept ("Railway Tracks and White Car Through Pine Forest")
- Without AI analysis: formatted from filename

**Description:**
- One natural English sentence from the AI, without title content words (linking words like "and", "through" are allowed)
- If it uses title words or is longer than 80 characters, the AI rewrites it (up to 3 attempts); words are not cut out of the middle of the sentence
- Does not share words with keywords
- 15-80 characters

**Keywords:**
- Up to 49 individual real words (Adobe Stock limit), no singular/plural duplicates
- English text only, no special characters
- Words separated by commas
- Not taken from title or description
- No glued words: a word glued from two real words (`stationhouse`) is split into them (`station`, `house`) using the macOS spell checker

### CSV Format

CSV file contains the following columns:
- `Filename` - source file name
- `Title` - image title
- `Description` - description
- `Keywords` - keywords (comma-separated)
- `Category` - category
- `Model Release` - model release availability (Yes/No)

## Technical Details

### Writing Metadata to Files

Metadata is written to image files via:
- **JPEG/JPG:** EXIF/IPTC metadata (via piexif)
  - Title → EXIF ImageDescription + IPTC ObjectName
  - Description → EXIF UserComment + IPTC Caption
  - Keywords → EXIF/IPTC Keywords
- **PNG/TIFF:** Metadata via Pillow

Stock sites (Adobe Stock, Shutterstock, Getty Images, etc.) automatically read this metadata when uploading.

## Logs

The full log of every run is saved to the `logs/` folder next to `main.py`:

```
logs/run_2026-10-13_21-36-06.log
```

- one file per run, named by its start time; written when the run ends - also when it is stopped or stops on an error
- the first line has the app version and the AI model; the path is shown at the start and end of the log and in the completion message
- `logs/` is not committed to git

To get a run analyzed, attach the file (or give its path) instead of copying the log from the window.

## Tests

```bash
source venv/bin/activate
python -m unittest discover -s tests -t .
```

Tests use only the standard library and a fake OpenAI client (no API key and no network needed). Tests that need ffmpeg are skipped if it is not installed.

## Requirements

- Python 3.8+
- PyQt6 for graphical interface
- Pillow for image processing
- piexif for writing EXIF/IPTC metadata to JPEG files
- transformers, torch for local AI image analysis (installed automatically)
- openai for AI analysis via OpenAI API (optional)
- httpx for working with custom APIs (optional)
- exiftool for writing IPTC metadata (installed automatically via brew)
- ffmpeg/ffprobe for video checks and frame extraction (`brew install ffmpeg`)

**Note:** On first launch of local analysis, the BLIP-2 model will be downloaded. This happens once.
- **BLIP-2 Flan-T5-Large** (Salesforce/blip2-flan-t5-large) - optimal balance of speed and quality
- Model size: ~2-3GB (downloaded automatically)
- Faster than XL version, but still provides very high quality and detailed descriptions

## Image Analysis Types

### Local Analysis (recommended)
- **No API keys required** - works completely offline
- Uses pre-trained model **BLIP-2 Flan-T5-Large** (Salesforce/blip2-flan-t5-large)
- Optimal balance of speed and description quality
- Model is downloaded automatically on first use (~2-3GB)
- Works on CPU (significantly faster on GPU with CUDA, if available)
- Recommended to use GPU for faster processing
- Free and unlimited
- **Improved prompts** - uses 8 different prompts to get maximally detailed descriptions
- **Extended generation parameters** - max_length=300, num_beams=7 for longer and higher quality descriptions

### OpenAI API
To use OpenAI API:

1. Get API key at https://platform.openai.com/api-keys
2. Select "OpenAI API" in the program
3. Enter key in "API Key" field - after "Start processing" it is saved to macOS Keychain (service "TagStock PhotoKey") and filled in automatically on next launch
4. Or set environment variable: `export OPENAI_API_KEY="sk-..."`

**Note:** AI analysis uses model `gpt-6-luna` (OpenAI GPT-6 Luna, vision support, `reasoning_effort="none"`). The model is set by `OPENAI_MODEL` in `main.py`. Price: $0.10 / 1M input tokens, $0.50 / 1M output tokens - approximately $0.30 per 1000 images.

### Custom API
- Select "Custom API" in the program
- Specify your API endpoint
- Enter API key (if required)
- API must accept requests in OpenAI-compatible format

## Notes

- Source files are not modified - processed files are copied to a second folder
- Metadata is generated based on AI analysis of image content
- **Local analysis is used by default** - no API keys required and works offline
- All metadata complies with stock site requirements (English text only, no special characters)
- Title, Description and Keywords are written to EXIF/IPTC metadata for automatic pulling on stock sites
- Local model is downloaded once on first use and cached for subsequent launches
