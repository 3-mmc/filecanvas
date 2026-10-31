"""Thunderbird itself, on Linux, macOS, or Windows from WSL: its profiles, whether the MCP
extension is enabled in one, whether it's running (headless or in a window), starting and
stopping it, starting it at login, and fetching the extension. Nothing here reads mail,
passwords or account settings; the profile is only read, never written."""
import configparser
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import platform
import re
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


def translated_path(path, windows=False):
    """Translate through WSL's configured mounts, including custom drives and UNC paths."""
    try:
        got = subprocess.run(['wslpath', '-w' if windows else '-u', str(path)],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as e:
        raise OSError('Cannot translate Thunderbird path: ' + str(path)) from e
    if got.returncode or not got.stdout.strip():
        raise OSError('Cannot translate Thunderbird path: ' + str(path))
    return got.stdout.strip()


def windows_executable():
    """An explicit override, PATH, Windows App Paths, then conventional install locations."""
    override = os.environ.get('TWIGS_THUNDERBIRD_EXE')
    if override:
        path = Path(translated_path(override)) if PureWindowsPath(override).is_absolute() else Path(override)
        if not path.is_file():
            raise OSError('TWIGS_THUNDERBIRD_EXE does not name an executable file')
        return str(path)
    found = shutil.which('thunderbird.exe')
    if found:
        return found
    # Windows owns these locations: don't assume its system or profile drive is C:.
    script = r"""$ErrorActionPreference = 'SilentlyContinue'
$keys = @('HKCU:\Software\Microsoft\Windows\CurrentVersion\App Paths\thunderbird.exe',
          'HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths\thunderbird.exe',
          'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\thunderbird.exe')
foreach ($key in $keys) { (Get-Item $key).GetValue('') }
foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
    if ($base) { Join-Path $base 'Mozilla Thunderbird\thunderbird.exe' }
}
"""
    try:
        got = subprocess.run(['powershell.exe', '-NoProfile', '-Command', script],
                             capture_output=True, text=True, timeout=10)
        if got.returncode == 0:
            for candidate in got.stdout.splitlines():
                if candidate.strip():
                    try:
                        path = Path(translated_path(candidate.strip().strip('"')))
                    except OSError:
                        continue
                    if path.is_file():
                        return str(path)
    except (OSError, subprocess.SubprocessError):
        pass
    return None


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
            if listed[s].get('IsRelative', '1') == '1':
                folder = root/rel.replace('\\', '/') if kind == 'windows' else root/rel
            else:
                folder = Path(translated_path(rel)) if kind == 'windows' else Path(rel)
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
        exe = windows_executable()
        return [str(exe)] if exe else None
    found = shutil.which('thunderbird') or shutil.which('thunderbird-esr')
    return [found] if found else None


PROCESS = re.compile(r'(^|[/\\])thunderbird(-bin|-esr|\.exe)?$', re.I)


def processes(side=None):
    """[(pid, arguments)] of Thunderbird's main processes (not its content processes)."""
    side = side or where()
    try:
        if side == 'windows':
            script = ("$session = (Get-Process -Id $PID).SessionId; "
                      "Get-CimInstance Win32_Process -Filter \"Name='thunderbird.exe'\" | "
                      "Where-Object { $_.SessionId -eq $session } | "
                      "ForEach-Object { $cmd = $_.CommandLine; "
                      "if (-not $cmd) { $cmd = 'thunderbird.exe' }; \"$($_.ProcessId) $cmd\" }")
            got = subprocess.run(['powershell.exe', '-NoProfile', '-Command', script],
                                 capture_output=True, text=True, timeout=20)
        else:
            got = subprocess.run(['ps', '-x', '-o', 'pid=,args='], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        raise OSError('Cannot inspect Thunderbird processes') from e
    if got.returncode:
        raise OSError('Cannot inspect Thunderbird processes')
    out = []
    for line in got.stdout.splitlines():
        pid, _, args = line.strip().partition(' ')
        program = args.split(' -', 1)[0].strip().strip('"')
        if pid.isdigit() and PROCESS.search(program) and '-contentproc' not in args:
            out.append((int(pid), args))
    return out


def profile_match(pid, args, profile, side):
    """True/False for an explicit profile; None when a normal GUI launch is ambiguous.

    ps loses argv boundaries. On Linux use procfs when available; on other systems
    retain quoted Windows paths and collect path words up to the next option.
    """
    try:
        if side == 'linux':
            argv = Path(f'/proc/{pid}/cmdline').read_bytes().decode().rstrip('\0').split('\0')
        else:
            argv = []
    except (OSError, UnicodeError):
        argv = []
    if not argv:
        argv = [a.strip('"') for a in re.findall(r'"[^"\n]*"|\S+', args)]
    for i, arg in enumerate(argv):
        flag, sep, inline = arg.partition('=')
        if flag.lower() not in ('-p', '-profile', '--profile'):
            continue
        if sep:
            value = inline.strip('"')
        else:
            words = []
            for word in argv[i+1:]:
                if word.startswith('-'):
                    break
                words.append(word)
            value = ' '.join(words)
        if flag.lower() == '-p':
            # Duplicate names in different installations aren't proof of ownership.
            matches = [p for p in profiles(side) if p.name == value]
            if len({p.path.resolve() for p in matches}) > 1:
                return None
            return value == profile.name
        if side == 'windows':
            return PureWindowsPath(value) == PureWindowsPath(translated_path(profile.path, windows=True))
        return Path(value).is_absolute() and Path(value).resolve() == profile.path.resolve()
    if side != 'windows':
        try:
            # Mozilla's Unix profile lock records the owning PID, including GUI launches.
            owner = os.readlink(profile.path/'lock').rsplit(':', 1)[-1].lstrip('+')
            if owner.isdigit() and int(owner) == pid:
                return True
        except OSError:
            pass
    return None


def state(side=None, profile=None):
    """State and matching processes. Unknown ownership never authorizes stopping a process."""
    side = side or where()
    found = processes(side)
    if profile is not None:
        matched, unknown = [], []
        for pid, args in found:
            match = profile_match(pid, args, profile, side)
            if match is True:
                matched.append((pid, args))
            elif match is None:
                unknown.append((pid, args))
        if unknown:
            return 'unknown', matched + unknown
        found = matched
    if not found:
        return 'stopped', []
    return ('headless' if all(re.search(r'(?:^|\s)--headless(?:\s|$)', a) for _, a in found) else 'window'), found


def require_known(now):
    if now == 'unknown':
        raise OSError('Cannot identify the running Thunderbird profile; close Thunderbird yourself, then try again')


def profile_arguments(profile):
    """Use the exact directory; sandboxed packages resolve their own profile names."""
    if profile.kind in ('flatpak', 'snap'):
        return ['--no-remote', '-P', profile.name]
    path = translated_path(profile.path, windows=True) if profile.kind == 'windows' else str(profile.path)
    return ['--no-remote', '-profile', path]


def hint(side=None):
    """Why Thunderbird mail can't be read, and what to do, or None when nothing's in the way."""
    profile = chosen(profiles(side))
    if profile is None:
        return 'Thunderbird isn\'t set up here\nInstall it, add your accounts, then: twigs thunderbird connect'
    if profile.extension != 'enabled':
        return ('Thunderbird MCP is ' + ('disabled' if profile.extension else 'not installed') +
                f' in the profile {profile.name}\nRun: twigs thunderbird connect')
    now = state(side, profile=profile)[0]
    if now == 'unknown':
        return 'Cannot identify the running Thunderbird profile; close Thunderbird yourself, then: twigs thunderbird start'
    if now == 'stopped':
        return 'Thunderbird isn\'t running\nStart it: twigs thunderbird start (or open Thunderbird)'
    return None


def launch(command):
    """Start a program on its own, not tied to this terminal."""
    devnull = subprocess.DEVNULL
    return subprocess.Popen(command, stdin=devnull, stdout=devnull, stderr=devnull, start_new_session=True)


def start(profile, headless=True, side=None):
    """Start Thunderbird on `profile`; nothing if it's already running. Returns the state."""
    now, _ = state(side, profile=profile)
    require_known(now)
    if now != 'stopped':
        return now
    command = executable(profile)
    if not command:
        raise OSError('Thunderbird isn\'t installed where twigs looks; start it yourself')
    launch(command + (['--headless'] if headless else []) + profile_arguments(profile))
    return 'headless' if headless else 'window'


def stop(side=None, wait=20, profile=None):
    """Stop a headless Thunderbird. A window is left alone: it's closed as any window is."""
    side = side or where()
    profile = profile or chosen(profiles(side))
    if profile is None:
        raise OSError('No Thunderbird profile found; nothing was stopped')
    now, found = state(side, profile=profile)
    require_known(now)
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
        _, found = state(side, profile=profile)
    if found:
        raise OSError('Thunderbird is still running; try again in a moment')


def window(profile, then_headless=True, side=None):
    """Thunderbird in a window: a headless one stops first, and (not on Windows, where twigs
    can't wait for it) goes back to headless when the window closes."""
    side = side or where()
    now = state(side, profile=profile)[0]
    require_known(now)
    if now == 'window':
        return
    if now == 'headless':
        stop(side, profile=profile)
    command = executable(profile)
    if not command:
        raise OSError('Thunderbird isn\'t installed where twigs looks; open it yourself')
    command += profile_arguments(profile)
    if then_headless and side != 'windows':
        # Keep the same executable/profile after the window exits; don't reselect
        # a profile or depend on a development checkout remaining on PATH.
        launch(['/bin/sh', '-c', '"$@" && exec "$@" --headless', 'sh'] + command)
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
        exe = translated_path(command[0], windows=True)
        # Runs Thunderbird itself, hidden: WSL needn't be running at login.
        return ('CreateObject("WScript.Shell").Run """' + exe.replace('"', '""') + '"" --headless --no-remote -profile ""' +
                translated_path(profile.path, windows=True).replace('"', '""') + '""", 0, False\r\n')
    command = executable(profile)
    if not command:
        raise OSError('Cannot find Thunderbird for login startup')
    command += ['--headless'] + profile_arguments(profile)
    if side == 'macos':
        args = ''.join(f'    <string>{escape(a)}</string>\n' for a in command)
        return ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0">\n<dict>\n  <key>Label</key>\n  <string>dev.twigs.thunderbird</string>\n'
                f'  <key>ProgramArguments</key>\n  <array>\n{args}  </array>\n'
                '  <key>RunAtLoad</key>\n  <true/>\n</dict>\n</plist>\n')
    return ('[Desktop Entry]\nType=Application\nName=Thunderbird for twigs\n'
            'Comment=Thunderbird in the background, so twigs can show its mail\n'
            f'Exec={desktop_exec(command)}\nTerminal=false\nX-GNOME-Autostart-enabled=true\n')


def desktop_exec(command):
    """Desktop Entry Exec has two escape layers and isn't interpreted by a shell."""
    def quote(arg):
        arg = arg.replace('%', '%%')
        if re.fullmatch(r'[A-Za-z0-9_./:=+-]+', arg):
            return arg
        arg = ''.join('\\' + c if c in '\\"`$' else c for c in arg)
        return '"' + arg.replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t') + '"'
    return ' '.join(quote(a) for a in command)


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
