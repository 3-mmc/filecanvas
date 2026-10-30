"""Dependency isolation and diagnostics, including source and installed launchers."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import tests
from twigs.kit import doctor

ROOT = Path(__file__).resolve().parents[1]


class Isolation(unittest.TestCase):
    def run_without_site_packages(self, code):
        got = subprocess.run([sys.executable, '-S', '-c', code], cwd=ROOT,
                             capture_output=True, text=True, timeout=15)
        self.assertEqual(got.returncode, 0, got.stderr)
        return got.stdout

    def test_chat_and_task_desk_work_without_any_external_python_packages(self):
        self.run_without_site_packages('''
import sys
from twigs.apps.msg import chat
from twigs.apps.tasks.model.store import Store
from twigs.apps.tasks.canvas import Desk
s = Store(':memory:')
s.seed_demo()
p = Desk(s, watch=0)
assert any(n.kind == 'today' for n in p.root().load())
assert hasattr(s, 'set_due')
assert not {'notmuch2', 'tuido', 'textual'} & sys.modules.keys()
s.close()
''')

    def test_configured_mail_missing_its_binding_does_not_remove_chats(self):
        self.run_without_site_packages('''
from twigs.apps.msg import cli, accounts, chat
cli.configured = lambda: [object()]
accounts.NOTMUCH = __file__ if '__file__' in globals() else '/dev/null'
accounts.load = lambda: ({}, [])
accounts.chat_settings = lambda: dict(enabled=True, nchat='nchat', confdir=None,
                                     read_receipts=True, typing=True, notify=False)
cli.shutil.which = lambda name: '/test/nchat'
chat.has_profiles = lambda conf: True
chat.Chat = lambda *a, **kw: 'chat provider'
assert cli.sections() == [('Messages', 'chat provider')]
''')

    def test_packaged_install_selects_twigs_without_replacing_console_scripts(self):
        from twigs import __main__ as cli
        with patch.object(cli, 'LAUNCHER', '/not/a/source/checkout/twigs-launcher'), \
             patch.object(cli, 'link') as link, patch.object(cli.apps, 'keep') as keep, \
             contextlib.redirect_stdout(io.StringIO()):
            cli.install(['files', 'tasks'])
        link.assert_not_called()
        keep.assert_called_once_with(['files', 'tasks'])


class Diagnostics(unittest.TestCase):
    def test_platform_detection_for_macos_and_linux_derivatives(self):
        self.assertEqual(doctor.detect('darwin'), 'macos')
        self.assertEqual(doctor.detect('linux', {'ID': 'ubuntu', 'ID_LIKE': 'debian'}), 'ubuntu')
        self.assertEqual(doctor.detect('linux', {'ID': 'endeavouros', 'ID_LIKE': 'arch'}), 'arch')
        self.assertEqual(doctor.detect('linux', {'ID': 'debian'}), 'debian')

    def test_platform_guidance_and_python_isolation_are_explicit(self):
        for where, command in [('macos', 'brew install'), ('arch', 'pacman -S'), ('ubuntu', 'apt install')]:
            report = doctor.diagnose(['mail'], where, command=lambda n: None,
                                     module=lambda n: False, probe=False)
            hint = next(c['hint'] for c in report['checks'] if c['name'] == 'notmuch2')
            self.assertIn(command, hint)
            self.assertIn('--system-site-packages', hint)
            self.assertNotIn('sudo pip', hint)

    def test_optional_missing_dependencies_only_fail_when_requested(self):
        with patch.object(doctor.shutil, 'which', return_value=None), \
             patch.object(doctor, 'importable', return_value=False):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(doctor.main(['--json']), 0)
            report = json.loads(out.getvalue())
            self.assertFalse(next(c['ok'] for c in report['checks'] if c['name'] == 'notmuch2'))
            self.assertTrue(next(c['ok'] for c in report['checks'] if c['feature'] == 'tasks'))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(doctor.main(['mail']), 1)
                self.assertEqual(doctor.main(['tasks']), 0)

    def test_broken_native_binding_is_reported_as_unavailable(self):
        with patch.object(doctor.importlib, 'import_module', side_effect=OSError('library unavailable')):
            self.assertFalse(doctor.importable('notmuch2'))

    def test_nchat_without_serve_is_not_reported_ready(self):
        fake = subprocess.CompletedProcess([], 0, 'nchat help, without the serve extension', '')
        with patch.object(doctor.subprocess, 'run', return_value=fake):
            report = doctor.diagnose(['chat'], 'macos', command=lambda n: '/bin/nchat')
        capability = next(c for c in report['checks'] if c['name'] == 'nchat --serve capability')
        self.assertFalse(capability['ok'])
        self.assertIn('3-mmc/nchat', capability['hint'])
