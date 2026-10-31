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
    with patch.object(host, 'executable', return_value=['/usr/bin/thunderbird']):
        path = host.autostart(p, side='linux')
        assert path == tmp_path/'config/autostart/twigs-thunderbird.desktop'
        assert 'Exec=/usr/bin/thunderbird --headless --no-remote -profile /p\n' in path.read_text()
        assert host.autostarts('linux')
        host.autostart(p, False, side='linux')
        assert not path.exists()
        plist = host.autostart_text(p, 'macos')
        assert '<string>/usr/bin/thunderbird</string>' in plist and '<key>RunAtLoad</key>' in plist
    win = p._replace(kind='windows')
    with patch.object(host, 'executable', return_value=['/mnt/c/Program Files/Mozilla Thunderbird/thunderbird.exe']), \
         patch.object(host.subprocess, 'run',
                      return_value=subprocess.CompletedProcess([], 0, 'C:\\Program Files\\Mozilla Thunderbird\\thunderbird.exe\n', '')):
        vbs = host.autostart_text(win, 'windows')
    assert vbs.startswith('CreateObject("WScript.Shell").Run """C:\\Program Files\\Mozilla Thunderbird\\thunderbird.exe""')
    assert '--headless --no-remote -profile ""C:\\Program Files' in vbs and vbs.endswith(', 0, False\r\n')


def test_starting_never_starts_a_second_thunderbird():
    p = host.Profile('default-release', Path('/p'), Path('/r'), 'flatpak', True, 'enabled')
    with patch.object(host, 'state', return_value=('window', [])), patch.object(host, 'launch') as launch:
        assert host.start(p) == 'window'
        launch.assert_not_called()
    with patch.object(host, 'state', return_value=('stopped', [])), patch.object(host, 'launch') as launch, \
         patch.object(host.shutil, 'which', return_value='/usr/bin/flatpak'):
        assert host.start(p) == 'headless'
        assert launch.call_args.args[0] == ['flatpak', 'run', 'org.mozilla.Thunderbird', '--headless', '--no-remote', '-P', 'default-release']


def test_stop_leaves_a_window_alone():
    with patch.object(host, 'state', return_value=('window', [(1, 'thunderbird')])), patch.object(host.os, 'kill') as kill:
        with pytest.raises(OSError, match='close it there'):
            host.stop('linux', profile=host.Profile('default', Path('/p'), Path('/r'), 'native', True, 'enabled'))
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


def sample_profile(path=Path('/profiles/Mail Profile'), kind='native'):
    return host.Profile('mail', path, path.parent, kind, True, 'enabled')


@pytest.mark.parametrize('kind,raw,translated', [
    ('windows', r'D:\Mail\my profile', '/mnt/d/Mail/my profile'),
    ('windows', r'\\server\share\profile', '/mnt/share/profile'),
])
def test_windows_absolute_profile_paths_are_translated(tmp_path, kind, raw, translated):
    (tmp_path/'profiles.ini').write_text('[Profile0]\nName=mail\nIsRelative=0\nPath='+raw+'\nDefault=1\n')
    with patch.object(host, 'roots', return_value=[(tmp_path, kind)]), \
         patch.object(host, 'translated_path', return_value=translated) as translate:
        p = host.chosen(host.profiles('windows'))
    assert p.path == Path(translated)
    translate.assert_called_once_with(raw)


def test_windows_relative_profile_backslashes_are_separators(tmp_path):
    (tmp_path/'profiles.ini').write_text('[Profile0]\nName=mail\nPath=Profiles\\abc.mail\n')
    with patch.object(host, 'roots', return_value=[(tmp_path, 'windows')]):
        assert host.profiles('windows')[0].path == tmp_path/'Profiles/abc.mail'


def test_windows_executable_uses_registry_on_another_drive(tmp_path, monkeypatch):
    monkeypatch.delenv('TWIGS_THUNDERBIRD_EXE', raising=False)
    exe = tmp_path/'Thunderbird/thunderbird.exe'
    exe.parent.mkdir()
    exe.touch()
    output = subprocess.CompletedProcess([], 0, 'D:\\Apps\\Thunderbird\\thunderbird.exe\n', '')
    with patch.object(host.shutil, 'which', return_value=None), \
         patch.object(host.subprocess, 'run', return_value=output) as run, \
         patch.object(host, 'translated_path', return_value=str(exe)):
        assert host.executable(sample_profile(kind='windows')) == [str(exe)]
    assert 'App Paths' in run.call_args.args[0][-1]
    assert 'HKCU:' in run.call_args.args[0][-1]


