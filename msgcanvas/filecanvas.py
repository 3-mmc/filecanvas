# Vendored from filecanvas (github.com/3-mmc/filecanvas) at e74715d: do not edit here.
# Change it there and run tools/vendor-filecanvas.py; msgcanvas's own choices are in its providers.
"""filecanvas: the file system as one canvas you move across.

Every folder you open keeps its place on a tidy-tree layout (depth to the right, children
spread down, each block of children centred on its parent and shifted only as much as it
needs to keep clear of its neighbours). A camera eases after the selection and only moves
when the selection leaves a box around the middle of the screen. The path you're on is drawn
in the theme's path colour; everything else dims with its distance from it.

    python3 filecanvas.py [START]          # prints the chosen path on stdout, nothing on Esc
    cd "$(python3 filecanvas.py)"

In your shell: `cdi` goes to a folder you choose, ⌃T puts a path in at the cursor:

    eval "$(python3 /path/to/filecanvas.py --init bash)"     # or zsh; fish: --init fish | source

Keys: ↑↓ siblings · → or Tab open · ← parent (the branch stays open) · type to filter this
column · Backspace unfilters · ⌃X fold everything off the current path · Enter choose · Esc.
Settings are flags (--help), and any of them can live in ~/.config/filecanvas/config.json.

The file system is one provider of the tree (FileSystem); anything else can be another
(Provider, and Tree for nested dicts): `pick(provider=...)`.
"""
import argparse, json, os, re, select, shutil, stat, subprocess, sys, threading, unicodedata
from concurrent.futures import ThreadPoolExecutor

CONFIG = os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'),
                      'filecanvas', 'config.json')

# -- colour

# A theme names colours by role. A colour is a 256-colour number or '#rrggbb'.
#   sel_fg/sel_bg   the chosen entry         path       folders on the path you're on
#   path_line       connectors on the path   line       other connectors (brightest)
#   dir / file      entries, when LS_COLORS isn't used, or has nothing for them
#   stub            "(empty)", "↓ 12 more"   filter     the filter chip
#   hint            the key hints            bg         what dimmed colours fade into
#   rest            branches you're not on (--grey-rest)
#   ghost           the screen behind, dimmed (--backdrop)
#   home_* seg_* cwd_fg sep_fg               the path bar, as powerline-shell draws a prompt
#   repo_clean_* repo_dirty_*                the bar's git chip
#   git_dirty git_staged git_untracked git_conflict   the marks beside changed entries
# `ls_colors` is the theme's default for --ls-colors; you can still change it.
THEMES = {
    'powerline': {  # powerline-shell's default theme
        'bg': '#121212', 'sel_bg': 31, 'sel_fg': 15, 'path': 254, 'path_line': 31,
        'line': 244, 'dir': 110, 'file': 250, 'stub': 242, 'filter': 148, 'hint': 242,
        'home_bg': 31, 'home_fg': 15, 'seg_bg': 237, 'seg_fg': 250, 'cwd_fg': 254, 'sep_fg': 244,
        'repo_clean_bg': 148, 'repo_clean_fg': 0, 'repo_dirty_bg': 161, 'repo_dirty_fg': 15,
        'git_dirty': 161, 'git_staged': 148, 'git_untracked': 130, 'git_conflict': 9,
        'rest': 248, 'ghost': 237, 'ls_colors': True,
    },
    'amber': {  # cdeck's
        'bg': '#121212', 'sel_bg': 214, 'sel_fg': 232, 'path': 214, 'path_line': 214,
        'line': 244, 'dir': 222, 'file': 250, 'stub': 240, 'filter': 214, 'hint': 240,
        'home_bg': 214, 'home_fg': 232, 'seg_bg': 94, 'seg_fg': 222, 'cwd_fg': 230, 'sep_fg': 172,
        'repo_clean_bg': 136, 'repo_clean_fg': 232, 'repo_dirty_bg': 166, 'repo_dirty_fg': 232,
        'git_dirty': 166, 'git_staged': 142, 'git_untracked': 137, 'git_conflict': 196,
        'rest': 246, 'ghost': 237, 'ls_colors': False,
    },
    'nord': {
        'bg': '#2e3440', 'sel_bg': '#88c0d0', 'sel_fg': '#2e3440', 'path': '#88c0d0',
        'path_line': '#81a1c1', 'line': '#616e88', 'dir': '#81a1c1', 'file': '#d8dee9',
        'stub': '#4c566a', 'filter': '#ebcb8b', 'hint': '#4c566a',
        'home_bg': '#5e81ac', 'home_fg': '#eceff4', 'seg_bg': '#3b4252', 'seg_fg': '#d8dee9',
        'cwd_fg': '#eceff4', 'sep_fg': '#4c566a',
        'repo_clean_bg': '#a3be8c', 'repo_clean_fg': '#2e3440', 'repo_dirty_bg': '#bf616a',
        'repo_dirty_fg': '#eceff4', 'git_dirty': '#d08770', 'git_staged': '#a3be8c',
        'git_untracked': '#b48ead', 'git_conflict': '#bf616a',
        'rest': '#a7adba', 'ghost': '#434c5e', 'ls_colors': True,
    },
    'gruvbox': {
        'bg': '#282828', 'sel_bg': '#fabd2f', 'sel_fg': '#282828', 'path': '#fabd2f',
        'path_line': '#d79921', 'line': '#7c6f64', 'dir': '#83a598', 'file': '#ebdbb2',
        'stub': '#665c54', 'filter': '#b8bb26', 'hint': '#665c54',
        'home_bg': '#458588', 'home_fg': '#ebdbb2', 'seg_bg': '#504945', 'seg_fg': '#d5c4a1',
        'cwd_fg': '#fbf1c7', 'sep_fg': '#7c6f64',
        'repo_clean_bg': '#98971a', 'repo_clean_fg': '#282828', 'repo_dirty_bg': '#cc241d',
        'repo_dirty_fg': '#ebdbb2', 'git_dirty': '#fe8019', 'git_staged': '#b8bb26',
        'git_untracked': '#d3869b', 'git_conflict': '#fb4934',
        'rest': '#a89984', 'ghost': '#504945', 'ls_colors': True,
    },
    'solarized-dark': {
        'bg': '#002b36', 'sel_bg': '#268bd2', 'sel_fg': '#fdf6e3', 'path': '#93a1a1',
        'path_line': '#268bd2', 'line': '#586e75', 'dir': '#268bd2', 'file': '#839496',
        'stub': '#586e75', 'filter': '#b58900', 'hint': '#586e75',
        'home_bg': '#268bd2', 'home_fg': '#fdf6e3', 'seg_bg': '#073642', 'seg_fg': '#93a1a1',
        'cwd_fg': '#eee8d5', 'sep_fg': '#586e75',
        'repo_clean_bg': '#859900', 'repo_clean_fg': '#002b36', 'repo_dirty_bg': '#dc322f',
        'repo_dirty_fg': '#fdf6e3', 'git_dirty': '#cb4b16', 'git_staged': '#859900',
        'git_untracked': '#6c71c4', 'git_conflict': '#dc322f',
        'rest': '#839496', 'ghost': '#073642', 'ls_colors': True,
    },
}

# Separators: (hard, soft, left cap of the chosen entry, right cap). Plain needs no special font.
GLYPHS = {
    'powerline': ('', '', '', ''),
    'round': ('', '', '', ''),   # needs a Nerd Font
    'plain': ('', '/', '', ''),
}
MARKS = {  # git state -> (role, glyph, plain glyph)
    4: ('git_conflict', '✖', '!'), 3: ('git_dirty', '●', 'M'),
    2: ('git_staged', '✚', '+'), 1: ('git_untracked', '?', '?'),
}

# How bright something is, by how far it is from the choice (or the path): 1 is full colour.
STRENGTH = (1.0, .9, .8, .72, .64, .56, .5, .45, .4)

XTERM16 = [(0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0), (0, 0, 238), (205, 0, 205),
           (0, 205, 205), (229, 229, 229), (127, 127, 127), (255, 0, 0), (0, 255, 0),
           (255, 255, 0), (92, 92, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255)]
LEVELS = (0, 95, 135, 175, 215, 255)


