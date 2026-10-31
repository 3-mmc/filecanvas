"""Thunderbird itself, on Linux, macOS, or Windows from WSL: its profiles, whether the MCP
extension is enabled in one, whether it's running (headless or in a window), starting and
stopping it, starting it at login, and fetching the extension. Nothing here reads mail,
passwords or account settings; the profile is only read, never written."""
import configparser
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from collections import namedtuple

EXTENSION_ID = 'thunderbird-mcp@tkasperczyk.dev'
EXTENSION_VERSION = '0.10.0'       # the version the bundled bridge is pinned to (vendor/UPSTREAM.md)
EXTENSION_URL = ('https://github.com/TKasperczyk/thunderbird-mcp/releases/download/'
                 f'v{EXTENSION_VERSION}/thunderbird-mcp-v{EXTENSION_VERSION}.xpi')
EXTENSION_SHA256 = '638b7512d4f8b30d71b1547e10372ac537f046cc05d196a867e3871684c19f8c'
EXTENSION_MAX = 10 * 1024 * 1024
WINDOWS_EXE = ('Program Files/Mozilla Thunderbird/thunderbird.exe',
               'Program Files (x86)/Mozilla Thunderbird/thunderbird.exe')
MAC_APPS = ('/Applications/Thunderbird.app', '~/Applications/Thunderbird.app')
AUTOSTART_NAME = 'twigs-thunderbird'

# where: the profile's folder; root: its Thunderbird's folder; kind: native, flatpak, snap,
# macos or windows (how to start it); extension: 'enabled', 'disabled' or None
Profile = namedtuple('Profile', 'name path root kind default extension')


def where():
    """'macos', 'windows' (twigs in WSL, Thunderbird on Windows) or 'linux'."""
    if sys.platform == 'darwin':
        return 'macos'
    if 'microsoft' in platform.release().lower() and not linux_roots() and windows_roots():
        return 'windows'
    return 'linux'


def linux_roots():
    home = Path.home()
    config = Path(os.environ.get('XDG_CONFIG_HOME') or home/'.config')
    found = [(config/'thunderbird', 'native'), (home/'.thunderbird', 'native'),
             (home/'.var/app/org.mozilla.Thunderbird/.thunderbird', 'flatpak'),
             (home/'snap/thunderbird/common/.thunderbird', 'snap')]
    return [(r, k) for r, k in found if (r/'profiles.ini').is_file()]


def windows_home():
    """The Windows user's folder as a WSL path, or None."""
    try:
        got = subprocess.run(['cmd.exe', '/c', 'echo %USERPROFILE%'], capture_output=True, text=True,
                             timeout=10, cwd='/mnt/c')
        win = got.stdout.strip()
        if got.returncode or not win or '%' in win:
            return None
        got = subprocess.run(['wslpath', '-u', win], capture_output=True, text=True, timeout=5)
        return Path(got.stdout.strip()) if got.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def windows_roots():
    home = windows_home()
    root = home/'AppData/Roaming/Thunderbird' if home else None
    return [(root, 'windows')] if root and (root/'profiles.ini').is_file() else []


def roots(side=None):
    side = side or where()
    if side == 'macos':
        root = Path.home()/'Library/Thunderbird'
        return [(root, 'macos')] if (root/'profiles.ini').is_file() else []
    return windows_roots() if side == 'windows' else linux_roots()


def extension(folder):
    """Whether the MCP extension is in a profile: 'enabled', 'disabled', or None."""
    try:
        addons = json.loads((Path(folder)/'extensions.json').read_text(encoding='utf-8')).get('addons', [])
    except (OSError, ValueError, AttributeError):
        return None
    for a in addons:
        if isinstance(a, dict) and a.get('id') == EXTENSION_ID:
            off = a.get('userDisabled') or a.get('appDisabled') or a.get('softDisabled') or not a.get('active', True)
            return 'disabled' if off else 'enabled'
    return None


def ini(path):
    c = configparser.RawConfigParser(strict=False)
    c.optionxform = str
    try:
        c.read(path, encoding='utf-8')
    except (OSError, configparser.Error):
        pass
    return c


def profiles(side=None):
    """Every Thunderbird profile found, each install's default first."""
    out = []
    for root, kind in roots(side):
        listed = ini(root/'profiles.ini')
        defaults = {listed[s].get('Default') for s in listed.sections() if s.startswith('Install')}
        installs = ini(root/'installs.ini')
        defaults |= {installs[s].get('Default') for s in installs.sections()}
        defaults.discard(None)
        found = []
        for s in listed.sections():
            if not s.startswith('Profile') or 'Path' not in listed[s]:
                continue
            rel = listed[s]['Path']
            folder = root/rel if listed[s].get('IsRelative', '1') == '1' else Path(rel)
            # an install's default wins; Default=1 is what Thunderbird used before installs had their own
            default = rel in defaults or (not defaults and listed[s].get('Default') == '1')
            found.append(Profile(listed[s].get('Name', rel), folder, root, kind, default, extension(folder)))
        out += sorted(found, key=lambda p: not p.default)
    return out


