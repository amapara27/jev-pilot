"""Exercise installed CLI metadata, user configuration and reports outside the checkout."""

import configparser
from contextlib import chdir
from email.parser import Parser
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from packaging.requirements import Requirement

from app.cli import main
from app.config import ConfigurationError, load_api_key
from test_cli_e2e import FakeDesktop, FakeJev
from test_speech_e2e import FakeMicrophone, FakeSpeechModel


ROOT = Path(__file__).resolve().parents[1]


class PackagingEndToEnd(unittest.TestCase):
    """Use real builds/entry points and synthetic desktop/audio/provider services."""

    def test_configuration_and_reports_are_independent_of_working_directory(self):
        cases = {}
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            home, installed, working, checkout = (base / name for name in ('home', 'site-packages', 'working', 'checkout'))
            for path in (home, installed, working, checkout):
                path.mkdir()
            data = home / 'Library/Application Support/Jev Pilot'
            data.mkdir(parents=True)
            (data / '.env').write_text('TYPESAFE_API_KEY=user-fixture\n')
            for path in (installed, working):
                (path / '.env').write_text('TYPESAFE_API_KEY=unrelated-fixture\n')
            with patch.dict(os.environ, {}, clear=True), patch('app.config.Path.home', return_value=home), patch('app.config.repository_root', return_value=installed), chdir(working):
                self.assertEqual(load_api_key(), 'user-fixture')
                with patch.dict(os.environ, {'TYPESAFE_API_KEY': ' environment-fixture '}):
                    self.assertEqual(load_api_key(), 'environment-fixture')
                for voice in (False, True):
                    output = io.StringIO()
                    argv = ['--listen', '--silence-ms', '200'] if voice else ['switch to Terminal']
                    status = main([*argv, '--dry-run'], desktop=FakeDesktop(), client=FakeJev(), output=output,
                                  speech_model=FakeSpeechModel(), audio_source_factory=FakeMicrophone)
                    self.assertEqual(status, 0, output.getvalue())
                    report = json.loads((data / 'Reports/python-cli-last.json').read_text())
                    self.assertEqual(report['runs'][0]['outcome'] if voice else report['outcome'], 'dry_run')
                    self.assertFalse((working / '.build').exists())
                    cases['voice' if voice else 'text'] = report
                explicit = working / 'custom/session.json'
                main(['switch to Terminal', '--dry-run', '--report', str(explicit)], desktop=FakeDesktop(), client=FakeJev(), output=io.StringIO())
                self.assertEqual(json.loads(explicit.read_text())['outcome'], 'dry_run')
                (checkout / 'pyproject.toml').write_text('[project]\nname="jevpilot"\n')
                (checkout / '.env').write_text('TYPESAFE_API_KEY=checkout-fixture\n')
                with patch('app.config.repository_root', return_value=checkout):
                    self.assertEqual(load_api_key(), 'checkout-fixture')
                self.assertEqual(load_api_key(checkout), 'checkout-fixture')
                (data / '.env').unlink()
                with self.assertRaises(ConfigurationError) as caught:
                    load_api_key()
                self.assertIn(str(data / '.env'), str(caught.exception))
                self.assertNotIn('fixture', json.dumps(cases))
        (ROOT / '.build').mkdir(exist_ok=True)
        (ROOT / '.build/installed-paths-e2e.json').write_text(json.dumps(cases, indent=2))

    def test_builds_and_installs_a_cli_without_checkout_files(self):
        self.assertTrue((ROOT / 'pyproject.toml').is_file(), 'Package metadata is required.')
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dist, target, working = (base / name for name in ('dist', 'installed', 'working'))
            dist.mkdir()
            working.mkdir()

            def run(arguments, cwd=ROOT, env=None):
                result = subprocess.run(arguments, cwd=cwd, env=env, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                return result.stdout

            for operation in ('build_sdist', 'build_wheel'):
                run([sys.executable, '-c', f'from setuptools.build_meta import {operation}; import sys; {operation}(sys.argv[1])', str(dist)])
            wheel, = dist.glob('*.whl')
            source, = dist.glob('*.tar.gz')
            with zipfile.ZipFile(wheel) as archive:
                files = archive.namelist()
                self.assertTrue(all(name.startswith(('app/', 'jevpilot-0.1.0.dist-info/')) for name in files))
                metadata = Parser().parsestr(archive.read('jevpilot-0.1.0.dist-info/METADATA').decode())
                self.assertEqual(metadata['Name'], 'jevpilot')
                self.assertEqual(metadata['Version'], '0.1.0')
                self.assertEqual(metadata['Requires-Python'], '>=3.12')
                self.assertEqual({str(Requirement(item)) for item in metadata.get_all('Requires-Dist')},
                                 {str(Requirement(item)) for item in (ROOT / 'requirements.txt').read_text().splitlines() if item.strip()})
                entries = configparser.ConfigParser()
                entries.read_string(archive.read('jevpilot-0.1.0.dist-info/entry_points.txt').decode())
                self.assertEqual(dict(entries['console_scripts']), {'jevpilot': 'app.cli:main'})
            with tarfile.open(source) as archive:
                source_files = archive.getnames()
                self.assertTrue(any(name.endswith('/requirements.txt') for name in source_files))
                self.assertFalse(any(part in {'.env', '.build', '.codex', '.git'} or part.endswith('.env') for name in source_files for part in Path(name).parts))
                archive.extractall(base / 'source', filter='data')
            source_root, = (base / 'source').iterdir()
            rebuilt = base / 'rebuilt'
            rebuilt.mkdir()
            run([sys.executable, '-c', 'from setuptools.build_meta import build_wheel; import sys; build_wheel(sys.argv[1])', str(rebuilt)], cwd=source_root)
            run([sys.executable, '-m', 'pip', 'install', '--no-deps', '--no-index', '--target', str(target), str(wheel)])
            environment = {**os.environ, 'PYTHONPATH': str(target)}
            help_text = run([str(target / 'bin/jevpilot'), '--help'], cwd=working, env=environment)
            self.assertIn('--listen', help_text)
            self.assertIn('--report', help_text)
            run([sys.executable, '-c', 'import app, sys; from pathlib import Path; assert Path(app.__file__).is_relative_to(Path(sys.argv[1]))', str(target)], cwd=working, env=environment)
            self.assertIn('--listen', run([sys.executable, '-m', 'app', '--help'], cwd=working, env=environment))
            artifacts = ROOT / '.build/packaging-dist'
            artifacts.mkdir(parents=True, exist_ok=True)
            for path in (wheel, source):
                shutil.copy2(path, artifacts / path.name)
            (ROOT / '.build/packaging-e2e.json').write_text(json.dumps({'version': metadata['Version'], 'wheel': wheel.name, 'source': source.name, 'wheel_files': files, 'source_rebuild': True, 'installed_help': True}, indent=2))