def rgb(c):
    if isinstance(c, tuple):
        return c
    if isinstance(c, str):
        c = c.lstrip('#')
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    if c < 16:
        return XTERM16[c]
    if c < 232:
        c -= 16
        return LEVELS[c // 36], LEVELS[c // 6 % 6], LEVELS[c % 6]
    return (8 + 10 * (c - 232),) * 3


def nearest(t):
    """The 256-colour number closest to an RGB triple."""
    def d(a):
        return sum((x - y) ** 2 for x, y in zip(a, t))
    cube = [min(range(6), key=lambda i: abs(LEVELS[i] - v)) for v in t]
    n = 16 + 36 * cube[0] + 6 * cube[1] + cube[2]
    g = max(0, min(23, round((sum(t) / 3 - 8) / 10)))
    return min((n, 232 + g), key=lambda i: d(rgb(i)))


class Paint:
    def __init__(self, theme, truecolor):
        self.t, self.true = theme, truecolor
        self.bg = rgb(theme['bg'])

    def code(self, c, s, ground):
        c = self.t.get(c, c) if isinstance(c, str) and not c.startswith('#') else c
        if s >= 0.999 and isinstance(c, int):
            return f'{ground};5;{c}'     # the exact palette colour, as `ls` would show it
        r = tuple(round(b + (x - b) * s) for x, b in zip(rgb(c), self.bg))
        return f'{ground};2;{r[0]};{r[1]};{r[2]}' if self.true else f'{ground};5;{nearest(r)}'

    def fg(self, c, s=1.0, bold=False):
        return f"\033[{'1;' if bold else ''}{self.code(c, s, 38)}m"

    def on(self, fg, bg, bold=False):
        return f"\033[{'1;' if bold else ''}{self.code(fg, 1, 38)};{self.code(bg, 1, 48)}m"


def cells(text):
    """How many columns `text` takes on a terminal: CJK and the like take two, accents none."""
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in 'WF' else 1
               for c in text)


def clip(text, room):
    """`text` cut to `room` columns, ending in … when it was cut."""
    if cells(text) <= room:
        return text
    out, used = '', 0
    for c in text:
        if used + cells(c) > room - 1:
            break
        out, used = out + c, used + cells(c)
    return out + '…'


def printable(text):
    return ''.join(c if c.isprintable() else '?' for c in text)


def wrap(text, width):
    """`text` as lines of at most `width` columns, broken at spaces where it can be."""
    width = max(width, 2)
    out = []
    for para in text.replace('\r', '').expandtabs(4).split('\n'):
        line = None
        for word in printable(para).split(' '):
            if line is not None and cells(line) + 1 + cells(word) <= width:
                line += ' ' + word
                continue
            if line is not None:
                out.append(line)
            while cells(word) > width:          # longer than a line on its own: cut it
                head = clip(word, width + 1)[:-1]
                out.append(head)
                word = word[len(head):]
            line = word
        out.append(line or '')
    return out


def truecolor_terminal():
    if os.environ.get('COLORTERM') in ('truecolor', '24bit'):
        return True
    if os.environ.get('TMUX'):
        return False   # tmux passes 24-bit colour on only when it's set up to; don't guess
    return bool(os.environ.get('WT_SESSION')) or os.environ.get('TERM_PROGRAM') in (
        'iTerm.app', 'WezTerm', 'vscode', 'ghostty')


def ls_colors():
    """LS_COLORS as {key: (colour, bold)}: 'di', 'ln', 'ex', … and '*.ext' (lower-cased)."""
    out = {}
    for item in os.environ.get('LS_COLORS', '').split(':'):
        key, _, value = item.partition('=')
        if not key or not value:
            continue
        codes, colour, bold = value.split(';'), None, False
        i = 0
        while i < len(codes):
            c = codes[i]
            if c in ('1', '01'):
                bold = True
            elif c.isdigit() and (30 <= int(c) <= 37 or 90 <= int(c) <= 97):
                colour = int(c) - 30 if int(c) < 90 else int(c) - 82
            elif c == '38' and i + 2 < len(codes) and codes[i + 1] == '5':
                colour, i = int(codes[i + 2]), i + 2
            elif c == '38' and i + 4 < len(codes) and codes[i + 1] == '2':
                colour, i = tuple(int(x) for x in codes[i + 2:i + 5]), i + 4
            i += 1
        if colour is not None or bold:
            out[key.lower() if key.startswith('*') else key] = (colour, bold)
    return out


# -- pictures in the pane

class Picture:
    """A picture for the reading pane: preview() returns one, or a list of text and pictures.
    chafa draws it as coloured blocks, as wide as the pane and at most `rows` high, so it scrolls
    with the text in any terminal; without chafa it's a line saying what it is."""
    SGR = re.compile(r'\x1b\[[0-9;]*m')
    # any other CSI (not ending in m), OSC / DCS / PM / APC strings, and any other ESC pair
    OTHER = re.compile(r'\x1b\[[0-9;?]*[A-Za-ln-z@`]|\x1b[\]P^_][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[^\[]')

    def __init__(self, path, name=None, rows=20):
        self.path, self.name, self.rows = path, name or os.path.basename(path), rows
        self.drawn = {}

    def lines(self, width, height, truecolor=True):
        key = (width, min(height, self.rows), truecolor)
        if key not in self.drawn:
            self.drawn[key] = self.draw(*key)
        return self.drawn[key]

    def draw(self, width, height, truecolor):
        note = ['🖼 ' + clip(printable(self.name), max(1, width - 2))]
        if not shutil.which('chafa') or width < 4 or height < 2:
            return note
        try:
            out = subprocess.run(['chafa', '--format=symbols', '--colors=' + ('full' if truecolor else '256'),
                                  f'--size={width}x{height}', '--animate=off', '--polite=on', self.path],
                                 capture_output=True, timeout=10).stdout.decode('utf-8', 'replace')
        except (OSError, subprocess.SubprocessError):
            return note
        return self.clean(out) or note

    def clean(self, out):
        """chafa's lines with only their colours: other escapes (cursor, modes) go whole, then
        stray control characters; each line ends in a reset."""
        lines = []
        for line in out.rstrip('\n').split('\n'):
            line = self.OTHER.sub('', line)
            codes, texts = self.SGR.findall(line), self.SGR.split(line)
            kept = [''.join(c for c in t if c.isprintable()) for t in texts]
            lines.append(''.join(k + (codes[i] if i < len(codes) else '') for i, k in enumerate(kept)) + '\033[0m')
        return [l for l in lines if l != '\033[0m']


# -- git

class Git:
    """`git status` per repository, run in the background; the canvas redraws when it lands."""
    RANK = {'DD': 4, 'AU': 4, 'UD': 4, 'UA': 4, 'DU': 4, 'AA': 4, 'UU': 4}

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.roots, self.info, self.pending = {}, {}, set()
        self.lock = threading.Lock()
        self.changed = False
        self.pool = ThreadPoolExecutor(4) if enabled else None

    def root(self, d):
        walked = []
        while True:
            if d in self.roots:
                r = self.roots[d]
                break
            walked.append(d)
            if os.path.exists(os.path.join(d, '.git')):
                r = d
                break
            up = os.path.dirname(d)
            if up == d:
                r = None
                break
            d = up
        for w in walked:
            self.roots[w] = r
        return r

    def status(self, root):
        with self.lock:
            if root in self.info:
                return self.info[root]
            if root not in self.pending:
                self.pending.add(root)
                self.pool.submit(self._run, root)
        return None

    def _run(self, root):
        files, prefixes, agg, dirty = {}, {}, {}, False
        try:
            raw = subprocess.run(['git', '-C', root, 'status', '--porcelain=v1', '-z',
                                  '--ignored=matching', '-unormal'],
                                 capture_output=True, timeout=5).stdout
            parts = os.fsdecode(raw).split('\0')
            i = 0
            while i < len(parts):
                e = parts[i]
                i += 1
                if len(e) < 4:
                    continue
                xy, rel = e[:2], e[3:]
                if xy[0] in 'RC':
                    i += 1   # the name it had before
                path = os.path.join(root, rel.rstrip('/'))
                if xy == '!!':
                    prefixes[path] = 'ignored'
                    continue
                rank = 1 if xy == '??' else self.RANK.get(xy) or (3 if xy[1] in 'MDT' else 2)
                dirty = True
                files[path] = rank
                if xy == '??' and rel.endswith('/'):
                    prefixes[path] = 1
                d = os.path.dirname(path)
                while len(d) >= len(root):
                    agg[d] = max(agg.get(d, 0), rank)
                    if d == root:
                        break
                    d = os.path.dirname(d)
        except (OSError, subprocess.SubprocessError):
            pass
        with self.lock:
            self.info[root] = {'files': files, 'prefixes': prefixes, 'agg': agg, 'dirty': dirty}
            self.pending.discard(root)
            self.changed = True

    def wait(self):
        while self.pending:
            threading.Event().wait(0.02)

    def state(self, node):
        """(rank 0-4, ignored, whether the rank is of something inside a folder)."""
        if not self.enabled or not isinstance(node, Node):
            return 0, False, False
        r = self.root(node.path if node.is_dir else os.path.dirname(node.path))
        info = r and self.status(r)
        if not info:
            return 0, False, False
        if node.path == r:
            return info['agg'].get(r, 0), False, True
        rank = info['files'].get(node.path, 0)
        p = node.path
        while len(p) > len(r):
            hit = info['prefixes'].get(p)
            if hit == 'ignored':
                return rank, True, False
            if hit and not rank:
                rank = hit
            p = os.path.dirname(p)
        if not rank and node.is_dir:
            return info['agg'].get(node.path, 0), False, True
        return rank, False, False

    def branch(self, path):
        """(name, dirty) for the repository holding `path`, or None."""
        if not self.enabled:
            return None
        r = self.root(path if os.path.isdir(path) else os.path.dirname(path))
        if not r:
            return None
        git = os.path.join(r, '.git')
        try:
            if os.path.isfile(git):
                with open(git) as f:
                    git = os.path.join(r, f.read().split('gitdir:', 1)[1].strip())
            with open(os.path.join(git, 'HEAD')) as f:
                head = f.read().strip()
        except (OSError, IndexError):
            return None
        name = head[16:] if head.startswith('ref: refs/heads/') else head[:7]
        info = self.status(r)
        return name, bool(info and info['dirty'])


