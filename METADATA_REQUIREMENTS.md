# METADATA REQUIREMENTS FOR STOCK IMAGES

## TITLE

### Main Requirements:
- ✅ **Generation based on image analysis** - title is formed exclusively based on AI analysis of image content
- ✅ **6-10 words** - title must contain no more than 10 words; a title of 5 words or less is padded with AI keywords (not description words, no singular/plural duplicates)
- ✅ **Reads as a phrase** - linking words between content words are kept: "Railway Tracks and White Car Through Pine Forest", "Young Man Climbs Rocky Mound in Sunny Woodland"
- ✅ **No word repetitions** - each content word in title must be unique (not repeated)
- ✅ **English text only** - no other languages
- ✅ **No special characters** - only English letters, numbers and spaces
- ✅ **Uniqueness** - title should not repeat for different images (previous title is tracked)
- ✅ **Format** - each word with capital letter (Title Case); linking words of 3 letters or less (and, in, of...) are lowercase

### Linking words (kept between content words, never first or last, never two in a row):
- and, or, at, in, on, of, for, to, with, by, from, into, onto, through, toward, towards, past, over, under, beneath, beside, along, across, around, near, between, behind, above, below, inside, among, amid
- They are **not title words**: the description may use them, keywords never contain them

### Excluded words (stop words):
- Articles: the, a, an
- Linking verbs: is, are, was, were
- Demonstrative pronouns: this, that, these, those
- Personal pronouns: it, its, they, them, what, you
- Common words: see, image, photography, photo, picture, detailed, describe, scene, happening, showing, view

### Data Source:
- Only words from AI image analysis (captions from BLIP model)
- Predefined lists or templates are NOT used

---

## DESCRIPTION

### Main Requirements:
- ✅ **Natural sentence from image analysis** - the AI writes one grammatical sentence; it is kept as a phrase (not rebuilt from separate words)
- ✅ **Must NOT repeat title** - CRITICALLY IMPORTANT: if the AI sentence uses title content words (or generic words), a text-only request asks the AI to rewrite it without them - up to 3 attempts, each told what was wrong with the previous one. If no attempt is clean, the sentence is **cut before the first forbidden word** (at least 6 words must remain); words are never cut out of the middle of the sentence, except as a last resort
- ✅ **Must NOT share words with keywords** - CRITICALLY IMPORTANT: description words are removed from keywords
- ✅ **15-80 characters, more than 5 words** - a longer sentence is rewritten by the AI; if it is still too long, it is cut before the last unfinished phrase ("...through sunny woods", not "...through sunny woods and rocky") and never ends with an article or preposition
- ✅ **Hyphenated words** - hyphens, dashes and slashes become spaces ("forward facing", not "forwardfacing")
- ✅ **English text only, no special characters**
- ✅ **Format** - first letter capital
- ✅ Articles and prepositions (a, the, on, with...) are allowed for grammar

### Removed generic words:
- see, image, photography, photo, picture, detailed, describe, scene, happening, showing, view, professional, high, quality, list, main, objects, colors, elements

### Fallback:
- If fewer than 6 words remain after removing title words, description is built from analysis words (without title words)

---

## KEYWORDS

### Main Requirements:
- ✅ **Up to 49 words** - Adobe Stock limit; the AI is asked for 65 words so that enough remain after filtering
- ✅ **Warning below 25 words** - fewer keywords are written as is, processing continues
- ✅ **Only real words from AI analysis** - no generated variations (adding "s", cutting endings)
- ✅ **Only individual words** - phrases from AI are skipped
- ✅ **NOT taken from title**, **NOT taken from description**
- ✅ **No singular/plural duplicates** (swing / swings)
- ✅ **Unique, minimum 3 characters, lowercase, separated by ", "**
- ✅ **Order** - AI order (most relevant first), then other words from AI title/description analysis
- ✅ **Vertical video** - starts with "vertical, video"

### Excluded words (stop words):
- Articles, prepositions, linking verbs, conjunctions, pronouns: the, a, an, of, in, on, at, to, for, with, is, are, was, were, and, or, but, this, that, these, those, it, its, they, them, what, you, by, from, as, be, have, has, had, do, does, did, will, would, could, should, may, might, must, can, up, out, so, if, no, not, only, just, more, most, very, much, many, some, any, all, each, every, both, such, than, too, also, here, there, where, when, why, how, who, which, whose, whom
- Common words: see, image, photography, photo, picture, detailed, describe, scene, happening, showing, view, professional, high, quality

---

## GENERAL REQUIREMENTS

### Data Source for All Fields:
- ✅ All metadata is generated based on AI image analysis
- ✅ OpenAI model `gpt-6-luna` is used
- ✅ Model analyzes image content and generates descriptions
- ✅ Words for title, description and keywords are extracted from these descriptions

### Field Relationships:
- ✅ Title and Description - must NOT have common words (linking words like "and", "through" are allowed)
- ✅ Description and Keywords - must NOT have common words
- ✅ Title and Keywords - no common words (except "vertical, video" for vertical clips)

### Recording Format:
- ✅ All metadata is written directly to JPEG files via EXIF/IPTC
- ✅ Stock platforms automatically pull data when uploading

---

## VIDEO

- ✅ Video is analyzed through 3 frames (25%, 50%, 75% of the clip); Title/Description/Keywords follow the same rules as photos
- ✅ **Vertical clips** - title ends with "Vertical Video", keywords start with "vertical, video" (Adobe Stock requirement); these words are never used in description
- ✅ Clips not meeting Adobe Stock technical requirements are skipped (see README, section "Video (Adobe Stock)")
- ✅ **No repeated root in a title** - "Railway Rails", "Rocky Rock", "Forested Forest" are not allowed: the later word is dropped
- ✅ **No hanging ending** - a title never ends with ahead, nearby, beyond, away, forward, back...
- ✅ **iStock keywords are Getty vocabulary terms** - each keyword is replaced by the term it means in the clip (candidates from ESP, meaning chosen by AI), relevant ESP title suggestions added, up to 48 terms; Adobe Stock keeps plain words
- ✅ **Unique titles** - after all clips are described, a title repeated by several clips is replaced (first clip keeps it); the new title follows the same rules, keywords are rebuilt for it
- ✅ Video files are not copied or modified - metadata is saved only to `adobe_stock_videos.csv` (Adobe Stock CSV upload) and `istock_videos.csv` (iStock / Getty ESP CSV upload) in the output folder

---

## EXAMPLES

### ❌ INCORRECT:

**Title:** "Small Bird Standing Next Door"  
**Description:** "Small bird standing next door" ← repeats title!  
**Keywords:** "swing, swings, wint, snowys, colds" ← duplicates and invented words!

---

### ✅ CORRECT:

**Title:** "Small Bird Standing Next Door"  
**Description:** "Perched on a wooden surface near the entrance" ← natural sentence, no title words  
**Keywords:** "wildlife, animal, feather, beak, branch, tree, garden, brown, gray, outdoor, nature, tiny, cute, calm, ..." ← up to 49 real words, none from title or description

---

## NOTES

- All requirements are strictly followed in the code
- If any requirement is violated, a warning is generated in logs
- The log of every run is saved to `logs/run_YYYY-MM-DD_HH-MM-SS.log` (see README, section "Logs")
- If fewer than 25 keywords remain after filtering, a warning is displayed
- Description is always checked for common phrases and recreated if necessary

