import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FrontendTests(unittest.TestCase):
    def test_manual_duration_accepts_whole_and_decimal_seconds(self):
        html = (ROOT / 'app/web/index.html').read_text(encoding='utf-8')
        tag = re.search(r'<input\s+[^>]*id="seconds"[^>]*>', html).group()
        self.assertIn('step="any"', tag)

    def test_script_has_valid_javascript(self):
        html = (ROOT / 'app/web/index.html').read_text(encoding='utf-8')
        script = re.search(r'<script>(.*?)</script>', html, re.S).group(1)
        result = subprocess.run(['node', '--check'], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
