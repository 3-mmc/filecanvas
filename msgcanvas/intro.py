"""The opening screen: MSGCANVAS assembling out of noise, as cdeck's logo does, over a field of
faint stars, saying what's being loaded, and dissolving into dust once it all is (or a key is
pressed). It stays at least MIN seconds and at most MAX. CDECK_CALM holds it still;
MSGCANVAS_NO_INTRO=1, or a terminal that isn't one, skips it."""
import os, select, sys, time

from . import filecanvas as fc

LOGO = [
    "███╗   ███╗███████╗ ██████╗  ██████╗ █████╗ ███╗   ██╗██╗   ██╗ █████╗ ███████╗",
    "████╗ ████║██╔════╝██╔════╝ ██╔════╝██╔══██╗████╗  ██║██║   ██║██╔══██╗██╔════╝",
    "██╔████╔██║███████╗██║  ███╗██║     ███████║██╔██╗ ██║██║   ██║███████║███████╗",
    "██║╚██╔╝██║╚════██║██║   ██║██║     ██╔══██║██║╚██╗██║╚██╗ ██╔╝██╔══██║╚════██║",
    "██║ ╚═╝ ██║███████║╚██████╔╝╚██████╗██║  ██║██║ ╚████║ ╚████╔╝ ██║  ██║███████║",
    "╚═╝     ╚═╝╚══════╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝╚═╝  ╚═══╝  ╚═══╝  ╚═╝  ╚═╝╚══════╝",
]
SMALL = ["m s g c a n v a s"]
RAMP = [209, 209, 210, 211, 212, 213, 177, 141, 105, 75, 81, 117]   # cdeck's: orange, pink, sky blue
SHADOW = [130, 131, 132, 133, 96, 97, 61, 61, 25, 31, 31, 67]
TAGLINE = 'mail and chats, on one canvas'
ASSEMBLE = 0.7      # seconds for the logo to come out of the noise
OUTRO = 0.4         # and to go to dust
MIN, MAX = 0.9, 3.0


class Screen:
    def __init__(self, cols, rows, calm=False):
        self.cols, self.rows, self.calm = cols, rows, calm
        self.logo = LOGO if cols >= len(LOGO[0]) + 4 and rows >= len(LOGO) + 8 else SMALL
        self.top = max(1, (rows - len(self.logo) - 4) // 2)
        self.left = max(0, (cols - fc.cells(self.logo[0])) // 2)

    def colour(self, x, y):
        """The ramp across the logo; its ╗╝║═ shadow in the darker ramp."""
        i = min(len(RAMP) - 1, x * len(RAMP) // max(1, fc.cells(self.logo[0])))
        return SHADOW[i] if self.logo[y][x] not in '█ ' and self.logo is LOGO else RAMP[i]

    def frame(self, t, status, leaving=None):
        """The screen at t seconds in; `leaving`: seconds since it began to go."""
        out = ['\033[H\033[0m']
        for row in range(self.rows):
            line = []
            y = row - self.top
            for col in range(self.cols):
                x = col - self.left
                ch, style = ' ', ''
                if 0 <= y < len(self.logo) and 0 <= x < len(self.logo[y]) and self.logo[y][x] != ' ':
                    ch, style = self.logo[y][x], f'\033[38;5;{self.colour(x, y)}m'
                    born = fc.chance(y + 3, x) * ASSEMBLE
                    if not self.calm and t < born + 0.15:      # still noise, flickering till it settles
                        ch = fc.NOISE[int(fc.chance(int(t * 20) + y, x) * len(fc.NOISE))] if t >= born else ' '
                        style = '\033[38;5;240m' if t < born + 0.05 else style
                    if leaving is not None and not self.calm:
                        gone = leaving / OUTRO - fc.chance(y + 7, x) * 0.6
                        if gone > 0:
                            ch = fc.DUST[min(len(fc.DUST) - 1, int(gone * 2.5 * len(fc.DUST)))]
                elif fc.chance(row + 11, col) < 0.012:            # a faint star, slowly twinkling
                    phase = 0 if self.calm else int(t * 2 + fc.chance(row, col) * 6) % 6
                    ch, style = '·∙·  ·'[phase], '\033[38;5;238m'
                line.append(style + ch + ('\033[0m' if style else ''))
            out.append(''.join(line) + ('\r\n' if row < self.rows - 1 else ''))
        text = self.under(t, status, leaving)
        return ''.join(out) + text

    def under(self, t, status, leaving):
        """The tagline and what's loading, centred under the logo."""
        y = self.top + len(self.logo) + 2
        lines = [('\033[38;5;245m', TAGLINE), ('\033[38;5;240m', status)]
        out = []
        for i, (style, text) in enumerate(lines):
            if leaving is not None and not self.calm:
                text = fc.dust(text, leaving / OUTRO, i)
            text = fc.clip(text, self.cols - 2)
            x = max(0, (self.cols - fc.cells(text)) // 2)
            out.append(f'\033[{y + i + 1};{x + 1}H{style}{text}\033[0m\033[K')
        return ''.join(out)


def intro(status, ready):
    """Show the opening screen while things load: status() says what's happening, ready() when
    it's all there. Returns once ready (or after MAX), or at a key."""
    if os.environ.get('MSGCANVAS_NO_INTRO') or not sys.stdin.isatty() or not sys.stdout.isatty():
        return
    import termios, tty
    cols, rows = os.get_terminal_size()
    if cols < 24 or rows < 8:
        return
    screen = Screen(cols, rows, calm=bool(os.environ.get('CDECK_CALM')))
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    sys.stdout.write('\033[?1049h\033[?25l\033[2J')
    tty.setcbreak(fd)
    termios.tcflush(fd, termios.TCIFLUSH)       # nothing typed before it showed counts
    try:
        t0, left = time.monotonic(), None
        while True:
            t = time.monotonic() - t0
            if left is None and (t >= MAX or (t >= MIN and ready())):
                left = t
            if left is not None and (t - left >= OUTRO or screen.calm):
                break
            sys.stdout.write('\033[?2026h' + screen.frame(t, status(), None if left is None else t - left) + '\033[?2026l')
            sys.stdout.flush()
            if select.select([fd], [], [], 1 / 30)[0]:
                os.read(fd, 64)
                if left is None:
                    left = t
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write('\033[0m\033[2J\033[?25h\033[?1049l')
        sys.stdout.flush()
