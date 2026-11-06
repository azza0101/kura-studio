import unittest
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def test_launcher_uses_configured_engine_and_has_bounded_wait(self):
        bat = (ROOT / 'start.bat').read_text(encoding='utf8')
        script = (ROOT / 'tools/Avvia-Kura.ps1').read_text(encoding='utf8')
        self.assertIn('Avvia-Kura.ps1', bat)
        self.assertIn('engine_url', script)
        self.assertIn('engine_dir', script)
        self.assertIn('TimeoutSecondi', script)
        self.assertIn('RedirectStandardError', script)
        self.assertIn('ExitCode', script)

    @unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows PowerShell required')
    def test_engine_crash_is_reported_instead_of_waiting_forever(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = root / 'Fake Comfy'
            engine.mkdir()
            (engine / 'main.py').write_text("import sys\nsys.stderr.write('RuntimeError: No CUDA GPUs are available\\n')\nsys.exit(7)\n", encoding='utf8')
            tools = root / 'tools'
            tools.mkdir()
            shutil.copy2(ROOT / 'tools/Avvia-Kura.ps1', tools / 'Avvia-Kura.ps1')
            (root / 'config.json').write_text(json.dumps({'engine_dir': str(engine), 'engine_url': 'http://127.0.0.1:38473', 'port': 38474}), encoding='utf8')
            process = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(tools / 'Avvia-Kura.ps1'), '-TimeoutSecondi', '8'], capture_output=True, text=True, timeout=35)
            self.assertNotEqual(process.returncode, 0)
            self.assertIn('No CUDA GPUs are available', process.stdout)
            self.assertIn('La GPU CUDA non risulta disponibile', process.stdout)
            self.assertIn('codice 7', process.stdout)
