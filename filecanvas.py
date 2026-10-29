#!/usr/bin/env python3
"""filecanvas: the file system as one canvas you move across.

Every folder you open keeps its place on a tidy-tree layout (depth to the right, children
spread down, each block of children centred on its parent and shifted only as much as it
needs to keep clear of its neighbours). A camera eases after the selection and only moves
when the selection leaves a box around the middle of the screen. The path you're on is drawn
in the theme's path colour; everything else dims with its distance from it.

    python3 filecanvas.py [START]          # prints the chosen path on stdout, nothing on Esc
    cd "$(python3 filecanvas.py)"

Try it from a plain bash (Ctrl-T puts the chosen path in at the cursor; normal Tab untouched):

    _filecanvas() { local p; p=$(python3 /path/to/filecanvas.py) || return
                    READLINE_LINE=${READLINE_LINE:0:READLINE_POINT}$p${READLINE_LINE:READLINE_POINT}
                    READLINE_POINT=$((READLINE_POINT + ${#p})); }
    bind -x '"\\C-t": _filecanvas'

or zsh:

    _filecanvas() { local p; p=$(python3 /path/to/filecanvas.py </dev/tty) && LBUFFER+=$p; zle reset-prompt }
    zle -N _filecanvas; bindkey '^T' _filecanvas

Keys: ↑↓ siblings · → or Tab open · ← parent (the branch stays open) · type to filter this
column · Backspace unfilters · ⌃X fold everything off the current path · Enter choose · Esc.
Settings are flags (--help), and any of them can live in ~/.config/filecanvas/config.json.
"""
import argparse, json, os, select, stat, subprocess, sys, threading
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
        'ls_colors': True,
    },
    'amber': {  # cdeck's
        'bg': '#121212', 'sel_bg': 214, 'sel_fg': 232, 'path': 214, 'path_line': 214,
        'line': 244, 'dir': 222, 'file': 250, 'stub': 240, 'filter': 214, 'hint': 240,
        'home_bg': 214, 'home_fg': 232, 'seg_bg': 94, 'seg_fg': 222, 'cwd_fg': 230, 'sep_fg': 172,
        'repo_clean_bg': 136, 'repo_clean_fg': 232, 'repo_dirty_bg': 166, 'repo_dirty_fg': 232,
        'git_dirty': 166, 'git_staged': 142, 'git_untracked': 137, 'git_conflict': 196,
        'ls_colors': False,
    },
    'nord': {
        'bg': '#2e3440', 'sel_bg': '#88c0d0', 'sel_fg': '#2e3440', 'path': '#88c0d0',
        'path_line': '#81a1c1', 'line': '#616e88', 'dir': '#81a1c1', 'file': '#d8dee9',
        'stub': '#4c566a', 'filter': '#ebcb8b', 'hint': '#4c566a',
        'home_bg': '#5e81ac', 'home_fg': '#eceff4', 'seg_bg': '#3b4252', 'seg_fg': '#d8dee9',
        'cwd_fg': '#eceff4', 'sep_fg': '#4c566a',
        'repo_clean_bg': '#a3be8c', 'repo_clean_fg': '#2e3440', 'repo_dirty_bg': '#bf616a',
        'repo_dirty_fg': '#eceff4', 'git_dirty': '#d08770', 'git_staged': '#a3be8c',
        'git_untracked': '#b48ead', 'git_conflict': '#bf616a', 'ls_colors': True,
    },
    'gruvbox': {
        'bg': '#282828', 'sel_bg': '#fabd2f', 'sel_fg': '#282828', 'path': '#fabd2f',
        'path_line': '#d79921', 'line': '#7c6f64', 'dir': '#83a598', 'file': '#ebdbb2',
        'stub': '#665c54', 'filter': '#b8bb26', 'hint': '#665c54',
        'home_bg': '#458588', 'home_fg': '#ebdbb2', 'seg_bg': '#504945', 'seg_fg': '#d5c4a1',
        'cwd_fg': '#fbf1c7', 'sep_fg': '#7c6f64',
        'repo_clean_bg': '#98971a', 'repo_clean_fg': '#282828', 'repo_dirty_bg': '#cc241d',
        'repo_dirty_fg': '#ebdbb2', 'git_dirty': '#fe8019', 'git_staged': '#b8bb26',
        'git_untracked': '#d3869b', 'git_conflict': '#fb4934', 'ls_colors': True,
    },
    'solarized-dark': {
        'bg': '#002b36', 'sel_bg': '#268bd2', 'sel_fg': '#fdf6e3', 'path': '#93a1a1',
        'path_line': '#268bd2', 'line': '#586e75', 'dir': '#268bd2', 'file': '#839496',
        'stub': '#586e75', 'filter': '#b58900', 'hint': '#586e75',
        'home_bg': '#268bd2', 'home_fg': '#fdf6e3', 'seg_bg': '#073642', 'seg_fg': '#93a1a1',
        'cwd_fg': '#eee8d5', 'sep_fg': '#586e75',
        'repo_clean_bg': '#859900', 'repo_clean_fg': '#002b36', 'repo_dirty_bg': '#dc322f',
        'repo_dirty_fg': '#fdf6e3', 'git_dirty': '#cb4b16', 'git_staged': '#859900',
        'git_untracked': '#6c71c4', 'git_conflict': '#dc322f', 'ls_colors': True,
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
    def __init__(self, path, name, is_dir, parent=None, kind='fi'):
        self.path, self.name, self.is_dir, self.parent, self.kind = path, name, is_dir, parent, kind
        self.kids = None        # loaded lazily
        self.open = False
        self.sel = 0            # index into shown(): the remembered / selected child
        self.filter = ""
        self.error = None
        self.row = self.depth = 0
        self.dirs_only = parent.dirs_only if parent else False

    def load(self):
        if self.kids is None:
            self.kids = []
            try:
                with os.scandir(self.path) as it:
                    for e in it:
                        is_dir, key = kind(e)
                        self.kids.append(Node(e.path, e.name, is_dir, self, key))
            except OSError as e:
                self.error = e.strerror or "unreadable"
            self.kids.sort(key=lambda n: (not n.is_dir, n.name.startswith('.'), n.name.casefold()))
        return self.kids

    def shown(self):
        f = self.filter.casefold()
        kids = [k for k in self.load() if k.is_dir] if self.dirs_only else self.load()
        return [k for k in kids if f in k.name.casefold()] if f else kids

    def label(self):
        return self.name + ('/' if self.is_dir and self.name != '/' else '')


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
        self.ls = ls_colors() if opts.ls_colors else {}
        self.git = Git(opts.git)
        self.root = Node('/', '/', True, kind='di')
        self.root.dirs_only = opts.dirs_only
        # A start that doesn't exist yet is a half-typed path: its folder, filtered by the rest.
        start = os.path.abspath(os.path.expanduser(start or '.'))
        typed = ''
        if not os.path.exists(start):
            start, typed = os.path.dirname(start), os.path.basename(start)
        node = self.root
        for part in [p for p in start.split('/') if p]:
            node.open = True
            for i, k in enumerate(node.load()):
                if k.name == part:
                    node.sel, node = node.shown().index(k) if k in node.shown() else 0, k
                    break
        if node.is_dir:
            node.open = True
            node.filter = typed
            # on the start folder itself (choosing a folder), or on what's in it
            self.focus = node if opts.on_start and not typed or not node.shown() else node.shown()[0]
        else:
            self.focus = node
        self.cam = None       # (x, y) world position of the top-left corner, floats
        self.target = None

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
        if self.ls:
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
        return ('dir' if n.is_dir else 'file'), False

    # -- drawing
    def frame(self, w, h, animate=True):
        o, P, T = self.opts, self.paint, self.theme
        hard, soft, cap_l, cap_r = self.glyphs
        plain = o.glyphs == 'plain'
        layout(self.root, o, self.focus)
        nodes = []
        place(self.root, 0, 0, self.focus, o, nodes)

        def text_of(n):
            t = n.label()
            return t if len(t) <= o.colmax else t[:o.colmax - 1] + '…'

        marks = {}
        for n in nodes:
            rank, ignored, inside = self.git.state(n)
            marks[id(n)] = (rank, ignored, inside)
        widths = {}
        for n in nodes:
            wd = len(text_of(n)) + (2 if marks[id(n)][0] else 0)
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
            y -= oy
            if 0 <= y < view_h:
                for i, ch in enumerate(text):
                    xx = x - ox + i
                    if 0 <= xx < w:
                        grid[y][xx] = (ch, style)

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
            if n.name.startswith('.'):
                s *= 0.75
            if marks[id(n)][1]:        # ignored by git
                s *= 0.65
            return max(s, 0.3)

        for n in nodes:
            x = xs[n.depth]
            text = text_of(n)
            rank, ignored, inside = marks[id(n)]
            if n is self.focus:
                put(x, n.row, ' ' + text + ' ', P.on('sel_fg', 'sel_bg', True))
                if cap_l:
                    put(x - 1, n.row, cap_l, P.fg('sel_bg'))
                cur = x + len(text) + 2
                if cap_r:
                    put(cur, n.row, cap_r, P.fg('sel_bg'))
                    cur += 1
                s = 1.0
            else:
                if id(n) in path:
                    style, s = P.fg('path', 1, True), 1.0
                else:
                    s = strength(n)
                    c, bold = self.colour(n)
                    style = P.fg(c, s, bold)
                put(x, n.row, text, style)
                cur = x + len(text)
            if rank:
                role, glyph, ascii_ = MARKS[rank]
                glyph = '*' if inside and plain else '•' if inside else ascii_ if plain else glyph
                put(cur + 1, n.row, glyph, P.fg(role, s))
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

        lines = [o.header(w)] if o.header else []
        for row in grid:
            s, cur = [], None
            for ch, st in row:
                if st != cur:
                    s.append('\033[0m' + st); cur = st
                s.append(ch)
            lines.append(''.join(s) + '\033[0m')
        lines.append(self.bar(w))
        if o.footer:
            lines.append(o.footer(w))
        else:
            hint = '↑↓ move  →/tab open  ← parent  type filter  ⌃X fold  ⏎ choose  esc cancel'
            lines.append(P.fg('hint') + hint[:w - 1] + '\033[0m')
        return lines, (ox, oy) != (round(tx), round(ty)) or bool(self.git.pending)

    def bar(self, w):
        """The chosen path as powerline-shell draws a prompt: ~ in its own chip, then the rest."""
        P = self.paint
        hard, soft, _, _ = self.glyphs
        path = self.focus.path
        home = os.path.expanduser('~')
        segs = []   # (text, fg, bg, bold)
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
            tail.append(((' ' if self.opts.glyphs != 'plain' else '') + name,
                         'repo_dirty_fg' if dirty else 'repo_clean_fg',
                         'repo_dirty_bg' if dirty else 'repo_clean_bg', False))
        p = self.focus.parent
        if p and p.filter:
            tail.append(('filter: ' + p.filter + '▏', 'bg', 'filter', False))

        def width(ss):
            return sum(len(t) + 3 for t, *_ in ss)
        # Too long: the first folders after ~ give way to one '…', the last one always stays.
        while width(segs + tail) > w - 1 and len(segs) > 2:
            if segs[1][0] != '…':
                segs[1] = ('…', 'seg_fg', 'seg_bg', False)
            elif len(segs) > 3:
                del segs[2]
            else:
                break
        segs += tail
        out = []
        for i, (text, fg, bg, bold) in enumerate(segs):
            text = ''.join(c if c.isprintable() else '?' for c in text)
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


def keys(data):
    seq = {'\x1b[A': 'up', '\x1b[B': 'down', '\x1b[C': 'right', '\x1b[D': 'left',
           '\x1bOA': 'up', '\x1bOB': 'down', '\x1bOC': 'right', '\x1bOD': 'left', '\x1b[Z': 'backtab'}
    i = 0
    while i < len(data):
        for s, name in seq.items():
            if data.startswith(s, i):
                yield name; i += len(s); break
        else:
            yield 'esc' if data[i] == '\x1b' else data[i]
            i += 1


def apply(c, k, opts):
    """One key; returns ('done', path) / ('cancel', None) / None."""
    p = c.focus.parent
    if k in ('esc', '\x03', '\x07'):
        return 'cancel', None
    if k in ('\r', '\n'):
        return 'done', c.focus.path
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


def run(opts):
    """Draw the canvas on the terminal until Enter (the chosen path, absolute) or Esc (None)."""
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
            if c.git.changed:
                c.git.changed, dirty = False, True
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
    c.git.wait()
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
    a.add_argument('--keys', help=argparse.SUPPRESS)
    a.add_argument('--size', default='120x36', help=argparse.SUPPRESS)
    a.add_argument('--raw', action='store_true', help=argparse.SUPPRESS)
    a.add_argument('--dirs-only', action='store_true', help='folders only (for choosing one)')
    a.add_argument('--on-start', action='store_true', help='begin on the start folder itself, not inside it')
    return a


def options(argv=(), skip=(), defaults=None, **settings):
    """Settings: the defaults (the caller's `defaults` over ours), then config.json (less the
    keys in `skip`), then `argv`, then `settings` (any flag's name, plus `theme` as a dict, and
    `header` / `footer`: functions of the width returning a line for the top / the bottom)."""
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
    opts.footer = opts.header = None
    for k, v in settings.items():
        setattr(opts, k, v)
    opts.colours = theme(opts.theme, overrides)
    if opts.ls_colors is None:
        opts.ls_colors = opts.colours.get('ls_colors', True)
    opts.truecolor = truecolor_terminal() if opts.color == 'auto' else opts.color == 'truecolor'
    return opts


def pick(start='.', skip=(), defaults=None, **settings):
    """Let someone choose a path on the canvas; the absolute path, or None if they cancelled."""
    return run(options((), skip, defaults, start=start, **settings))


def main(argv=None):
    opts = options(sys.argv[1:] if argv is None else argv)
    if opts.keys is not None:
        dump(opts)
        return
    chosen = run(opts)
    if chosen is None:
        sys.exit(1)
    print(display(chosen))


if __name__ == '__main__':
    main()
