"""Exercise the workflow's release guard without GitHub credentials or uploads."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ReleaseEndToEnd(unittest.TestCase):
    """Check manual-build isolation and run the production Bash guard locally."""

    def test_release_version_and_publishing_boundary(self):
        path = ROOT / '.github/workflows/release.yml'
        self.assertTrue(path.is_file(), 'A release workflow is required.')
        workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        self.assertIn('workflow_dispatch', workflow['on'])
        self.assertEqual(workflow['on']['release']['types'], ['published'])
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        build, publish = (workflow['jobs'][name] for name in ('build', 'publish'))
        self.assertEqual(publish['if'], "github.event_name == 'release'")
        self.assertEqual(publish['needs'], 'build')
        self.assertEqual(publish['environment']['name'], 'pypi')
        self.assertEqual(publish['permissions'], {'id-token': 'write'})
        self.assertNotIn('permissions', build)
        self.assertFalse(any('checkout@' in step.get('uses', '') for step in publish['steps']))
        guard, = (step for step in build['steps'] if step.get('name') == 'Check release version')
        self.assertEqual(guard['if'], "github.event_name == 'release'")
        self.assertEqual(guard['env']['RELEASE_TAG'], '${{ github.event.release.tag_name }}')
        cases = {}
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            uv = base / 'uv'
            uv.write_text('#!/bin/bash\n[[ "$1 $2" == "version --short" ]] || exit 2\n[[ "$TEST_VERSION" != failed ]] || exit 3\nprintf "%s\\n" "$TEST_VERSION"\n')
            uv.chmod(0o755)
            for name, tag, version, expected in (
                ('matching', 'v0.1.0', '0.1.0', 0),
                ('mismatch', 'v0.2.0', '0.1.0', 1),
                ('missing', '', '0.1.0', 1),
                ('literal_tag', 'v0.1.0$(touch injected)', '0.1.0', 1),
                ('version_failure', 'v0.1.0', 'failed', 3),
            ):
                environment = {**os.environ, 'PATH': str(base) + os.pathsep + os.defpath,
                               'RELEASE_TAG': tag, 'TEST_VERSION': version}
                result = subprocess.run(['/bin/bash', '-e', '-c', guard['run']], cwd=base,
                                        env=environment, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertFalse((base / 'injected').exists())
                cases[name] = {'exit_code': result.returncode, 'stderr': result.stderr}
        (ROOT / '.build').mkdir(exist_ok=True)
        (ROOT / '.build/release-workflow-e2e.json').write_text(json.dumps(cases, indent=2))