# -- the tree

class Node:
    """One entry on the canvas. `path` is its id (unique in the tree, what Enter returns), `is_dir`
    that it can be opened, `kind` and `data` its provider's own: what it is and what it holds."""
    def __init__(self, path, name, is_dir, parent=None, kind='fi', source=None, data=None):
        self.path, self.name, self.is_dir, self.parent, self.kind = path, name, is_dir, parent, kind
        self.source = source or (parent.source if parent else None)   # its Provider
        self.data = data
        self.kids = None        # loaded lazily
        self.open = False
        self.sel = 0            # index into shown(): the remembered / selected child
        self.filter = ""
        self.error = None
        self.row = self.depth = 0
        self.dirs_only = parent.dirs_only if parent else False

    def load(self):
        if self.kids is None:
            try:
                self.kids = list(self.source.children(self))
            except OSError as e:
                self.kids, self.error = [], e.strerror or "unreadable"
        return self.kids

    def shown(self):
        f = self.filter.casefold()
        kids = [k for k in self.load() if k.is_dir] if self.dirs_only else self.load()
        return [k for k in kids if f in k.name.casefold()] if f else kids

    def label(self):
        return self.source.label(self)


# -- where the tree comes from

class Provider:
    """Where a canvas's tree comes from: the canvas lays it out, draws it and moves across it; a
    provider says what's in it. Subclass it and hand one over (`pick(provider=...)`,
    `options(provider=...)`); only children() is needed, the rest have plain defaults.

    These run while drawing, so they should be quick. Slow work goes to a thread that sets
    `changed` when it lands (the canvas redraws, and clears it), with busy() true until it has;
    when what's in the tree has changed, the thread calls invalidate() instead.

    A provider can keep the canvas open: choose() returning None stays (Enter opens a branch),
    and `pane` shows preview() beside the tree, a reading pane."""
    changed = False
    pane = False      # the reading pane's default (--pane wins)

    def root(self):
        return Node('', '', True, source=self)

    def children(self, node):
        """`node`'s children, Nodes with `node` as their parent, in the order they're shown. Raise
        OSError when they can't be had: its strerror is shown in their place."""
        return []

    def locate(self, start):
        """Where to begin, from the caller's `start`: the ids (paths) of the nodes from below the
        root down to it, and text to filter its children by (a half-typed name)."""
        return [], ''

    def label(self, node):
        return node.name

    def colour(self, node):
        """(colour, bold) at full strength, a theme role or a colour; None for the theme's dir / file."""
        return None

    def mark(self, node):
        """A mark beside the entry, (theme role, glyph, glyph without special fonts); or None."""
        return None

    def dim(self, node):
        """How bright the entry is beside its neighbours: 1 the same, less fainter."""
        return 1.0

    def bar(self, node, plain):
        """The bar along the bottom with `node` chosen: (segments, tail), each a list of (text,
        fg, bg, bold) in theme roles. When there's no room, segments after the first give way."""
        names = []
        while node.parent:
            names.append(self.label(node)); node = node.parent
        segs = [(self.label(node) or '⌂', 'home_fg', 'home_bg', False)]
        names.reverse()
        for i, name in enumerate(names):
            last = i == len(names) - 1
            segs.append((name, 'cwd_fg' if last else 'seg_fg', 'seg_bg', last))
        return segs, []

    def choose(self, node):
        """Enter: what the canvas ends with (pick() returns it), or None to stay on it."""
        return node.path

    def actions(self, node):
        """Keys handled on `node`: [(key, name, hint)]. A key is a control character the canvas
        and the usual terminals leave free (⌃A ⌃D ⌃E ⌃F ⌃K ⌃L ⌃N ⌃O ⌃P ⌃R ⌃S ⌃T ⌃W ⌃Y: not ⌃B,
        tmux's, nor ⌃V, Windows Terminal's paste); the hint ('⌃R reply') is shown along the bottom."""
        return []

    def act(self, node, name, canvas):
        """Do the action `name` on `node`. canvas.ask() opens a typing line, canvas.outside()
        runs something with the terminal (an editor, another canvas), canvas.say() tells you how it
        went. A value ends the canvas, as choose()'s does."""
        return None

    def preview(self, node):
        """What the reading pane shows for `node`: text (wrapped to fit), or None for nothing."""
        return None

    def preview_end(self, node):
        """Whether the pane opens on the end of `node`'s preview (a conversation's newest lines,
        a log's latest), and keeps to the end as it grows while you're there."""
        return False

    def invalidate(self, path=None):
        """From any thread: fetch the children of the node with id `path` (of every node, with
        None) again before the next frame. What's open and chosen stays where the ids still are."""
        with STALE:
            self.__dict__.setdefault('_stale', set()).add(path)
        self.changed = True

    def stale(self):
        """The ids invalidate() was given since last asked."""
        with STALE:
            return self.__dict__.pop('_stale', set())

    def busy(self):
        return False

    def wait(self):
        while self.busy():
            threading.Event().wait(0.02)


STALE = threading.Lock()


class FileSystem(Provider):
    """The file system: folders first, dotfiles after, LS_COLORS, git's marks and branch."""
    def __init__(self, git=True, ls=False):
        self.git = Git(git)
        self.ls = ls_colors() if ls else {}

    @property
    def changed(self):
        return self.git.changed

    @changed.setter
    def changed(self, value):
        self.git.changed = value

    def root(self):
        return Node('/', '/', True, kind='di', source=self)

    def children(self, node):
        out = []
        with os.scandir(node.path) as it:
            for e in it:
                is_dir, key = kind(e)
                out.append(Node(e.path, e.name, is_dir, node, key))
        out.sort(key=lambda n: (not n.is_dir, n.name.startswith('.'), n.name.casefold()))
        return out

    def locate(self, start):
        # A start that doesn't exist yet is a half-typed path: its folder, filtered by the rest.
        start = os.path.abspath(os.path.expanduser(start or '.'))
        typed = ''
        if not os.path.exists(start):
            start, typed = os.path.dirname(start), os.path.basename(start)
        parts = [p for p in start.split('/') if p]
        return ['/' + '/'.join(parts[:i + 1]) for i in range(len(parts))], typed

    def label(self, node):
        return node.name + ('/' if node.is_dir and node.name != '/' else '')

    def colour(self, n):
        if not self.ls:
            return None
        hit = self.ls.get(n.kind)
        if not hit and n.kind in ('fi', 'ex'):
            dot = n.name.lower()
            while '.' in dot and not hit:
                dot = dot[dot.index('.') + 1:]
                hit = self.ls.get('*.' + dot)
            hit = hit or (self.ls.get('ex') if n.kind == 'ex' else None) or self.ls.get('fi')
        if hit and hit[0] is not None:
            c, bold = hit
            # Bold basic colours show as their bright versions in most terminals; dim from those.
            return (c + 8 if bold and isinstance(c, int) and c < 8 else c), bold
        if hit:
            return ('dir' if n.is_dir else 'file'), hit[1]
        return None

    def mark(self, node):
        rank, _, inside = self.git.state(node)
        if not rank:
            return None
        role, glyph, ascii_ = MARKS[rank]
        return (role, '•', '*') if inside else (role, glyph, ascii_)

    def dim(self, node):
        s = 0.75 if node.name.startswith('.') else 1.0
        return s * 0.65 if self.git.state(node)[1] else s   # ignored by git

    def bar(self, node, plain):
        path = node.path
        home = os.path.expanduser('~')
        segs = []
        if path == home or path.startswith(home + '/'):
            segs.append(('~', 'home_fg', 'home_bg', False))
            rest = path[len(home):].strip('/')
        else:
            segs.append(('/', 'seg_fg', 'seg_bg', False))
            rest = path.strip('/')
        parts = rest.split('/') if rest else []
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            segs.append((part, 'cwd_fg' if last else 'seg_fg', 'seg_bg', last))
        tail = []
        b = self.git.branch(path)
        if b:
            name, dirty = b
            tail.append(((' ' if not plain else '') + name,
                         'repo_dirty_fg' if dirty else 'repo_clean_fg',
                         'repo_dirty_bg' if dirty else 'repo_clean_bg', False))
        return segs, tail

    def preview(self, node):
        if node.is_dir:
            return None
        try:
            with open(node.path, 'rb') as f:
                head = f.read(32768)
        except OSError as e:
            return '(' + (e.strerror or 'unreadable') + ')'
        if b'\0' in head:
            return f'(binary, {os.path.getsize(node.path):,} bytes)'
        return head.decode(errors='replace')

    def busy(self):
        return bool(self.git.pending)

    def wait(self):
        self.git.wait()


