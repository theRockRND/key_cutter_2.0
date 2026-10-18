"""Video helpers: Adobe Stock technical requirements check and frame extraction.

Requirements source:
https://helpx.adobe.com/stock/contributor/submit-your-content/submit-videos/technical-requirements-for-video-submissions.html
"""
import json
import subprocess
from pathlib import Path

# Containers accepted by Adobe Stock: MOV, MPG, MP4
VIDEO_EXTENSIONS = ['.mov', '.mp4', '.mpg', '.mpeg']

MIN_DURATION_SEC = 5
MAX_DURATION_SEC = 60
MAX_FILE_SIZE_MB = 3900

# Accepted frame rates (fps)
ALLOWED_FPS = [23.976, 24, 25, 29.97, 30, 50, 59.94, 60]
FPS_TOLERANCE = 0.01

# Codecs (ffprobe names). ProRes and H.264 are recommended,
# MPEG-2, MPEG-4, Motion JPEG and PNG are also accepted.
RECOMMENDED_CODECS = {'h264', 'prores'}
ACCEPTED_CODECS = RECOMMENDED_CODECS | {'mpeg2video', 'mpeg4', 'mjpeg', 'png'}

# Accepted resolutions
HORIZONTAL_RESOLUTIONS = {(1920, 1080), (2048, 1080), (3840, 2160), (4096, 2160), (4096, 2304)}
VERTICAL_RESOLUTIONS = {(1080, 1920), (2160, 3840), (2160, 4096), (2304, 4096)}
SQUARE_RESOLUTIONS = {(1080, 1080), (2160, 2160)}
MIN_WIDTH = 1920
MAX_WIDTH = 4096
# Wide-screen ratios (e.g. 2.39:1) are accepted with a lower height
MIN_WIDESCREEN_RATIO = 16 / 9

# Audio (optional): 48 kHz, 16-bit uncompressed
AUDIO_SAMPLE_RATE = 48000
AUDIO_CODECS = {'pcm_s16le', 'pcm_s16be'}


def is_video_file(path):
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def _parse_fps(rate):
    """Converts ffprobe rate ('30000/1001') to float"""
    if not rate or rate == '0/0':
        return None
    if '/' in rate:
        num, den = rate.split('/', 1)
        den = float(den)
        return float(num) / den if den else None
    return float(rate)