def chosen(found=None):
    """The profile twigs uses: the one with the extension enabled (a default one first), else the
    default; None when there's no Thunderbird profile at all."""
    found = profiles() if found is None else found
    for pick in (lambda p: p.extension == 'enabled' and p.default, lambda p: p.extension == 'enabled',
                 lambda p: p.default, lambda p: True):
        hit = next((p for p in found if pick(p)), None)
        if hit:
            return hit
    return None


def executable(profile):
    """The command that starts this profile's Thunderbird, or None if it isn't found."""
    if profile.kind == 'flatpak':
        return ['flatpak', 'run', 'org.mozilla.Thunderbird'] if shutil.which('flatpak') else None
    if profile.kind == 'snap':
        return ['snap', 'run', 'thunderbird'] if shutil.which('snap') else None
    if profile.kind == 'macos':
        app = next((Path(a).expanduser() for a in MAC_APPS if Path(a).expanduser().is_dir()), None)
        return [str(app/'Contents/MacOS/thunderbird')] if app else None
    if profile.kind == 'windows':
        exe = next((Path('/mnt/c')/e for e in WINDOWS_EXE if (Path('/mnt/c')/e).is_file()), None)
        return [str(exe)] if exe else None
    found = shutil.which('thunderbird') or shutil.which('thunderbird-esr')
    return [found] if found else None


PROCESS = re.compile(r'(^|[/\\])thunderbird(-bin|-esr|\.exe)?$', re.I)


