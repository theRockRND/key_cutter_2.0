# Changelog

## 1.1.13

### Fixed
- Glued keywords are split: a word the macOS spell checker (en_US) does not know that is two known words glued together becomes these two keywords (`stationhouse` -> `station`, `house`; `railwayline` -> `railway`, `line`), stock sites reject such words. Proper names in lower case (`poland`, `european`) are not split. Replacements are logged as `Glued keyword ... split into`. Without the spell checker (not macOS) keywords stay as is

## 1.1.12

### Changed
- Each stock site has its own CSV folder in the output folder: `Adobe Stock/`, `iStock/`, `Shutterstock/`
- CSVs of earlier runs are kept: if the folder already has a CSV with this name, the new one gets `_2`, `_3`... (`shutterstock_videos_2.csv`). Replaces the removal of old CSVs from 1.1.11
- Run logs stay in the project `logs` folder, not in the output folder

## 1.1.11

### Added
- **Settings > Make CSV for:** Adobe Stock, iStock, Shutterstock checkboxes: CSVs are made only for the checked stock sites (all three by default, the choice is remembered between launches). Without iStock, ESP is not used and no iStock warnings are logged. With nothing checked, videos are skipped (their metadata goes only to CSVs), photos still get metadata in the file
- Shutterstock CSVs: `shutterstock_photos.csv` and `shutterstock_videos.csv` (photos and videos are separate tabs on Shutterstock), in the format of the Shutterstock sample `csv-templates/shutterstock-MetadataTemplate.csv`: `Filename, Description, Keywords, Categories, Editorial, Mature content, illustration`. Description is the title and the description in one text (up to 200 characters), Categories are left empty (choose them on the site), the flags are `no`

### Changed
- CSVs of the previous run are removed from the output folder at the start of a run, so a CSV of a stock site that is not checked does not stay there

## 1.1.10

### Added
- `shot speed` in the iStock CSV is filled (ESP requires it, an empty value blocks submitting): new **Shot speed (iStock)** setting with the ESP values Real Time / Slow Motion / Time Lapse, default Real Time, remembered between launches

## 1.1.9

### Added
- iStock keywords are matched to the Getty controlled vocabulary, so ESP accepts them without red "needs clarification" keywords: each keyword becomes the term it means in the clip (`sleepers` -> `Railroad Tie`, `train` -> `Train - Vehicle`, `tranquil` -> `Tranquility`). Candidate terms come from ESP itself (the same lookup as ESP keyword disambiguation), the AI picks the meaning for each clip. Relevant ESP "Get suggestions" terms for the title are added. Up to 48 terms (ESP adds orientation and country itself)
- `istock_vocabulary.json`: looked-up words are saved, known words need no ESP. New words are looked up through an open ESP tab in Chrome (View > Developer > Allow JavaScript from Apple Events). Without ESP, new words stay as is and the log says so
- Adobe Stock CSV keeps the plain keywords

## 1.1.8

### Changed
- `istock_videos.csv` follows the ESP template `csv-templates/istock-MetadataTemplate.csv`: columns and their order are read from the template (`file name, description, title, country, poster timecode, keywords, shot speed, date created`), line endings `\n` as in the template. The date column is now `date created` (was `created date`, which ESP does not know). `shot speed` is left empty: set it in ESP. If Getty changes the template, replace the file

## 1.1.7

### Fixed
- A title no longer repeats one root: a word of the same root as an earlier one is dropped ("Railway Rails Curve" -> "Railway Curve", also rock/rocky, forest/forested, snow/snowman)
- A title no longer ends with a hanging adverb (ahead, nearby, beyond, away...): "Arch Framed by Pine Foliage Ahead" -> "Arch Framed by Pine Foliage"
- Both rules apply to every title: photos, videos and titles replaced by the recheck

## 1.1.6

### Added
- After all clips are described, the video CSVs are re-checked: a title already used by another clip is replaced (the first clip keeps it). The AI is asked for a different title based on a detail from the keywords (up to 3 attempts; without AI the old title gets one of the keywords). The new title never uses description words; its words leave the keywords and the dropped title words come back to them. Both CSVs are rewritten, replacements are listed in the log under `[Recheck]`

