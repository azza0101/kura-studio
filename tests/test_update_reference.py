import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import updater


class UpdateReferenceTests(unittest.TestCase):
    def test_archive_update_installs_reference_and_keeps_configuration(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.writestr('kura-repo/app/assets/giulia-reference.jpg', b'reference')
            bundle.writestr('kura-repo/config.json', 'should not overwrite')
        commit = json.dumps({'sha': 'abc', 'commit': {'message': 'update', 'committer': {'date': '2026-11-06'}}}).encode()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'config.json'
            config.write_text('{"update_repo":"azza0101/kura-studio"}', encoding='utf8')
            with patch.object(updater, 'ROOT', root), patch.object(updater, 'STATE', root / 'app_version.json'), patch.object(updater, '_has_git', return_value=False), patch.object(updater, '_api', side_effect=[io.BytesIO(archive.getvalue()), io.BytesIO(commit)]):
                updater.apply()
            self.assertEqual((root / 'app/assets/giulia-reference.jpg').read_bytes(), b'reference')
            self.assertEqual(json.loads(config.read_text())['update_repo'], 'azza0101/kura-studio')