def test_explicit_windows_executable_supports_portable_install(tmp_path, monkeypatch):
    exe = tmp_path/'Portable Mail/thunderbird.exe'
    exe.parent.mkdir()
    exe.touch()
    monkeypatch.setenv('TWIGS_THUNDERBIRD_EXE', str(exe))
    assert host.windows_executable() == str(exe)
    monkeypatch.setenv('TWIGS_THUNDERBIRD_EXE', str(tmp_path/'missing.exe'))
    with pytest.raises(OSError, match='TWIGS_THUNDERBIRD_EXE'):
        host.windows_executable()


@pytest.mark.parametrize('side,args,expected', [
    ('macos', '/Applications/Thunderbird.app/Contents/MacOS/thunderbird --headless -profile /profiles/Mail Profile', True),
    ('macos', 'thunderbird --headless -profile /profiles/Other Profile', False),
    ('macos', 'thunderbird --headless -P mail', True),
    ('macos', 'thunderbird --headless -P unrelated', False),
    ('macos', 'thunderbird', None),
    ('windows', r'"D:\Apps\thunderbird.exe" --headless -profile "D:\Mail\Profile"', True),
    ('windows', r'"D:\Apps\thunderbird.exe" --headless -profile "D:\Other\Profile"', False),
])
def test_profile_matching_respects_paths_names_and_unknown_launches(side, args, expected):
    with patch.object(host, 'profiles', return_value=[sample_profile()]), \
         patch.object(host, 'translated_path', return_value=r'D:\Mail\Profile'):
        assert host.profile_match(999999, args, sample_profile(), side) is expected


def test_duplicate_profile_names_are_ambiguous():
    p = sample_profile()
    with patch.object(host, 'profiles', return_value=[p, p._replace(path=Path('/another/profile'))]):
        assert host.profile_match(999999, 'thunderbird -P mail', p, 'macos') is None


def test_stop_signals_only_the_selected_profile():
    p = sample_profile()
    own = (999991, 'thunderbird --headless -profile /profiles/Mail Profile')
    other = (999992, 'thunderbird --headless -profile /profiles/Other Profile')
    with patch.object(host, 'processes', side_effect=[[own, other], [other]]), \
         patch.object(host.os, 'kill') as kill, patch.object(host.time, 'sleep'):
        host.stop('linux', profile=p)
    kill.assert_called_once_with(own[0], host.signal.SIGTERM)


@pytest.mark.parametrize('operation', ['start', 'stop', 'window'])
def test_ambiguous_profile_never_authorizes_process_control(operation):
    p = sample_profile()
    with patch.object(host, 'processes', return_value=[(999999, 'thunderbird --headless')]), \
         patch.object(host, 'launch') as launch, patch.object(host.os, 'kill') as kill:
        with pytest.raises(OSError, match='Cannot identify'):
            if operation == 'stop':
                host.stop('linux', profile=p)
            else:
                getattr(host, operation)(p, side='linux')
    launch.assert_not_called()
    kill.assert_not_called()


def test_another_profile_does_not_prevent_starting_selected_profile():
    p = sample_profile()
    with patch.object(host, 'processes', return_value=[(999999, 'thunderbird --headless -profile /other')]), \
         patch.object(host, 'executable', return_value=['/usr/bin/thunderbird']), \
         patch.object(host, 'launch') as launch:
        assert host.start(p, side='linux') == 'headless'
    assert launch.call_args.args[0] == ['/usr/bin/thunderbird', '--headless', '--no-remote', '-profile', str(p.path)]


def test_unix_profile_lock_identifies_gui_without_profile_argument(tmp_path):
    p = sample_profile(path=tmp_path)
    (tmp_path/'lock').symlink_to('127.0.0.1:+999999')
    assert host.profile_match(999999, 'thunderbird', p, 'macos') is True


@pytest.mark.parametrize('result', [OSError('ps unavailable'), subprocess.CompletedProcess([], 1, '', 'denied')])
def test_process_inspection_failure_is_not_reported_as_stopped(result):
    kwargs = {'side_effect': result} if isinstance(result, Exception) else {'return_value': result}
    with patch.object(host.subprocess, 'run', **kwargs), pytest.raises(OSError, match='Cannot inspect'):
        host.state('linux', profile=sample_profile())