def processes(side=None):
    """[(pid, arguments)] of Thunderbird's main processes (not its content processes)."""
    side = side or where()
    try:
        if side == 'windows':
            script = ("Get-CimInstance Win32_Process -Filter \"Name='thunderbird.exe'\" | "
                      "ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }")
            got = subprocess.run(['powershell.exe', '-NoProfile', '-Command', script],
                                 capture_output=True, text=True, timeout=20)
        else:
            got = subprocess.run(['ps', '-axo', 'pid=,args='], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in got.stdout.splitlines():
        pid, _, args = line.strip().partition(' ')
        program = args.split(' -', 1)[0].strip().strip('"')
        if pid.isdigit() and PROCESS.search(program) and '-contentproc' not in args:
            out.append((int(pid), args))
    return out


def state(side=None):
    """'headless', 'window' or 'stopped', and the processes."""
    found = processes(side)
    if not found:
        return 'stopped', []
    return ('headless' if all('--headless' in a for _, a in found) else 'window'), found


def hint(side=None):
    """Why Thunderbird mail can't be read, and what to do, or None when nothing's in the way."""
    profile = chosen(profiles(side))
    if profile is None:
        return 'Thunderbird isn\'t set up here\nInstall it, add your accounts, then: twigs thunderbird connect'
    if profile.extension != 'enabled':
        return ('Thunderbird MCP is ' + ('disabled' if profile.extension else 'not installed') +
                f' in the profile {profile.name}\nRun: twigs thunderbird connect')
    if state(side)[0] == 'stopped':
        return 'Thunderbird isn\'t running\nStart it: twigs thunderbird start (or open Thunderbird)'
    return None


def launch(command):
    """Start a program on its own, not tied to this terminal."""
    devnull = subprocess.DEVNULL
    return subprocess.Popen(command, stdin=devnull, stdout=devnull, stderr=devnull, start_new_session=True)


def start(profile, headless=True, side=None):
    """Start Thunderbird on `profile`; nothing if it's already running. Returns the state."""
    now, _ = state(side)
    if now != 'stopped':
        return now
    command = executable(profile)
    if not command:
        raise OSError('Thunderbird isn\'t installed where twigs looks; start it yourself')
    launch(command + (['--headless'] if headless else []) + ['-P', profile.name])
    return 'headless' if headless else 'window'


def stop(side=None, wait=20):
    """Stop a headless Thunderbird. A window is left alone: it's closed as any window is."""
    side = side or where()
    now, found = state(side)
    if now == 'window':
        raise OSError('Thunderbird is open in a window; close it there')
    for pid, _ in found:
        if side == 'windows':
            # A headless Thunderbird has no window to ask to close.
            subprocess.run(['taskkill.exe', '/PID', str(pid), '/F'], capture_output=True, timeout=20)
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    end = time.monotonic() + wait
    while found and time.monotonic() < end:
        time.sleep(0.5)
        found = processes(side)
    if found:
        raise OSError('Thunderbird is still running; try again in a moment')


def twigs_command():
    """How to run twigs from outside this shell (autostart, after a window closes)."""
    found = shutil.which('twigs')
    return [found] if found else [sys.executable, '-m', 'twigs']


def window(profile, then_headless=True, side=None):
    """Thunderbird in a window: a headless one stops first, and (not on Windows, where twigs
    can't wait for it) goes back to headless when the window closes."""
    side = side or where()
    if state(side)[0] == 'headless':
        stop(side)
    command = executable(profile)
    if not command:
        raise OSError('Thunderbird isn\'t installed where twigs looks; open it yourself')
    command += ['-P', profile.name]
    if then_headless and side != 'windows':
        # A small shell waits for the window and starts the headless one, so twigs can exit.
        twigs = ' '.join(shlex.quote(a) for a in twigs_command())
        launch(['/bin/sh', '-c', '"$@"; exec ' + twigs + ' thunderbird start', 'sh'] + command)
    else:
        launch(command)


# -- starting at login
def autostart_path(side=None):
    side = side or where()
    if side == 'macos':
        return Path.home()/'Library/LaunchAgents'/'dev.twigs.thunderbird.plist'
    if side == 'windows':
        home = windows_home()
        if not home:
            raise OSError('Cannot find the Windows user folder')
        return home/'AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup'/f'{AUTOSTART_NAME}.vbs'
    config = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')
    return config/'autostart'/f'{AUTOSTART_NAME}.desktop'


def autostart_text(profile, side=None):
    side = side or where()
    if side == 'windows':
        command = executable(profile)
        if not command:
            raise OSError('Cannot find thunderbird.exe')
        exe = subprocess.run(['wslpath', '-w', command[0]], capture_output=True, text=True, timeout=5).stdout.strip()
        # Runs Thunderbird itself, hidden: WSL needn't be running at login.
        return ('CreateObject("WScript.Shell").Run """' + exe + '"" --headless -P ""' +
                profile.name.replace('"', '') + '""", 0, False\r\n')
    command = twigs_command() + ['thunderbird', 'start']
    if side == 'macos':
        args = ''.join(f'    <string>{escape(a)}</string>\n' for a in command)
        return ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0">\n<dict>\n  <key>Label</key>\n  <string>dev.twigs.thunderbird</string>\n'
                f'  <key>ProgramArguments</key>\n  <array>\n{args}  </array>\n'
                '  <key>RunAtLoad</key>\n  <true/>\n</dict>\n</plist>\n')
    return ('[Desktop Entry]\nType=Application\nName=Thunderbird for twigs\n'
            'Comment=Thunderbird in the background, so twigs can show its mail\n'
            f'Exec={" ".join(shlex.quote(a) for a in command)}\nTerminal=false\nX-GNOME-Autostart-enabled=true\n')


def escape(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def autostart(profile, on=True, side=None):
    """Start headless Thunderbird at login (on), or not any more (off). Returns the file."""
    path = autostart_path(side)
    if not on:
        path.unlink(missing_ok=True)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(autostart_text(profile, side), encoding='utf-8', newline='')
    return path


def autostarts(side=None):
    try:
        return autostart_path(side).is_file()
    except OSError:
        return False


# -- the extension
def cache():
    base = os.environ.get('XDG_CACHE_HOME') or Path.home()/'.cache'
    return Path(base)/'twigs'


def fetch_extension(folder=None, opener=urllib.request.urlopen):
    """The pinned extension, downloaded once and checked against its known hash."""
    dest = Path(folder or cache())/f'thunderbird-mcp-v{EXTENSION_VERSION}.xpi'
    if dest.is_file() and hashlib.sha256(dest.read_bytes()).hexdigest() == EXTENSION_SHA256:
        return dest
    with opener(EXTENSION_URL, timeout=60) as got:
        data = got.read(EXTENSION_MAX + 1)
    if len(data) > EXTENSION_MAX or hashlib.sha256(data).hexdigest() != EXTENSION_SHA256:
        raise OSError('The downloaded extension does not match the version twigs expects; nothing was saved')
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_suffix('.tmp')
    temp.write_bytes(data)
    os.replace(temp, dest)
    return dest


def shown_path(path, side=None):
    """A path as Thunderbird's file chooser would take it (Windows' own on Windows)."""
    if (side or where()) == 'windows':
        got = subprocess.run(['wslpath', '-w', str(path)], capture_output=True, text=True, timeout=5)
        if got.returncode == 0:
            return got.stdout.strip()
    return str(path)