def probe_video(video_path):
    """Reads video parameters through ffprobe. Raises FileNotFoundError if ffprobe is missing."""
    cmd = ['ffprobe', '-v', 'error', '-print_format', 'json',
           '-show_format', '-show_streams', str(video_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise Exception(f"ffprobe failed: {result.stderr.strip()[:200]}")
    data = json.loads(result.stdout)

    streams = data.get('streams', [])
    video = next((s for s in streams if s.get('codec_type') == 'video'
                  and not s.get('disposition', {}).get('attached_pic')), None)
    audio = next((s for s in streams if s.get('codec_type') == 'audio'), None)
    fmt = data.get('format', {})

    if video is None:
        raise Exception("no video stream found")

    width = int(video.get('width', 0))
    height = int(video.get('height', 0))
    # Rotated phone footage: display size is swapped
    rotation = 0
    if 'rotate' in video.get('tags', {}):
        rotation = int(video['tags']['rotate'])
    for side in video.get('side_data_list', []):
        if 'rotation' in side:
            rotation = int(side['rotation'])
    if abs(rotation) % 180 == 90:
        width, height = height, width

    duration = fmt.get('duration') or video.get('duration')
    info = {
        'width': width,
        'height': height,
        'fps': _parse_fps(video.get('avg_frame_rate')) or _parse_fps(video.get('r_frame_rate')),
        'codec': video.get('codec_name', ''),
        'duration': float(duration) if duration else None,
        'size_mb': Path(video_path).stat().st_size / (1024 * 1024),
        'audio': None,
    }
    if audio is not None:
        info['audio'] = {
            'codec': audio.get('codec_name', ''),
            'sample_rate': int(audio.get('sample_rate', 0) or 0),
        }
    return info


def get_orientation(width, height):
    if width == height:
        return 'square'
    return 'vertical' if height > width else 'horizontal'


def check_resolution(width, height):
    """Returns error text or None"""
    size = (width, height)
    orientation = get_orientation(width, height)

    if orientation == 'square':
        if size in SQUARE_RESOLUTIONS:
            return None
        return f"square resolution {width}x{height} is not accepted (allowed: 1080x1080, 2160x2160)"

    if orientation == 'vertical':
        if size in VERTICAL_RESOLUTIONS:
            return None
        allowed = ', '.join(f"{w}x{h}" for w, h in sorted(VERTICAL_RESOLUTIONS))
        return f"vertical resolution {width}x{height} is not accepted (allowed: {allowed})"

    if size in HORIZONTAL_RESOLUTIONS:
        return None
    if width < MIN_WIDTH:
        return f"width {width}px is below minimum {MIN_WIDTH}px"
    if width > MAX_WIDTH:
        return f"width {width}px is above maximum {MAX_WIDTH}px (DCI 4K)"
    if width / height >= MIN_WIDESCREEN_RATIO - 0.01:
        return None  # wide-screen ratio, e.g. 1920x804 (2.39:1)
    allowed = ', '.join(f"{w}x{h}" for w, h in sorted(HORIZONTAL_RESOLUTIONS))
    return f"horizontal resolution {width}x{height} is not accepted (allowed: {allowed} or wide-screen ratios)"


def check_video_requirements(info, extension):
    """Checks probed video against Adobe Stock requirements.
    Returns (errors, warnings): errors mean the clip will be rejected."""
    errors = []
    warnings = []

    if extension.lower() not in VIDEO_EXTENSIONS:
        errors.append(f"format {extension} is not accepted (allowed: MOV, MPG, MP4)")

    duration = info.get('duration')
    if duration is None:
        errors.append("duration could not be determined")
    elif duration < MIN_DURATION_SEC or duration > MAX_DURATION_SEC:
        errors.append(f"duration {duration:.2f}s is out of range {MIN_DURATION_SEC}-{MAX_DURATION_SEC}s")

    if info['size_mb'] > MAX_FILE_SIZE_MB:
        errors.append(f"file size {info['size_mb']:.0f} MB exceeds {MAX_FILE_SIZE_MB} MB")

    fps = info.get('fps')
    if fps is None:
        errors.append("frame rate could not be determined")
    elif not any(abs(fps - allowed) <= FPS_TOLERANCE for allowed in ALLOWED_FPS):
        errors.append(f"frame rate {fps:.3f} fps is not accepted "
                      f"(allowed: {', '.join(str(f) for f in ALLOWED_FPS)})")

    codec = info.get('codec', '')
    if codec not in ACCEPTED_CODECS:
        errors.append(f"codec {codec} is not accepted (allowed: ProRes, H.264, MPEG-2, MPEG-4, Motion JPEG, PNG)")
    elif codec not in RECOMMENDED_CODECS:
        warnings.append(f"codec {codec} is accepted, but ProRes or H.264 is recommended")

    resolution_error = check_resolution(info['width'], info['height'])
    if resolution_error:
        errors.append(resolution_error)

    audio = info.get('audio')
    if audio:
        if audio['sample_rate'] != AUDIO_SAMPLE_RATE:
            errors.append(f"audio sample rate {audio['sample_rate']} Hz, required {AUDIO_SAMPLE_RATE} Hz")
        if audio['codec'] not in AUDIO_CODECS:
            errors.append(f"audio codec {audio['codec']}, required 16-bit uncompressed PCM")

    if duration is not None and MIN_DURATION_SEC <= duration <= MAX_DURATION_SEC and not 10 <= duration <= 20:
        warnings.append(f"duration {duration:.1f}s: 10-20s is recommended")

    return errors, warnings


def extract_frames(video_path, output_dir, duration, count=3, max_side=1024):
    """Extracts `count` evenly spaced JPEG frames for AI analysis. Returns list of paths."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i in range(count):
        timestamp = duration * (i + 1) / (count + 1)
        frame_path = output_dir / f"frame_{i + 1}.jpg"
        cmd = ['ffmpeg', '-y', '-v', 'error', '-ss', f"{timestamp:.3f}", '-i', str(video_path),
               '-frames:v', '1',
               '-vf', f"scale={max_side}:{max_side}:force_original_aspect_ratio=decrease",
               '-q:v', '3', str(frame_path)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and frame_path.exists():
            frames.append(frame_path)
    return frames