def test_gui_roundtrip_uses_same_binary_and_profile_without_twigs(tmp_path):
    # Exercise the actual wrapper with a fake Thunderbird, never the installed app.
    import os
    exe = tmp_path/'Fake Thunderbird'
    log = tmp_path/'calls'
    exe.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n')
    exe.chmod(0o755)
    p = sample_profile()
    with patch.object(host, 'state', return_value=('stopped', [])), \
         patch.object(host, 'executable', return_value=[str(exe)]), \
         patch.object(host, 'launch') as launch:
        host.window(p, side='macos')
    command = launch.call_args.args[0]
    subprocess.run(command, env={**os.environ, 'CALL_LOG': str(log)}, check=True)
    assert log.read_text().splitlines() == [
        '--no-remote -profile /profiles/Mail Profile',
        '--no-remote -profile /profiles/Mail Profile --headless']


def test_gui_failure_does_not_spawn_headless(tmp_path):
    exe = tmp_path/'fake'
    exe.write_text('#!/bin/sh\nexit 1\n')
    exe.chmod(0o755)
    with patch.object(host, 'state', return_value=('stopped', [])), \
         patch.object(host, 'executable', return_value=[str(exe)]), patch.object(host, 'launch') as launch:
        host.window(sample_profile(), side='macos')
    assert subprocess.run(launch.call_args.args[0]).returncode == 1


def test_linux_exec_quoting_roundtrips_special_characters_through_glib():
    # Exercise a real desktop-entry consumer without a Python GI dependency.
    import ctypes as c
    import ctypes.util
    import tempfile
    import time
    libraries = [ctypes.util.find_library(n) for n in ('glib-2.0', 'gio-2.0', 'gobject-2.0')]
    if not all(libraries):
        pytest.skip('GLib desktop launcher is not installed')
    glib, gio, gobject = [c.CDLL(n) for n in libraries]
    glib.g_key_file_new.restype = c.c_void_p
    glib.g_key_file_load_from_data.argtypes = [c.c_void_p, c.c_char_p, c.c_size_t, c.c_int, c.c_void_p]
    glib.g_key_file_free.argtypes = [c.c_void_p]
    gio.g_desktop_app_info_new_from_keyfile.argtypes = [c.c_void_p]
    gio.g_desktop_app_info_new_from_keyfile.restype = c.c_void_p
    gio.g_app_info_launch.argtypes = [c.c_void_p, c.c_void_p, c.c_void_p, c.c_void_p]
    gobject.g_object_unref.argtypes = [c.c_void_p]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        exe = root / "a b'c\"d$`e\\g"
        log = root/'argv'
        exe.write_text('#!/usr/bin/env python3\nimport json, sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n')
        exe.chmod(0o755)
        args = ['with space', 'quote"d', 'dollar$', 'percent%', 'back\\slash']
        entry = ('[Desktop Entry]\nType=Application\nName=Test\nExec='+
                 host.desktop_exec([str(exe), str(log)]+args)+'\n').encode()
        key = glib.g_key_file_new()
        app = None
        try:
            assert glib.g_key_file_load_from_data(key, entry, len(entry), 0, None)
            app = gio.g_desktop_app_info_new_from_keyfile(key)
            assert app
            assert gio.g_app_info_launch(app, None, None, None)
            for _ in range(100):
                if log.exists():
                    break
                time.sleep(0.01)
            assert json.loads(log.read_text()) == args
        finally:
            if app:
                gobject.g_object_unref(app)
            glib.g_key_file_free(key)


def test_windows_stop_targets_only_selected_profile():
    p = sample_profile(kind='windows')
    own = (999991, r'"D:\Apps\thunderbird.exe" --headless -profile "D:\Mail\Profile"')
    other = (999992, r'"D:\Apps\thunderbird.exe" --headless -profile "D:\Other\Profile"')
    with patch.object(host, 'translated_path', return_value=r'D:\Mail\Profile'), \
         patch.object(host, 'processes', side_effect=[[own, other], [other]]), \
         patch.object(host.subprocess, 'run') as run, patch.object(host.time, 'sleep'):
        host.stop('windows', profile=p)
    run.assert_called_once_with(['taskkill.exe', '/PID', str(own[0]), '/F'], capture_output=True, timeout=20)


