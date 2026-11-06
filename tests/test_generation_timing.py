import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import server


class GenerationTimingTests(unittest.TestCase):
    def test_timing_is_stored_and_read_for_the_output_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'KuraStudio').mkdir()
            video = root / 'KuraStudio' / 'video_01-audio.mp4'
            video.write_bytes(b'video')
            job = {'started_at': 100.0, 'result': {'filename': video.name, 'subfolder': 'KuraStudio'}}
            with patch.object(server, 'OUT_DIR', root), patch.object(server.time, 'monotonic', return_value=175.0):
                server.finish_timing(job)
                self.assertEqual(job['elapsed_seconds'], 75.0)
                self.assertEqual(server.read_timing(video), 75.0)

    def test_existing_video_has_unknown_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(server.read_timing(Path(tmp) / 'old.mp4'))
