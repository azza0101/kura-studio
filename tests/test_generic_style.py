import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import server

class GenericStyleTests(unittest.TestCase):
    def test_generic_style_preserves_eyes_and_has_no_giulia(self):
        generic = server.GENERIC_FIXED_PROMPT
        self.assertNotIn('giulia', generic.lower())
        self.assertIn('NO visible white sclera', generic)
        self.assertIn('style reference', generic.lower())
        self.assertIn('characters', generic.lower())