def test_gui_already_open_does_not_schedule_a_headless_restart():
    with patch.object(host, 'state', return_value=('window', [])), patch.object(host, 'launch') as launch:
        host.window(sample_profile(), side='macos')
    launch.assert_not_called()


def test_headless_to_gui_stops_only_selected_profile():
    p = sample_profile()
    with patch.object(host, 'state', return_value=('headless', [])), \
         patch.object(host, 'stop') as stop, \
         patch.object(host, 'executable', return_value=['/Applications/Thunderbird.app/Contents/MacOS/thunderbird']), \
         patch.object(host, 'launch') as launch:
        host.window(p, side='macos')
    stop.assert_called_once_with('macos', profile=p)
    assert launch.call_args.args[0][-3:] == ['--no-remote', '-profile', str(p.path)]


def test_windows_gui_is_direct_without_a_fake_restart_promise():
    p = sample_profile(kind='windows')
    with patch.object(host, 'state', return_value=('stopped', [])), \
         patch.object(host, 'translated_path', return_value=r'D:\Mail\Profile'), \
         patch.object(host, 'executable', return_value=['/mnt/d/Apps/thunderbird.exe']), \
         patch.object(host, 'launch') as launch:
        host.window(p, side='windows')
    assert launch.call_args.args[0] == ['/mnt/d/Apps/thunderbird.exe', '--no-remote', '-profile', r'D:\Mail\Profile']


def test_mac_login_arguments_are_valid_plist_and_keep_exact_profile():
    import plistlib
    exe = '/Applications/Mail & Tools/Thunderbird.app/Contents/MacOS/thunderbird'
    p = sample_profile(path=Path('/Users/Example User/Mail & Profiles/one'))
    with patch.object(host, 'executable', return_value=[exe]):
        data = plistlib.loads(host.autostart_text(p, 'macos').encode())
    assert data['RunAtLoad'] is True
    assert data['ProgramArguments'] == [exe, '--headless', '--no-remote', '-profile', str(p.path)]


def test_windows_login_entry_uses_exact_profile_and_rejects_failed_translation():
    p = sample_profile(kind='windows')
    with patch.object(host, 'executable', return_value=['/mnt/d/Apps/thunderbird.exe']), \
         patch.object(host, 'translated_path', side_effect=[r'D:\Apps\thunderbird.exe', r'D:\Mail\Profile']):
        entry = host.autostart_text(p, 'windows')
    assert entry == ('CreateObject("WScript.Shell").Run """D:\\Apps\\thunderbird.exe"" '
                     '--headless --no-remote -profile ""D:\\Mail\\Profile""", 0, False\r\n')
    with patch.object(host.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'bad')):
        with pytest.raises(OSError, match='Cannot translate'):
            host.translated_path('/unavailable', windows=True)


def test_status_reports_unknown_profile_as_unhealthy():
    p = sample_profile()
    said = []
    with patch.object(host, 'chosen', return_value=p), \
         patch.object(host, 'state', return_value=('unknown', [])), \
         patch.object(host, 'autostarts', return_value=False), \
         patch.object(cli.settings, 'load', return_value={'accounts': ['one']}):
        assert cli.status(say=said.append) == 1
    assert any('could not be identified' in line for line in said)


def test_duplicate_profile_roots_do_not_make_same_profile_ambiguous():
    p = sample_profile()
    with patch.object(host, 'profiles', return_value=[p, p]):
        assert host.profile_match(999999, 'thunderbird -P mail', p, 'macos') is True


def test_doctor_reports_bad_executable_override_instead_of_crashing():
    from twigs.kit import doctor
    with patch.object(host, 'chosen', return_value=sample_profile()), \
         patch.object(host, 'where', return_value='windows'), \
         patch.object(host, 'executable', side_effect=OSError('TWIGS_THUNDERBIRD_EXE is missing')), \
         patch.object(host, 'state', return_value=('stopped', [])), \
         patch.object(cli.settings, 'load', return_value=None):
        report = doctor.diagnose(['thunderbird'], 'ubuntu', command=lambda _: None)
    check = next(c for c in report['checks'] if c['name'] == 'Thunderbird profile')
    assert not check['ok'] and 'TWIGS_THUNDERBIRD_EXE' in check['hint']
