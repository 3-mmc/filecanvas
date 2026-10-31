"""Thunderbird itself on Linux, macOS and Windows-from-WSL; every profile, process and download
here is synthetic, and nothing is started."""
import hashlib
import io
import json
import subprocess
from pathlib import Path
from unittest.mock import patch
import pytest
from twigs.apps.thunderbird import cli, host


def profile_root(root, default_install=True, extension='enabled'):
    root.mkdir(parents=True)
    (root/'profiles.ini').write_text('[General]\n[Profile0]\nName=default-release\nPath=abc.default-release\n'
                                     '[Profile1]\nName=default\nPath=xyz.default\nDefault=1\n')
    if default_install:
        (root/'installs.ini').write_text('[FDC34C9F024745EB]\nDefault=abc.default-release\nLocked=1\n')
    for folder in ('abc.default-release', 'xyz.default'):
        (root/folder).mkdir()
    if extension:
        addon = dict(id=host.EXTENSION_ID, active=extension == 'enabled', userDisabled=extension != 'enabled')
        (root/'abc.default-release/extensions.json').write_text(json.dumps(dict(addons=[addon])))
    return root


def test_install_default_beats_the_old_default_flag_and_the_extension_decides(tmp_path):
    root = profile_root(tmp_path/'thunderbird')
    with patch.object(host, 'roots', return_value=[(root, 'native')]):
        found = host.profiles()
        assert [(p.name, p.default, p.extension) for p in found] == \
            [('default-release', True, 'enabled'), ('default', False, None)]
        assert host.chosen(found).name == 'default-release'


def test_without_an_install_default_the_old_flag_counts_but_the_extension_still_wins(tmp_path):
    root = profile_root(tmp_path/'thunderbird', default_install=False)
    with patch.object(host, 'roots', return_value=[(root, 'native')]):
        found = host.profiles()
        assert next(p for p in found if p.default).name == 'default'
        assert host.chosen(found).name == 'default-release'


def test_a_disabled_extension_is_reported_as_disabled(tmp_path):
    root = profile_root(tmp_path/'thunderbird', extension='disabled')
    assert host.extension(root/'abc.default-release') == 'disabled'
    assert host.extension(root/'xyz.default') is None


@pytest.mark.parametrize('side,listing,expect', [
    ('linux', '  10 /usr/lib/thunderbird/thunderbird --headless -P default-release\n'
              '  11 /usr/lib/thunderbird/thunderbird -contentproc -isForBrowser\n'
              '  12 /bin/sh /home/me/.local/bin/thunderbird-headless start\n', 'headless'),
    ('linux', ' 20 /app/lib/thunderbird/thunderbird -P default\n', 'window'),
    ('macos', ' 30 /Applications/Thunderbird.app/Contents/MacOS/thunderbird\n', 'window'),
    ('windows', '40 "C:\\Program Files\\Mozilla Thunderbird\\thunderbird.exe" --headless -P "default-release"\r\n'
                '41 "C:\\Program Files\\Mozilla Thunderbird\\thunderbird.exe" -contentproc 40\r\n', 'headless'),
    ('linux', '', 'stopped'),
])
def test_running_state_is_read_from_main_processes_on_each_platform(side, listing, expect):
    with patch.object(host.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, listing, '')) as run:
        assert host.state(side)[0] == expect
    assert run.call_args.args[0][0] == ('powershell.exe' if side == 'windows' else 'ps')


def test_hint_names_the_next_step():
    p = host.Profile('default-release', Path('/p'), Path('/r'), 'native', True, 'enabled')
    with patch.object(host, 'profiles', return_value=[p]), patch.object(host, 'state', return_value=('stopped', [])):
        assert 'twigs thunderbird start' in host.hint()
    with patch.object(host, 'profiles', return_value=[p._replace(extension=None)]):
        assert 'not installed' in host.hint()
    with patch.object(host, 'profiles', return_value=[]):
        assert 'isn\'t set up' in host.hint()
    with patch.object(host, 'profiles', return_value=[p]), patch.object(host, 'state', return_value=('headless', [])):
        assert host.hint() is None


