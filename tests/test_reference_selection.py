import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import server

class ReferenceSelectionTests(unittest.TestCase):
    def test_builtin_references_are_explicit_and_bundled(self):
        self.assertEqual(set(server.BUILTIN_REFERENCES), {'giulia', 'generic'})
        for path in server.BUILTIN_REFERENCES.values():
            self.assertTrue(path.is_file())
    def test_unknown_reference_is_rejected(self):
        with self.assertRaises(ValueError):
            server.reference_path('../config.json')
