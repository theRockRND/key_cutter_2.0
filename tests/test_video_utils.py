"""Tests for Adobe Stock video requirements check (video_utils.py)"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import video_utils
from video_utils import (check_resolution, check_video_requirements, extract_frames, get_orientation,
                         is_video_file, probe_video)

FFMPEG_AVAILABLE = shutil.which('ffmpeg') is not None and shutil.which('ffprobe') is not None


def make_info(**overrides):
    """Probed info of a valid clip: 1920x1080, 25 fps, H.264, 12 s, no audio"""
    info = {'width': 1920, 'height': 1080, 'fps': 25.0, 'codec': 'h264',
            'duration': 12.0, 'size_mb': 100.0, 'audio': None}
    info.update(overrides)
    return info


class ParseHelpersTest(unittest.TestCase):
    def test_parse_fps(self):
        self.assertAlmostEqual(video_utils._parse_fps('30000/1001'), 29.97, places=2)
        self.assertEqual(video_utils._parse_fps('25/1'), 25.0)
        self.assertEqual(video_utils._parse_fps('24'), 24.0)
        self.assertIsNone(video_utils._parse_fps('0/0'))
        self.assertIsNone(video_utils._parse_fps(''))
        self.assertIsNone(video_utils._parse_fps('25/0'))

    def test_is_video_file(self):
        for name in ['a.mov', 'a.MP4', 'a.mpg', 'a.mpeg']:
            self.assertTrue(is_video_file(name), name)
        for name in ['a.jpg', 'a.avi', 'a.mkv', 'mov']:
            self.assertFalse(is_video_file(name), name)

    def test_get_orientation(self):
        self.assertEqual(get_orientation(1920, 1080), 'horizontal')
        self.assertEqual(get_orientation(1080, 1920), 'vertical')
        self.assertEqual(get_orientation(1080, 1080), 'square')


class ResolutionTest(unittest.TestCase):
    def test_accepted_resolutions(self):
        sizes = [(1920, 1080), (2048, 1080), (3840, 2160), (4096, 2160), (4096, 2304),
                 (1080, 1920), (2160, 3840), (2160, 4096), (2304, 4096),
                 (1080, 1080), (2160, 2160),
                 (1920, 804), (3840, 1608)]  # wide-screen 2.39:1
        for width, height in sizes:
            self.assertIsNone(check_resolution(width, height), f"{width}x{height}")

    def test_rejected_resolutions(self):
        sizes = [(1280, 720), (5120, 2880), (1920, 1440), (720, 1280), (1440, 2560), (1600, 1600)]
        for width, height in sizes:
            self.assertIsNotNone(check_resolution(width, height), f"{width}x{height}")


class RequirementsTest(unittest.TestCase):
    def test_valid_clip(self):
        self.assertEqual(check_video_requirements(make_info(), '.mov'), ([], []))

    def test_format(self):
        errors, _ = check_video_requirements(make_info(), '.avi')
        self.assertTrue(any('format' in e for e in errors))

    def test_duration_limits(self):
        for duration in [5.0, 60.0]:
            errors, _ = check_video_requirements(make_info(duration=duration), '.mp4')
            self.assertEqual(errors, [], duration)
        for duration in [4.9, 60.1, None]:
            errors, _ = check_video_requirements(make_info(duration=duration), '.mp4')
            self.assertTrue(any('duration' in e for e in errors), duration)

    def test_recommended_duration_is_warning_only(self):
        errors, warnings = check_video_requirements(make_info(duration=7.8), '.mp4')
        self.assertEqual(errors, [])
        self.assertTrue(any('10-20s' in w for w in warnings))

    def test_file_size(self):
        self.assertEqual(check_video_requirements(make_info(size_mb=3900), '.mov')[0], [])
        errors, _ = check_video_requirements(make_info(size_mb=3901), '.mov')
        self.assertTrue(any('3900 MB' in e for e in errors))

    def test_frame_rates(self):
        for fps in [23.976, 24, 25, 29.97, 30, 50, 59.94, 60, 30000 / 1001, 60000 / 1001]:
            self.assertEqual(check_video_requirements(make_info(fps=fps), '.mov')[0], [], fps)
        for fps in [15, 48, 120, None]:
            errors, _ = check_video_requirements(make_info(fps=fps), '.mov')
            self.assertTrue(any('frame rate' in e for e in errors), fps)

    def test_codecs(self):
        for codec in ['h264', 'prores']:
            self.assertEqual(check_video_requirements(make_info(codec=codec), '.mov'), ([], []), codec)
        for codec in ['mpeg2video', 'mpeg4', 'mjpeg', 'png']:
            errors, warnings = check_video_requirements(make_info(codec=codec), '.mov')
            self.assertEqual(errors, [], codec)
            self.assertTrue(any('recommended' in w for w in warnings), codec)
        for codec in ['hevc', 'vp9', 'av1']:
            errors, _ = check_video_requirements(make_info(codec=codec), '.mov')
            self.assertTrue(any('codec' in e for e in errors), codec)

    def test_audio(self):
        ok = {'codec': 'pcm_s16le', 'sample_rate': 48000}
        self.assertEqual(check_video_requirements(make_info(audio=ok), '.mov')[0], [])
        errors, _ = check_video_requirements(make_info(audio={'codec': 'aac', 'sample_rate': 44100}), '.mp4')
        self.assertTrue(any('sample rate' in e for e in errors))
        self.assertTrue(any('PCM' in e for e in errors))

    def test_resolution_error_is_reported(self):
        errors, _ = check_video_requirements(make_info(width=1280, height=720), '.mp4')
        self.assertTrue(any('1280' in e for e in errors))


@unittest.skipUnless(FFMPEG_AVAILABLE, "ffmpeg/ffprobe not installed")
class FfmpegIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.vertical = cls.dir / 'vertical.mov'
        cls.with_audio = cls.dir / 'audio.mov'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=size=1080x1920:rate=30',
                        '-t', '6', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(cls.vertical)], check=True)
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=size=1920x1080:rate=30000/1001',
                        '-f', 'lavfi', '-i', 'sine=r=48000', '-t', '6', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                        '-c:a', 'pcm_s16le', str(cls.with_audio)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_probe_vertical(self):
        info = probe_video(self.vertical)
        self.assertEqual((info['width'], info['height']), (1080, 1920))
        self.assertAlmostEqual(info['fps'], 30.0, places=2)
        self.assertEqual(info['codec'], 'h264')
        self.assertAlmostEqual(info['duration'], 6.0, places=1)
        self.assertIsNone(info['audio'])
        self.assertEqual(check_video_requirements(info, '.mov')[0], [])

    def test_probe_audio(self):
        info = probe_video(self.with_audio)
        self.assertAlmostEqual(info['fps'], 29.97, places=2)
        self.assertEqual(info['audio'], {'codec': 'pcm_s16le', 'sample_rate': 48000})
        self.assertEqual(check_video_requirements(info, '.mov')[0], [])

    def test_probe_invalid_file(self):
        broken = self.dir / 'broken.mp4'
        broken.write_bytes(b'not a video')
        with self.assertRaises(Exception):
            probe_video(broken)

    def test_extract_frames(self):
        out = self.dir / 'frames'
        frames = extract_frames(self.vertical, out, duration=6.0, count=3, max_side=512)
        self.assertEqual(len(frames), 3)
        from PIL import Image
        for frame in frames:
            with Image.open(frame) as img:
                self.assertEqual(max(img.size), 512)
                self.assertGreater(img.size[1], img.size[0])  # stays vertical


if __name__ == '__main__':
    unittest.main()