def test_autostart_entries_for_each_platform(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path/'config'))
    p = host.Profile('default-release', Path('/p'), Path('/r'), 'native', True, 'enabled')
    with patch.object(host, 'twigs_command', return_value=['/home/me/bin/twigs']):
        path = host.autostart(p, side='linux')
        assert path == tmp_path/'config/autostart/twigs-thunderbird.desktop'
        assert 'Exec=/home/me/bin/twigs thunderbird start\n' in path.read_text()
        assert host.autostarts('linux')
        host.autostart(p, False, side='linux')
        assert not path.exists()
        plist = host.autostart_text(p, 'macos')
        assert '<string>/home/me/bin/twigs</string>' in plist and '<key>RunAtLoad</key>' in plist
    win = p._replace(kind='windows')
    with patch.object(host, 'executable', return_value=['/mnt/c/Program Files/Mozilla Thunderbird/thunderbird.exe']), \
         patch.object(host.subprocess, 'run',
                      return_value=subprocess.CompletedProcess([], 0, 'C:\\Program Files\\Mozilla Thunderbird\\thunderbird.exe\n', '')):
        vbs = host.autostart_text(win, 'windows')
    assert vbs.startswith('CreateObject("WScript.Shell").Run """C:\\Program Files\\Mozilla Thunderbird\\thunderbird.exe""')
    assert '--headless -P ""default-release""' in vbs and vbs.endswith(', 0, False\r\n')


def test_starting_never_starts_a_second_thunderbird():
    p = host.Profile('default-release', Path('/p'), Path('/r'), 'flatpak', True, 'enabled')
    with patch.object(host, 'state', return_value=('window', [])), patch.object(host, 'launch') as launch:
        assert host.start(p) == 'window'
        launch.assert_not_called()
    with patch.object(host, 'state', return_value=('stopped', [])), patch.object(host, 'launch') as launch, \
         patch.object(host.shutil, 'which', return_value='/usr/bin/flatpak'):
        assert host.start(p) == 'headless'
        assert launch.call_args.args[0] == ['flatpak', 'run', 'org.mozilla.Thunderbird', '--headless', '-P', 'default-release']


def test_stop_leaves_a_window_alone():
    with patch.object(host, 'state', return_value=('window', [(1, 'thunderbird')])), patch.object(host.os, 'kill') as kill:
        with pytest.raises(OSError, match='close it there'):
            host.stop('linux')
        kill.assert_not_called()


def test_extension_is_kept_only_when_its_hash_matches(tmp_path):
    good = b'xpi bytes'
    with patch.object(host, 'EXTENSION_SHA256', hashlib.sha256(good).hexdigest()):
        path = host.fetch_extension(tmp_path, opener=lambda url, timeout: io.BytesIO(good))
        assert path.read_bytes() == good
        # Already there and right: not downloaded again.
        assert host.fetch_extension(tmp_path, opener=lambda *a, **k: pytest.fail('downloaded twice')) == path
    with pytest.raises(OSError, match='does not match'):
        host.fetch_extension(tmp_path/'other', opener=lambda url, timeout: io.BytesIO(b'tampered'))
    assert not (tmp_path/'other').exists()


def test_guide_downloads_only_with_consent_then_waits_for_thunderbird():
    p = host.Profile('default-release', Path('/p'), Path('/r'), 'native', True, None)
    said = []
    with patch.object(host, 'chosen', return_value=p):
        with pytest.raises(OSError, match='Not downloaded'):
            cli.guide({}, ask=lambda q: '', say=said.append)
    class Answers:
        def __init__(self, config, timeout):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def call(self, name):
            return [dict(id='account1', name='me@example.test')]
    with patch.object(host, 'chosen', return_value=p), \
         patch.object(host, 'fetch_extension', return_value=Path('/cache/x.xpi')) as fetch, \
         patch.object(host, 'state', return_value=('stopped', [])), \
         patch.object(host, 'start') as start, \
         patch.object(cli, 'Client', Answers):
        got = cli.guide({}, ask=lambda q: 'y', say=said.append)
    fetch.assert_called_once()
    assert start.call_args.kwargs == dict(headless=False)       # the window: installing needs it
    assert got[0]['id'] == 'account1'
    assert any('Install Add-on From File' in s and '/cache/x.xpi' in s for s in said)


def test_picking_accounts_all_some_or_none():
    accounts = [dict(id='a1', name='One'), dict(id='a2', name='Two')]
    assert cli.pick(accounts, ['a1', 'a2'], ask=lambda q: '', say=lambda s: None) == ['a1', 'a2']
    assert cli.pick(accounts, ['a1', 'a2'], ask=lambda q: 'a2', say=lambda s: None) == ['a2']
    assert cli.pick(accounts, ['a1', 'a2'], ask=lambda q: 'n', say=lambda s: None) == []
    with pytest.raises(ValueError):
        cli.pick(accounts, ['a1'], ask=lambda q: 'a9', say=lambda s: None)
