"""Desktop notifications: Windows' under WSL (a toast, through PowerShell), macOS's, or
notify-send's. Shown without waiting; the texts go through the environment, never the command."""
import os, shutil, subprocess, sys

TOAST = r'''
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode($env:TWIGS_TITLE)) > $null
$x.Item(1).AppendChild($t.CreateTextNode($env:TWIGS_BODY)) > $null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($t))
'''


def wsl():
    try:
        with open('/proc/version') as f:
            return 'microsoft' in f.read().lower()
    except OSError:
        return False


def notifier(title, body):
    """The command that shows a notification, and its environment; None if there's no way."""
    env = dict(os.environ, TWIGS_TITLE=title[:120], TWIGS_BODY=body[:240])
    if wsl() and shutil.which('powershell.exe'):
        return ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', TOAST], env
    if sys.platform == 'darwin':
        script = 'display notification (system attribute "TWIGS_BODY") with title (system attribute "TWIGS_TITLE")'
        return ['osascript', '-e', script], env
    if shutil.which('notify-send'):
        return ['notify-send', '--app-name=twigs', title[:120], body[:240]], env
    return None


def notify(title, body):
    how = notifier(title, body)
    if how:
        cmd, env = how
        subprocess.Popen(cmd, env=env, cwd='/mnt/c' if wsl() else None, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
