import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import diagnostics


class DiagnosticsTests(unittest.TestCase):
    def test_only_allowed_events_and_no_sensitive_values(self):
        diagnostics.EVENTS.clear()
        diagnostics.record('generation_error')
        diagnostics.record('generation_error', 'ValueError', 'KSampler')
        diagnostics.record('secret prompt C:\\private\\image.png')
        with patch('diagnostics.platform.system', return_value='Windows'), patch('diagnostics.platform.release', return_value='11'):
            report = diagnostics.report('v2', True, ['secret path'])
        self.assertIn('generation_error', report)
        self.assertIn('ValueError', report)
        self.assertIn('KSampler', report)
        self.assertNotIn('secret', report)
        self.assertNotIn('image.png', report)
        self.assertIn('Nodi mancanti: 1', report)

    def test_missing_gh_is_explained(self):
        with patch('diagnostics.shutil.which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'GitHub CLI'):
                diagnostics.send('report')