## 1.1.5

### Added
- `istock_videos.csv` is filled completely: new `created date` column (shooting date from the camera file name, e.g. `DJI_20260525...` -> `05/25/2026`; file dates are not used because after editing they are the export date), `country` from the new **Country of shoot (iStock)** field in Settings (remembered between launches), `poster timecode` = middle frame of the clip (`HH:MM:SS:FF`)
- Warnings in the log when the country is empty or a file name has no date

## 1.1.4

### Added
- `istock_videos.csv` is written to the output folder next to `adobe_stock_videos.csv`, for the iStock (Getty ESP) CSV upload: `file name, title, description, keywords, country, poster timecode`. Country and poster timecode are left empty: set Country of shoot in ESP

## 1.1.3

### Added
- The log of every run is saved to `logs/run_YYYY-MM-DD_HH-MM-SS.log` next to `main.py` (also when the run is stopped or fails). The path is shown at the start and end of the log and in the completion message

### Fixed
- Title keeps linking words between content words: "Railway Tracks and White Car Through Pine Forest" instead of "Railway Tracks White Car Through Pine Forest". Articles are still dropped, linking words never start or end a title. Linking words (and, in, through, toward...) are not title words: the description may use them, keywords never do
- Description is no longer broken by cutting words out of the middle ("dim passage a sunlit forest"): the AI gets up to 3 rewrite attempts, each told what was wrong; if none is clean, the sentence is cut before the first forbidden word
- A description that is too long is cut before the last unfinished phrase ("...through sunny woods" instead of "...through sunny woods and rocky")
- Hyphens, dashes and slashes become spaces: "forward facing" instead of "forwardfacing"
- A short title is not padded with a singular/plural duplicate of its own word (Stick / Sticks)

## 1.1.2

### Added
- Unit tests (`tests/`, stdlib `unittest`, no extra dependencies): Adobe Stock video requirements, ffprobe/frame extraction on generated clips, metadata rules (title/description/keywords, vertical video), OpenAI request handling (JSON mode, retries, fatal errors) with a fake client, description rewrite, Keychain storage, end-to-end batch processing. Run with `python -m unittest discover -s tests -t .`

## 1.1.1

### Fixed
- Requests use JSON mode (`response_format`) and are retried once on invalid JSON or a temporary API error
- One failed file no longer stops the batch: it is skipped and listed at the end. Only key or balance errors stop processing
- A description longer than 80 characters is rewritten by the AI instead of being cut off; the rewrite gets the full list of forbidden words and a second attempt
- A short title is padded with AI keywords, not with description words

## 1.1.0

### Added
- Video support (MOV, MP4, MPG): each clip is checked against the Adobe Stock technical requirements for video (format, 5-60 s duration, 3900 MB limit, frame rate, codec, resolution, 48 kHz 16-bit PCM audio). Clips that fail are skipped, with the reasons listed in the log
- Video analysis: 3 frames from the clip are sent to the AI as one video
- Vertical clips get "Vertical Video" in the title and "vertical, video" at the start of the keywords
- `adobe_stock_videos.csv` is written to the output folder for the Adobe Stock CSV upload. Video files are not copied or modified
- The last used API key is saved to macOS Keychain and filled in on the next launch
- Version number shown in the window title

### Changed
- Model switched from `gpt-5.1` to `gpt-6-luna` (`reasoning_effort="none"`)
- Keywords: up to 49 (the Adobe Stock limit), only real words from the AI, no generated variations and no singular/plural duplicates. The AI is asked for 65 words so enough remain after filtering
- Description: a natural sentence from the AI. If it uses title words, the AI is asked to rewrite it
- File search is case-insensitive without duplicates on macOS

### Security
- Removed the hardcoded OpenAI API key. The key now comes from the UI field, the Keychain or `OPENAI_API_KEY`