class Tree(Provider):
    """Nested dicts as a tree, for trying the canvas on anything: a dict is a branch of
    {name: subtree}, anything else a leaf holding it (node.data). Ids are /-joined names.

        pick(provider=Tree({'fruit': {'apple': 1, 'pear': 2}, 'bread': None}), start='fruit')

    The reading pane shows a leaf's value (a list's items one to a line)."""
    pane = True

    def __init__(self, data, name='⌂'):
        self.data, self.name = data, name

    def root(self):
        return Node('', self.name, True, source=self, data=self.data)

    def children(self, node):
        d = self.data   # from the top, so a branch replaced since is seen (invalidate)
        for part in node.path.split('/')[1:]:
            d = d.get(part) if isinstance(d, dict) else None
        return [Node(f'{node.path}/{k}', str(k), isinstance(v, dict), node, data=v)
                for k, v in (d.items() if isinstance(d, dict) else ())]

    def locate(self, start):
        parts = [p for p in (start or '').split('/') if p and p != '.']
        return ['/' + '/'.join(parts[:i + 1]) for i in range(len(parts))], ''

    def preview(self, node):
        if node.is_dir or node.data is None:
            return None
        return '\n'.join(map(str, node.data)) if isinstance(node.data, list) else str(node.data)


def kind(entry):
    """(is_dir, LS_COLORS key) for a directory entry."""
    try:
        link = entry.is_symlink()
        st = entry.stat()
    except OSError:
        return False, 'or'           # a link to nothing
    if stat.S_ISDIR(st.st_mode):
        return True, 'ln' if link else 'di'
    if link:
        return False, 'ln'
    for test, key in ((stat.S_ISFIFO, 'pi'), (stat.S_ISSOCK, 'so'), (stat.S_ISBLK, 'bd'), (stat.S_ISCHR, 'cd')):
        if test(st.st_mode):
            return False, key
    return False, 'ex' if st.st_mode & 0o111 else 'fi'


class Stub:
    """A stand-in row: '↓ 12 more', '(empty)'."""
    is_dir, open, kids, name = False, False, [], ''

    def __init__(self, text, parent):
        self.text, self.parent, self.row, self.depth = text, parent, 0, 0

    def label(self):
        return self.text


