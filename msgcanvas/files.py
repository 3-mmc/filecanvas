"""Opening and saving what comes attached: the system's own app opens a file (Windows' under
WSL), and saved files go to your Downloads folder (Windows' under WSL)."""
import os, re, shutil, subprocess, sys

CACHE = os.path.join(os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'), 'msgcanvas')


def wsl():
    try:
        with open('/proc/version') as f:
            return 'microsoft' in f.read().lower()
    except OSError:
        return False


def downloads():
    """Where saved files go: Windows' Downloads under WSL, else ~/Downloads (or XDG's)."""
    if wsl():
        try:    # Windows' own word for whose profile this is; cmd.exe wants a Windows folder to start in
            home = subprocess.run(['cmd.exe', '/c', 'echo %USERPROFILE%'], capture_output=True, text=True,
                                  cwd='/mnt/c', timeout=10).stdout.strip()
            home = subprocess.run(['wslpath', '-u', home], capture_output=True, text=True).stdout.strip()
            if home and os.path.isdir(os.path.join(home, 'Downloads')):
                return os.path.join(home, 'Downloads')
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        out = subprocess.run(['xdg-user-dir', 'DOWNLOAD'], capture_output=True, text=True).stdout.strip()
        if out and out != os.path.expanduser('~'):
            return out
    except OSError:
        pass
    return os.path.expanduser('~/Downloads')


def opener(path):
    """The command that opens `path` with its usual app."""
    if wsl():
        win = subprocess.run(['wslpath', '-w', path], capture_output=True, text=True).stdout.strip()
        return ['explorer.exe', win]
    return ['open', path] if sys.platform == 'darwin' else ['xdg-open', path]


def open_file(path):
    """Open `path` without waiting; nothing it prints reaches the canvas."""
    subprocess.Popen(opener(path), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def safe_name(name):
    name = re.sub(r'[\x00-\x1f/\\:*?"<>|]+', '_', os.path.basename(name or '')).strip(' .')
    return name or 'attachment'


def unique(path):
    """`path`, or `path (2)`… if it's taken."""
    root, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(path):
        path, n = f'{root} ({n}){ext}', n + 1
    return path


def save(data, name, folder):
    os.makedirs(folder, exist_ok=True)
    path = unique(os.path.join(folder, safe_name(name)))
    with open(path, 'wb') as f:
        f.write(data)
    return path


def copy_to(path, folder):
    os.makedirs(folder, exist_ok=True)
    target = unique(os.path.join(folder, safe_name(os.path.basename(path))))
    shutil.copy2(path, target)
    return target


# Windows' toast, through PowerShell's own app id (a toast needs a registered app to come from).
# The words come in through the environment, never through the script, so nothing in a message
# can change what runs.
TOAST = r'''
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode($env:MSGCANVAS_TITLE)) > $null
$x.Item(1).AppendChild($t.CreateTextNode($env:MSGCANVAS_BODY)) > $null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($t))
'''


def notifier(title, body):
    """The command that shows a desktop notification, and its environment; None if there's no way."""
    env = dict(os.environ, MSGCANVAS_TITLE=title[:120], MSGCANVAS_BODY=body[:240])
    if wsl() and shutil.which('powershell.exe'):
        return ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', TOAST], env
    if sys.platform == 'darwin':
        script = 'display notification (system attribute "MSGCANVAS_BODY") with title (system attribute "MSGCANVAS_TITLE")'
        return ['osascript', '-e', script], env
    if shutil.which('notify-send'):
        return ['notify-send', '--app-name=msgcanvas', title[:120], body[:240]], env
    return None


def notify(title, body):
    """A desktop notification (Windows' under WSL), without waiting for it."""
    how = notifier(title, body)
    if how:
        cmd, env = how
        subprocess.Popen(cmd, env=env, cwd='/mnt/c' if wsl() else None, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