def window(node, limit):
    """The children drawn for an open node: all of them, or at most `limit` around the selected one."""
    items = node.shown()
    if not items:
        return [Stub('(' + (node.error or ('no match' if node.filter else 'empty')) + ')', node)]
    if not limit or len(items) <= limit:
        return items
    start = max(0, min(node.sel - limit // 2, len(items) - limit))
    out = items[start:start + limit]
    if start:
        out = [Stub(f'↑ {start} more', node)] + out[1:]
    if start + limit < len(items):
        out = out[:-1] + [Stub(f'↓ {len(items) - start - limit + 1} more', node)]
    return out


def is_open(n, focus, peek):
    return n.is_dir and (n.open or (peek and n is focus))


def layout(n, opts, focus):
    """Rows relative to `n` for its subtree; returns its contour: [(top, bottom)] per depth."""
    if not is_open(n, focus, opts.peek):
        return [(0, 0)]
    kids = window(n, opts.limit)
    n._shown = kids
    acc, ys = [], []
    for k in kids:
        c = layout(k, opts, focus)
        y = ys[-1] + 1 if ys else 0
        for d in range(min(len(acc), len(c))):
            # Keep clear of what's already placed at every depth; a blank row between cousins.
            y = max(y, acc[d][1] - c[d][0] + 1 + (1 if d else 0))
        for d, (t, b) in enumerate(c):
            if d < len(acc):
                acc[d] = (min(acc[d][0], t + y), max(acc[d][1], b + y))
            else:
                acc.append((t + y, b + y))
        ys.append(y)
    shift = -((ys[0] + ys[-1]) // 2)    # centred on the parent's row
    for k, y in zip(kids, ys):
        k._rel = y + shift
    return [(0, 0)] + [(t + shift, b + shift) for t, b in acc]


def place(n, row, depth, focus, opts, out):
    n.row, n.depth = row, depth
    out.append(n)
    if is_open(n, focus, opts.peek):
        for k in n._shown:
            place(k, row + k._rel, depth + 1, focus, opts, out)


class Canvas:
    def __init__(self, start, opts):
        self.opts = opts
        self.theme = opts.colours
        self.paint = Paint(self.theme, opts.truecolor)
        self.glyphs = GLYPHS[opts.glyphs]
        self.source = opts.provider or FileSystem(opts.git, opts.ls_colors)
        self.root = self.source.root()
        self.root.dirs_only = opts.dirs_only
        ids, typed = self.source.locate(start)
        node = self.root
        for want in ids:
            node.open = True
            for k in node.load():
                if k.path == want:
                    node.sel, node = node.shown().index(k) if k in node.shown() else 0, k
                    break
        if node.is_dir:
            node.open = True
            node.filter = typed
            # on the start folder itself (choosing a folder), or on what's in it
            self.focus = node if opts.on_start and not typed or not node.shown() else node.shown()[0]
        else:
            self.focus = node
        self.backdrop = opts.backdrop if isinstance(opts.backdrop, dict) else {}
        self.cam = None       # (x, y) world position of the top-left corner, floats
        self.target = None
        self.pane = self.source.pane if opts.pane is None else opts.pane
        self.scroll = 0                     # the reading pane's first line
        self.page = 10                      # and how many it shows
        self._preview = (None, 0, None)     # (node, width, its lines) last shown there
        self._at_end = False                # the pane showed its last line
        self.asking = None                  # (prompt, text, done, choices): the typing line, open
        self.message = None                 # what say() said, until the next key
        self.waiting = []                   # what outside() was given, to run with the terminal

    # -- for actions
    def ask(self, prompt, done, text='', choices=None):
        """Open the typing line along the bottom: Enter calls done(text), Esc closes it. With
        `choices` (letters, 'yne'), one of those keys answers at once: done(letter). What done()
        returns ends the canvas unless it's None; it may ask again."""
        self.asking = (prompt, text, done, choices)

    def say(self, text):
        """A line in the bar until the next key: what an action did."""
        self.message = text

    def outside(self, fn):
        """Run fn() with the terminal to itself, after this key: the canvas steps aside and comes
        back. fn runs programs with terminal() (an editor, a pager) or pick()s on another canvas."""
        self.waiting.append(fn)

    def drain(self):
        """Run what outside() was given (run() does, with the terminal set aside)."""
        while self.waiting:
            fn = self.waiting.pop(0)
            try:
                fn()
            except Exception as e:      # an action that fails says so; it doesn't take the canvas down
                self.say(f'failed: {e}')

    # -- what changed underneath
    def refresh(self):
        """Fetch again what the provider invalidated, keeping what's open and chosen by id."""
        stale = self.source.stale()
        if not stale:
            return
        todo = [self.root]
        while todo:
            n = todo.pop()
            if n.kids is None:
                continue
            if None in stale or n.path in stale:
                self.reload(n)
            todo.extend(n.kids)
        self._preview = (self._preview[0], -1, None)    # fetched again, scrolled where it was
        self.reattach()

    def reload(self, node):
        old = {k.path: k for k in node.kids}
        node.kids, node.error = None, None
        kids = []
        for k in node.load():
            o = old.get(k.path)
            if o:   # still there: the same node, with what it holds now
                o.name, o.is_dir, o.kind, o.data = k.name, k.is_dir, k.kind, k.data
                o.open = o.open and o.is_dir
                k = o
            kids.append(k)
        node.kids = kids

    def reattach(self):
        """After a reload: the focus back on the tree, beside where it was if it's gone."""
        chain, n = [], self.focus
        while n.parent:
            chain.append(n); n = n.parent
        for k in reversed(chain):
            if not any(k is x for x in n.kids or ()):
                shown = n.shown()
                self.focus = shown[min(n.sel, len(shown) - 1)] if shown else n
                break
            n = k
        p = self.focus.parent
        if p and self.focus in p.shown():
            p.sel = p.shown().index(self.focus)

    # -- moving
    def siblings(self):
        p = self.focus.parent
        return p.shown() if p else [self.focus]

    def move(self, step):
        p = self.focus.parent
        sib = self.siblings()
        if not p or not sib:
            return
        i = (sib.index(self.focus) + step) % len(sib) if self.focus in sib else 0
        if self.opts.fold and self.focus.open:
            self.focus.open = False
        p.sel, self.focus = i, sib[i]

    def enter(self):
        f = self.focus
        if not f.is_dir:
            return
        f.open = True
        kids = f.shown()
        if kids:
            f.sel = min(f.sel, len(kids) - 1)
            self.focus = kids[f.sel]

    def leave(self):
        if self.focus.parent:
            self.focus = self.focus.parent

    def refilter(self, text):
        p = self.focus.parent
        if not p:
            return
        p.filter = text
        sib = p.shown()
        if self.focus in sib:
            p.sel = sib.index(self.focus)
        elif sib:
            p.sel, self.focus = 0, sib[0]
        # nothing matches: focus stays where it was, the column shows "(no match)"

    def fold(self):
        on = set()
        n = self.focus
        while n:
            on.add(id(n)); n = n.parent
        stack = [self.root]
        while stack:
            n = stack.pop()
            if n.kids:
                stack.extend(n.kids)
            if id(n) not in on:
                n.open = False

    # -- colour of one entry
    def colour(self, n):
        """(colour, bold) for an entry at full strength."""
        if isinstance(n, Stub):
            return 'stub', False
        return self.source.colour(n) or (('dir' if n.is_dir else 'file'), False)

    # -- drawing
    def frame(self, w, h, animate=True):
        o, P, T = self.opts, self.paint, self.theme
        hard, soft, cap_l, cap_r = self.glyphs
        plain = o.glyphs == 'plain'
        self.refresh()
        full = w            # the tree gets what the reading pane leaves
        pane_w = int(w * o.pane_width) if self.pane and w >= 60 else 0
        w -= pane_w
        layout(self.root, o, self.focus)
        nodes = []
        place(self.root, 0, 0, self.focus, o, nodes)

        def text_of(n):
            return clip(printable(n.label()), o.colmax)

        marks = {}   # (the mark beside it, how bright it is) per entry
        for n in nodes:
            marks[id(n)] = (None, 1.0) if isinstance(n, Stub) else (self.source.mark(n), self.source.dim(n))
        widths = {}
        for n in nodes:
            wd = cells(text_of(n)) + (2 if marks[id(n)][0] else 0)
            widths[n.depth] = max(widths.get(n.depth, 0), wd)
        extra = o.gap + (1 if cap_r else 0)
        xs = [0]
        for d in range(max(widths) + 1):
            xs.append(xs[-1] + widths.get(d, 0) + extra)

        path = set()
        n = self.focus
        while n:
            path.add(id(n)); n = n.parent

        # Camera: keep the focus inside a box round the middle (a fraction of the view).
        view_h = h - 2 - (1 if o.header else 0)
        fx, fy = xs[self.focus.depth], self.focus.row
        aim_x, aim_y = w * o.across, view_h / 2
        if self.cam is None:
            self.cam = (fx - aim_x, fy - aim_y)
        tx, ty = self.target or self.cam
        half_w, half_h = w * o.dead / 2, view_h * o.dead / 2
        sx, sy = fx - tx, fy - ty
        if not aim_x - half_w <= sx <= aim_x + half_w:
            tx = fx - (aim_x - half_w if sx < aim_x - half_w else aim_x + half_w)
        if not aim_y - half_h <= sy <= aim_y + half_h:
            ty = fy - (aim_y - half_h if sy < aim_y - half_h else aim_y + half_h)
        self.target = (tx, ty)
        cx, cy = self.cam
        if not animate or o.ease >= 1:
            cx, cy = tx, ty
        else:
            cx += (tx - cx) * o.ease
            cy += (ty - cy) * o.ease
            if abs(tx - cx) < 0.5 and abs(ty - cy) < 0.5:
                cx, cy = tx, ty
        self.cam = (cx, cy)
        ox, oy = round(cx), round(cy)

        grid = [[(' ', '')] * w for _ in range(view_h)]

        def put(x, y, text, style):
            # One cell per column: a wide character fills two, the second holding '' (drawn as
            # nothing), so a row never runs past the screen's edge and wraps.
            y -= oy
            if not 0 <= y < view_h:
                return
            row, xx = grid[y], x - ox
            for ch in text:
                wide = cells(ch)
                if wide == 0:
                    if 0 < xx <= w and row[xx - 1][0]:
                        row[xx - 1] = (row[xx - 1][0] + ch, row[xx - 1][1])
                    continue
                for edge in range(xx, xx + wide):  # don't leave half of a wide character behind
                    if 0 <= edge < w:
                        if row[edge][0] == '' and edge > 0:
                            row[edge - 1] = (' ', row[edge - 1][1])
                        if edge + 1 < w and row[edge + 1][0] == '' and cells(row[edge][0][:1]) == 2:
                            row[edge + 1] = (' ', row[edge + 1][1])
                if 0 <= xx and xx + wide <= w:
                    row[xx] = (ch, style)
                    if wide == 2:
                        row[xx + 1] = ('', style)
                elif 0 <= xx < w:
                    row[xx] = (' ', style)
                xx += wide

        def dist(n):
            d = 0
            while id(n) not in path and n.parent is not None:
                n, d = n.parent, d + 1
            return d

        def strength(n):
            d = dist(n)
            if n.parent is self.focus.parent and n.parent is not None and d == 1:
                # the focus's own column: a wheel, fading with distance from the choice
                s = STRENGTH[min(abs(n.row - self.focus.row) // max(1, o.fade), len(STRENGTH) - 1)]
            else:
                s = STRENGTH[min(2 + d * 2, len(STRENGTH) - 1)]
            return max(s * marks[id(n)][1], 0.3)

        for n in nodes:
            x = xs[n.depth]
            text = text_of(n)
            mark = marks[id(n)][0]
            if n is self.focus:
                put(x, n.row, ' ' + text + ' ', P.on('sel_fg', 'sel_bg', True))
                if cap_l:
                    put(x - 1, n.row, cap_l, P.fg('sel_bg'))
                cur = x + cells(text) + 2
                if cap_r:
                    put(cur, n.row, cap_r, P.fg('sel_bg'))
                    cur += 1
                s = 1.0
            else:
                if id(n) in path:  # the way you came: its own colours with --grey-rest, else the path's
                    c, bold = self.colour(n) if o.grey_rest else ('path', True)
                    style, s = P.fg(c, 1, True), 1.0
                else:
                    s = strength(n)
                    c, bold = self.colour(n)
                    if o.grey_rest and n.parent is not self.focus.parent and n.parent is not self.focus:
                        c, bold = 'rest', False   # a branch you're not on
                    style = P.fg(c, s, bold)
                put(x, n.row, text, style)
                cur = x + cells(text)
            if mark:
                role, glyph, ascii_ = mark
                put(cur + 1, n.row, ascii_ if plain else glyph, P.fg(role, s))
                cur += 2
            if is_open(n, self.focus, o.peek) and n._shown:
                kids = n._shown
                hot = id(n) in path and any(id(k) in path for k in kids)
                line = P.fg('line', STRENGTH[min(dist(n) * 2, len(STRENGTH) - 1)])
                lit = P.fg('path_line')
                spine = xs[n.depth + 1] - 2
                end = cur + 1
                put(end, n.row, '─' * max(0, spine - end), lit if hot else line)
                top, bot = kids[0].row, kids[-1].row
                rows = {k.row for k in kids}
                chosen = next((k for k in kids if id(k) in path), None) if hot else None
                for y in range(min(top, n.row), max(bot, n.row) + 1):
                    if y == n.row:
                        ch = ('─' if top == bot else '┬' if y == top else '┴' if y == bot else '┼') if y in rows else '┤'
                    elif y in rows:
                        ch = '╭' if y == top else '╰' if y == bot else '├'
                    else:
                        ch = '│'
                    # the lit part of the spine runs from the parent's row to the chosen child's
                    on = chosen is not None and min(n.row, chosen.row) <= y <= max(n.row, chosen.row)
                    put(spine, y, ch, lit if on else line)
                for k in kids:
                    put(spine + 1, k.row, '─', lit if chosen is k else line)

        self.backdrop_under(grid, 1 if o.header else 0)
        lines = [o.header(full)] if o.header else []
        side = self.pane_rows(pane_w, view_h) if pane_w else [''] * view_h
        for row, beside in zip(grid, side):
            s, cur = [], None
            for ch, st in row:
                if st != cur:
                    s.append('\033[0m' + st); cur = st
                s.append(ch)
            lines.append(''.join(s) + '\033[0m' + beside)
        lines.append(self.typing_line(full) if self.asking else self.bar(full))
        if o.footer and not self.asking:
            lines.append(o.footer(full))
        elif self.asking:
            hint = 'esc cancel' if self.asking[3] is not None else '⏎ done  esc cancel  ⌃U clear  ⌃W word'
            lines.append(P.fg('hint') + hint[:full - 1] + '\033[0m')
        else:
            acts = [h for _, _, h in self.source.actions(self.focus)] if not isinstance(self.focus, Stub) else []
            hint = '  '.join(acts + ['↑↓ move  →/tab open  ← parent  type filter  ⌃X fold  ' +
                                     ('pgup/pgdn scroll  ' if pane_w else '') + '⏎ choose  esc cancel'])
            lines.append(P.fg('hint') + clip(hint, full - 1) + '\033[0m')
        return lines, (ox, oy) != (round(tx), round(ty)) or self.source.busy()

    def pane_lines(self, preview, room, height):
        """The pane's lines, styled, for what preview() gave: text, a Picture, or a list of them."""
        out = []
        for part in preview if isinstance(preview, list) else [preview]:
            if isinstance(part, Picture):
                out += part.lines(room, max(4, min(height - 1, part.rows)), self.opts.truecolor)
            elif part is not None:
                out += [self.paint.fg('file') + line for line in wrap(str(part), room)]
        return out

    def typing_line(self, w):
        """The open typing line: its prompt in a chip, then what's typed, the end of it if it's long."""
        prompt, text, _, choices = self.asking
        P = self.paint
        head = P.on('bg', 'filter', True) + ' ' + printable(prompt) + ' ' + '\033[0m '
        room = max(4, w - cells(prompt) - 5)
        text = printable(text)
        while cells(text) > room - 1:
            text = text[1:]
        return head + ('' if choices is not None else P.fg('path', 1, True) + text + '▏') + '\033[0m'

    def pane_rows(self, width, height):
        """The reading pane, `height` rows `width` wide: a line down its left side, the choice's
        name, and its preview wrapped, from self.scroll down."""
        P, f = self.paint, self.focus
        end = self.source.preview_end(f)
        if self._preview[0] is not f:
            self.scroll = sys.maxsize if end else 0
        elif end and self._preview[1] == -1 and self._at_end:
            self.scroll = sys.maxsize     # fetched again (refresh) with you at the end: stay there
        if self._preview[0] is not f or self._preview[1] != width:
            text = self.source.preview(f)
            self._preview = (f, width, None if text is None else self.pane_lines(text, width - 3, height - 2))
        body = self._preview[2]
        room = width - 3
        self.page = max(1, height - 2)
        self._at_end = body is not None and self.scroll >= len(body) - self.page
        rows = [P.fg('path', 1, True) + clip(printable(f.label()), room)]
        if body is not None:
            self.scroll = max(0, min(self.scroll, len(body) - self.page))
            rule = '─' * room
            if len(body) > self.page:   # where in it you are
                at = f' {self.scroll + 1}–{min(self.scroll + self.page, len(body))} of {len(body)} '
                rule = clip('─' * max(0, room - cells(at) - 2) + at + '──', room)
            rows.append(P.fg('line', STRENGTH[5]) + rule)
            rows += body[self.scroll:self.scroll + self.page]
        edge = P.fg('line', STRENGTH[4]) + '│ '
        return ['\033[0m' + edge + (rows[i] if i < len(rows) else '') + '\033[0m' for i in range(height)]

    def backdrop_under(self, grid, top):
        """The screen behind (self.backdrop, {(row, col): char}), dimmed, in the grid's empty cells
        that have an empty cell either side, so what's drawn stays clear of it."""
        if not self.backdrop:
            return
        ghost = self.paint.fg('ghost')
        w = len(grid[0]) if grid else 0

        drawn = {(y, x) for y, row in enumerate(grid) for x, cell in enumerate(row) if cell != (' ', '')}
        for (y, x), ch in self.backdrop.items():
            y -= top
            if not 0 <= y < len(grid):
                continue
            row, wide = grid[y], cells(ch) == 2
            if not any((y, c) in drawn for c in range(x - 1, x + (3 if wide else 2))) and 0 <= x and x + wide < w:
                row[x] = (ch, ghost)
                if wide:
                    row[x + 1] = ('', ghost)

    def bar(self, w):
        """The way to the choice as powerline-shell draws a prompt (the provider's segments), then
        its tail and the filter."""
        P = self.paint
        hard, soft, _, _ = self.glyphs
        segs, tail = self.source.bar(self.focus, self.opts.glyphs == 'plain')   # (text, fg, bg, bold)
        segs, tail = list(segs), list(tail)
        p = self.focus.parent
        if p and p.filter:
            tail.append(('filter: ' + p.filter + '▏', 'bg', 'filter', False))
        if self.message:
            tail.append((self.message, 'bg', 'filter', False))

        segs = [(printable(t), *rest) for t, *rest in segs]
        tail = [(printable(t), *rest) for t, *rest in tail]

        def width(ss):
            return sum(cells(t) + 3 for t, *_ in ss)
        # Too long: the first folders after ~ give way to one '…', the last one always stays.
        while width(segs + tail) > w - 1 and len(segs) > 2:
            if segs[1][0] != '…':
                segs[1] = ('…', 'seg_fg', 'seg_bg', False)
            elif len(segs) > 3:
                del segs[2]
            else:
                break
        segs += tail
        while width(segs) > w - 1 and len(segs) > 1:   # still too long: drop from the end
            segs.pop()
        if width(segs) > w - 1:
            segs[0] = (clip(segs[0][0], max(1, w - 4)), *segs[0][1:])
        out = []
        for i, (text, fg, bg, bold) in enumerate(segs):
            out.append(P.on(fg, bg, bold) + ' ' + text + ' ')
            nxt = segs[i + 1] if i + 1 < len(segs) else None
            if nxt and nxt[2] == bg:
                out.append(P.on('sep_fg', bg) + (soft or ' '))
            elif nxt:
                out.append(P.on(bg, nxt[2]) + hard if hard else '')
            else:
                out.append('\033[0m' + P.fg(bg) + hard if hard else '')
        return ''.join(out) + '\033[0m'


def display(path):
    cwd = os.getcwd()
    if path == cwd:
        return '.'
    if path.startswith(cwd.rstrip('/') + '/'):
        return os.path.relpath(path, cwd)
    home = os.path.expanduser('~')
    if path == home or path.startswith(home + '/'):
        return '~' + path[len(home):]
    return path


def shell_word(path, fish=False):
    """`path` for a shell command line: as display() writes it, quoted, a leading ~/ left to expand.
    Fish's single quotes take \\ and \\' as escapes, so it gets its own quoting."""
    import re, shlex

    def quote(text):
        if fish:
            return text if re.fullmatch(r'[\w@%+=:,./-]+', text) else \
                "'" + text.replace('\\', '\\\\').replace("'", "\\'") + "'"
        return shlex.quote(text)
    shown = display(path)
    if shown == '~':
        return '~'
    if shown.startswith('~/'):
        return '~/' + (quote(shown[2:]) if shown[2:] else '')
    return quote(shown)


INIT = {
    'bash': '''# filecanvas: `@CMD@ [FOLDER]` goes to a folder you choose on the canvas;@KEYDOC@
if ! type -t @CMD@ >/dev/null 2>&1 || [[ $(declare -f @CMD@ 2>/dev/null) == *filecanvas* ]]; then
    @CMD@() { local p; p=$(@RUN@ --dirs-only --on-start --print absolute "${1:-.}") && builtin cd -- "$p"; : filecanvas; }
else
    echo "filecanvas: @CMD@ is already a command here, so it's left alone (--cmd NAME picks another)" >&2
fi
_filecanvas_insert() {
    local p; p=$(@RUN@ --print shell) || return
    READLINE_LINE=${READLINE_LINE:0:READLINE_POINT}$p${READLINE_LINE:READLINE_POINT}
    READLINE_POINT=$((READLINE_POINT + ${#p}))
}
@BIND@''',
    'fish': '''# filecanvas: `@CMD@ [FOLDER]` goes to a folder you choose on the canvas;@KEYDOC@
if not type -q @CMD@; or functions @CMD@ 2>/dev/null | string match -q '*filecanvas*'
    function @CMD@ --description 'cd to a folder chosen on the canvas (filecanvas)'
        set -l start .
        set -q argv[1]; and set start $argv[1]
        set -l p (@RUN@ --dirs-only --on-start --print absolute $start | string collect); and builtin cd -- $p
    end
else
    echo "filecanvas: @CMD@ is already a command here, so it's left alone (--cmd NAME picks another)" >&2
end
function _filecanvas_insert --description 'put a path chosen on the canvas in at the cursor (filecanvas)'
    set -l p (@RUN@ --print fish | string collect); and commandline -i -- $p
    commandline -f repaint
end
@BIND@''',
    'zsh': '''# filecanvas: `@CMD@ [FOLDER]` goes to a folder you choose on the canvas;@KEYDOC@
if (( ! $+commands[@CMD@] && ! $+aliases[@CMD@] && ! $+builtins[@CMD@] )) && [[ -z ${functions[@CMD@]} || ${functions[@CMD@]} == *filecanvas* ]]; then
    @CMD@() { local p; p=$(@RUN@ --dirs-only --on-start --print absolute "${1:-.}") && builtin cd -- "$p"; : filecanvas }
else
    print -u2 "filecanvas: @CMD@ is already a command here, so it's left alone (--cmd NAME picks another)"
fi
_filecanvas_insert() { local p; p=$(@RUN@ --print shell) && LBUFFER+=$p; zle reset-prompt }
zle -N _filecanvas_insert
@BIND@''',
}


def init(shell, cmd='cdi', key='ctrl-t', run=None):
    """The lines for a shell's rc file: `cmd` and, unless key is 'none', a key that inserts a path."""
    import re, shlex
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', cmd):
        sys.exit(f'filecanvas: {cmd!r} is not a command name')
    run = run or shlex.join([sys.executable, os.path.abspath(__file__)])
    bind, doc = '', ''
    m = re.fullmatch(r'(ctrl|alt)-([a-z])', key.lower())
    if m:
        mod, letter = m.groups()
        doc = f' {key} puts a path in at the cursor.'
        if shell == 'bash':
            seq = ('\\C-' if mod == 'ctrl' else '\\e') + letter
            bind = "bind -x '\"" + seq + "\": _filecanvas_insert'"
        elif shell == 'fish':
            seq = ('\\c' if mod == 'ctrl' else '\\e') + letter   # in vi mode too
            bind = f"bind {seq} _filecanvas_insert\nbind -M insert {seq} _filecanvas_insert"
        else:
            seq = '^' + letter.upper() if mod == 'ctrl' else '^[' + letter
            bind = "bindkey '" + seq + "' _filecanvas_insert"
    elif key != 'none':
        sys.exit(f"filecanvas: --key takes ctrl-<letter>, alt-<letter> or none, not {key!r}")
    return (INIT[shell].replace('@CMD@', cmd).replace('@RUN@', run).replace('@BIND@', bind)
            .replace('@KEYDOC@', doc).rstrip() + '\n')


def keys(data):
    seq = {'\x1b[A': 'up', '\x1b[B': 'down', '\x1b[C': 'right', '\x1b[D': 'left',
           '\x1bOA': 'up', '\x1bOB': 'down', '\x1bOC': 'right', '\x1bOD': 'left', '\x1b[Z': 'backtab',
           '\x1b[5~': 'pgup', '\x1b[6~': 'pgdn'}
    i = 0
    while i < len(data):
        for s, name in seq.items():
            if data.startswith(s, i):
                yield name; i += len(s); break
        else:
            yield 'esc' if data[i] == '\x1b' else data[i]
            i += 1


def apply(c, k, opts):
    """One key; returns ('done', what the provider chose: the path) / ('cancel', None) / None."""
    if c.asking:
        return answer(c, k)
    c.message = None
    p = c.focus.parent
    if k in ('esc', '\x03', '\x07'):
        return 'cancel', None
    acts = {} if isinstance(c.focus, Stub) else {key: name for key, name, _ in c.source.actions(c.focus)}
    if k in acts:
        done = c.source.act(c.focus, acts[k], c)
        return None if done is None else ('done', done)
    if k in ('\r', '\n'):
        chosen = c.source.choose(c.focus)
        if chosen is not None:
            return 'done', chosen
        c.enter()       # staying on the canvas: a branch opens
        return None
    if k in ('pgup', 'pgdn'):
        c.scroll = max(0, c.scroll + (c.page if k == 'pgdn' else -c.page))
        return None
    if k == 'up' or (k == 'backtab' and opts.tab == 'cycle'):
        c.move(-1)
    elif k == 'down' or (k == '\t' and opts.tab == 'cycle'):
        c.move(1)
    elif k in ('right', '\t'):
        c.enter()
    elif k == 'left':
        c.leave()
    elif k == '\x18':
        c.fold()
    elif k in ('\x7f', '\x08'):
        if p and p.filter:
            c.refilter(p.filter[:-1])
    elif k == '\x15':
        c.refilter('')
    elif len(k) == 1 and k.isprintable():
        c.refilter((p.filter if p else '') + k)
    return None


def answer(c, k):
    """A key while the typing line is open."""
    prompt, text, done, choices = c.asking
    if k in ('esc', '\x03', '\x07'):
        c.asking = None
        return None
    if choices is not None:
        if len(k) != 1 or k.lower() not in choices:
            return None
        c.asking, text = None, k.lower()
    elif k in ('\r', '\n'):
        c.asking = None
    else:
        if k in ('\x7f', '\x08'):
            text = text[:-1]
        elif k == '\x15':
            text = ''
        elif k == '\x17':                  # ⌃W: the last word
            text = text.rstrip(' ')
            text = text[:text.rfind(' ') + 1]
        elif len(k) == 1 and k.isprintable():
            text += k
        c.asking = (prompt, text, done, choices)
        return None
    r = done(text)
    return None if r is None else ('done', r)


def terminal(cmd, **kw):
    """Run `cmd` on the terminal itself (an editor, a pager), even when stdout is a pipe, as it
    is under cdi; its exit status. For outside()."""
    with open('/dev/tty', 'r+b', buffering=0) as t:
        return subprocess.call(cmd, stdin=t, stdout=t, stderr=t, **kw)


def capture():
    """{(row, col): char} of the tmux pane we're in, as it is now; {} outside tmux."""
    pane = os.environ.get('TMUX_PANE')
    if not (pane and os.environ.get('TMUX')):
        return {}
    try:
        text = subprocess.run(['tmux', 'capture-pane', '-p', '-t', pane], capture_output=True,
                              text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    out = {}
    for y, line in enumerate(text.split('\n')):
        x = 0
        for ch in line:
            if not ch.isspace() and ch.isprintable():
                out[(y, x)] = ch
            x += cells(ch)
    return out


def run(opts):
    """Draw the canvas on the terminal until Enter (the chosen path, absolute) or Esc (None)."""
    if opts.backdrop == 'auto':
        opts.backdrop = capture()
    c = Canvas(opts.start, opts)
    tty = open('/dev/tty', 'r+b', buffering=0)
    fd = tty.fileno()
    import termios, tty as ttymod
    old = termios.tcgetattr(fd)

    def out(s):
        tty.write(s.encode())

    result = None
    try:
        ttymod.setraw(fd)
        out('\033[?1049h\033[?25l')
        animate = not os.environ.get('CDECK_CALM')
        size, dirty = None, True
        while True:
            if os.get_terminal_size(fd) != size:
                size, dirty = os.get_terminal_size(fd), True
            if c.source.changed:
                c.source.changed, dirty = False, True
            if dirty:
                lines, moving = c.frame(*size, animate)
                out('\033[?2026h\033[H' + '\r\n'.join(l + '\033[K' for l in lines) + '\033[?2026l')
                dirty = moving
            ready, _, _ = select.select([fd], [], [], 1 / 60 if moving else 0.25)
            if not ready:
                continue
            dirty = True
            data = os.read(fd, 1024).decode(errors='replace')
            if data == '\x1b':  # a lone Esc, or the start of an arrow split across reads
                more, _, _ = select.select([fd], [], [], 0.03)
                if more:
                    data += os.read(fd, 1024).decode(errors='replace')
            for k in keys(data):
                r = apply(c, k, opts)
                if r:
                    result = r
                    break
            if result:
                break
            if c.waiting:       # an action's turn with the terminal: step aside, then come back
                out('\033[?25h\033[?1049l')
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
                try:
                    c.drain()
                finally:
                    ttymod.setraw(fd)
                    out('\033[?1049h\033[?25l')
                    size = None
    finally:
        out('\033[?25h\033[?1049l')
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return result[1] if result and result[0] == 'done' else None


def dump(opts):
    """Headless: apply --keys, print the last frame (plain text, or with colour with --raw)."""
    import re
    c = Canvas(opts.start, opts)
    for k in keys(opts.keys.encode().decode('unicode_escape')):
        if apply(c, k, opts):
            break
    w, h = map(int, opts.size.split('x'))
    c.frame(w, h, animate=False)
    c.source.wait()
    lines, _ = c.frame(w, h, animate=False)
    for l in lines:
        print(l if opts.raw else re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', l).rstrip())


def theme(name, overrides):
    if isinstance(name, dict):
        t = theme(name.get('base', 'powerline'), {})
        t.update({k: v for k, v in name.items() if k != 'base'})
    elif name in THEMES:
        t = dict(THEMES[name])
    elif os.path.isfile(os.path.expanduser(name)):
        with open(os.path.expanduser(name)) as f:
            data = json.load(f)
        t = theme(data.pop('base', 'powerline'), {})
        t.update(data)
    else:
        sys.exit(f"filecanvas: no theme {name!r} (themes: {', '.join(THEMES)}, or a .json file)")
    t.update(overrides)
    return t


def parser():
    a = argparse.ArgumentParser(description='The file system as one canvas you move across.',
                                epilog=f'Any of these can be set in {CONFIG}, e.g. '
                                       '{"theme": "nord", "dead": 0, "colors": {"sel_bg": 31}}.')
    a.add_argument('start', nargs='?', default='.')
    a.add_argument('--theme', default='powerline', help=f"{', '.join(THEMES)}, or a .json file "
                   '({"base": "nord", "sel_bg": "#ff8800", …}) (powerline)')
    a.add_argument('--glyphs', choices=tuple(GLYPHS), default='powerline',
                   help='separators: powerline (a powerline font), round (a Nerd Font), plain (powerline)')
    a.add_argument('--color', choices=('auto', 'truecolor', '256'), default='auto',
                   help='24-bit colour, or the 256-colour palette (auto)')
    a.add_argument('--ls-colors', action=argparse.BooleanOptionalAction, default=None,
                   help="colour entries the way `ls` does, from LS_COLORS (the theme's default)")
    a.add_argument('--git', action=argparse.BooleanOptionalAction, default=True,
                   help='mark changed entries and show the branch (on)')
    a.add_argument('--limit', type=int, default=0, help='most children shown per folder, the rest as "↓ N more"; 0 = all, trailing off screen (0)')
    a.add_argument('--dead', type=float, default=0.3, help='dead-zone size as a fraction of the view; 0 = always centred (0.3)')
    a.add_argument('--across', type=float, default=0.4, help='where the focus sits across the view, 0..1 (0.4)')
    a.add_argument('--ease', type=float, default=0.3, help='camera easing per frame; 1 = jump (0.3)')
    a.add_argument('--fade', type=int, default=2, help='rows per dimming step in the focused column (2)')
    a.add_argument('--colmax', type=int, default=24, help='widest a column gets (24)')
    a.add_argument('--gap', type=int, default=5, help='columns between a name and its children (5)')
    a.add_argument('--peek', action=argparse.BooleanOptionalAction, default=False,
                   help="show the focused folder's contents before opening it")
    a.add_argument('--fold', action=argparse.BooleanOptionalAction, default=False,
                   help='close a branch when you move off it (classic tree)')
    a.add_argument('--tab', choices=('branch', 'cycle'), default='branch', help='Tab opens (branch) or moves down (cycle)')
    a.add_argument('--print', choices=('display', 'absolute', 'shell', 'fish'), default='display', dest='print_as',
                   help='how to print the choice: from here or ~ (display), absolute, or quoted for '
                        'bash/zsh (shell) or fish')
    a.add_argument('--init', choices=tuple(INIT), help='print the lines for your shell\'s rc file: '
                   'eval "$(python3 filecanvas.py --init bash)" (or zsh); in fish, '
                   'python3 filecanvas.py --init fish | source')
    a.add_argument('--cmd', default='cdi', help='the name --init gives the cd command (cdi)')
    a.add_argument('--key', default='ctrl-t', help='the key --init binds to put a path in at the cursor: '
                   'ctrl-<letter>, alt-<letter> or none (ctrl-t)')
    a.add_argument('--keys', help=argparse.SUPPRESS)
    a.add_argument('--size', default='120x36', help=argparse.SUPPRESS)
    a.add_argument('--raw', action='store_true', help=argparse.SUPPRESS)
    a.add_argument('--dirs-only', action='store_true', help='folders only (for choosing one)')
    a.add_argument('--on-start', action='store_true', help='begin on the start folder itself, not inside it')
    a.add_argument('--grey-rest', action=argparse.BooleanOptionalAction, default=True,
                   help="only the branch you're on keeps its colours; the rest go grey (on)")
    a.add_argument('--backdrop', choices=('auto', 'off'), default='auto',
                   help='the screen behind, dimmed, in the empty space (inside tmux) (auto)')
    a.add_argument('--pane', action=argparse.BooleanOptionalAction, default=None,
                   help="a reading pane beside the tree: the chosen file's start (off; on for --tree)")
    a.add_argument('--pane-width', type=float, default=0.45, help="the pane's share of the width (0.45)")
    a.add_argument('--tree', metavar='FILE', help='show a JSON file as the tree, not the file system: '
                   'objects are branches, everything else leaves shown in the pane')
    return a


def options(argv=(), skip=(), defaults=None, **settings):
    """Settings: the defaults (the caller's `defaults` over ours), then config.json (less the
    keys in `skip`), then `argv`, then `settings` (any flag's name, plus `theme` as a dict,
    `header` / `footer`: functions of the width returning a line for the top / the bottom, and
    `provider`: a Provider, for a tree that isn't the file system)."""
    a = parser()
    a.set_defaults(**(defaults or {}))
    overrides = {}
    if os.path.isfile(CONFIG):
        try:
            with open(CONFIG) as f:
                conf = json.load(f)
        except (OSError, ValueError) as e:
            sys.exit(f'filecanvas: {CONFIG}: {e}')
        overrides = {} if 'colors' in skip else conf.pop('colors', {})
        conf.pop('colors', None)
        conf = {k.replace('-', '_'): v for k, v in conf.items() if k not in skip}
        known = {x.dest for x in a._actions}
        unknown = set(conf) - known
        if unknown:
            print(f"filecanvas: {CONFIG}: ignoring {', '.join(sorted(unknown))}", file=sys.stderr)
        a.set_defaults(**{k: v for k, v in conf.items() if k in known})
    opts = a.parse_args(list(argv))
    opts.footer = opts.header = opts.provider = None
    for k, v in settings.items():
        setattr(opts, k, v)
    if opts.tree and not opts.provider:
        try:
            with open(os.path.expanduser(opts.tree)) as f:
                opts.provider = Tree(json.load(f), os.path.basename(opts.tree))
        except (OSError, ValueError) as e:
            sys.exit(f'filecanvas: {opts.tree}: {e}')
    opts.colours = theme(opts.theme, overrides)
    if opts.ls_colors is None:
        opts.ls_colors = opts.colours.get('ls_colors', True)
    opts.truecolor = truecolor_terminal() if opts.color == 'auto' else opts.color == 'truecolor'
    return opts


def pick(start='.', skip=(), defaults=None, **settings):
    """Let someone choose a path on the canvas; the absolute path (the chosen node's id, with a
    `provider`), or None if they cancelled."""
    return run(options((), skip, defaults, start=start, **settings))


def main(argv=None):
    opts = options(sys.argv[1:] if argv is None else argv)
    if opts.init:
        print(init(opts.init, opts.cmd, opts.key), end='')
        return
    if opts.keys is not None:
        dump(opts)
        return
    chosen = run(opts)
    if chosen is None:
        sys.exit(1)
    print(chosen if opts.provider or opts.print_as == 'absolute' else shell_word(chosen, opts.print_as == 'fish')
          if opts.print_as in ('shell', 'fish') else display(chosen))


if __name__ == '__main__':
    main()
