"""Providers: a tree that isn't the file system, on the same canvas.

    python3 -m unittest test_filecanvas"""
import os, re, tempfile, unittest

import filecanvas as fc

ANSI = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]')
MAIL = {'Inbox': {'Kiln quote': {'from Ann': 'about the kiln', 'from Bo': 'reply'}, 'Rail pass': 'x'},
        'Sent': {}, 'Drafts': {'Hello': 'hi'}}


def options(**settings):
    return fc.options(backdrop='off', ease=1, **settings)


def screen(c, w=100, h=20):
    lines, _ = c.frame(w, h, animate=False)
    return [ANSI.sub('', l).rstrip() for l in lines]


def press(c, *keys):
    for k in keys:
        r = fc.apply(c, k, c.opts)
        if r:
            return r


class Unread(fc.Tree):
    """Every hook a provider has, so the canvas is seen to ask for each."""
    def mark(self, node):
        return ('git_dirty', '●', '*') if node.name.startswith('from') else None

    def colour(self, node):
        return ('filter', True) if node.name == 'Drafts' else None

    def dim(self, node):
        return 0.5 if node.name == 'Sent' else 1.0

    def bar(self, node, plain):
        segs, tail = super().bar(node, plain)
        return segs, tail + [('2 unread', 'repo_clean_fg', 'repo_clean_bg', False)]

    def children(self, node):
        if node.name == 'Sent':
            raise OSError(0, 'offline')
        return super().children(node)


class TreeProvider(unittest.TestCase):
    def canvas(self, start='', provider=None, **settings):
        return fc.Canvas(start, options(provider=provider or fc.Tree(MAIL), **settings))

    def test_begins_on_the_first_child_of_the_root(self):
        c = self.canvas()
        self.assertEqual(c.focus.path, '/Inbox')
        self.assertIn('Inbox', '\n'.join(screen(c)))

    def test_start_is_walked_by_id(self):
        c = self.canvas('Inbox/Kiln quote')
        self.assertEqual(c.focus.path, '/Inbox/Kiln quote/from Ann')
        self.assertTrue(c.root.open and c.root.kids[0].open)

    def test_moving_and_choosing_return_ids(self):
        c = self.canvas()
        self.assertIsNone(press(c, 'right', 'right', 'down'))
        self.assertEqual(press(c, '\r'), ('done', '/Inbox/Kiln quote/from Bo'))
        self.assertEqual(c.focus.data, 'reply')
        self.assertEqual(press(c, 'esc'), ('cancel', None))

    def test_filter_and_branches_only(self):
        c = self.canvas()
        press(c, 'd', 'r')
        self.assertEqual(c.focus.path, '/Drafts')
        c = self.canvas('Inbox', dirs_only=True)
        self.assertEqual([k.name for k in c.focus.parent.shown()], ['Kiln quote'])

    def test_bar_is_the_way_down(self):
        c = self.canvas('Inbox/Kiln quote', glyphs='plain')
        self.assertEqual(screen(c)[-2].split(), ['⌂', 'Inbox', '/', 'Kiln', 'quote', '/', 'from', 'Ann'])

    def test_every_hook_is_asked(self):
        p = Unread(MAIL)
        c = self.canvas('Inbox/Kiln quote', provider=p, glyphs='plain')
        text = screen(c)
        self.assertTrue(any(re.search(r'from Bo \*', l) for l in text))
        self.assertIn('2 unread', text[-2])
        sent = next(k for k in c.root.kids if k.name == 'Sent')
        self.assertEqual(c.colour(next(k for k in c.root.kids if k.name == 'Drafts')), ('filter', True))
        c.focus = sent
        c.enter()
        self.assertEqual(sent.error, 'offline')
        self.assertIn('(offline)', '\n'.join(screen(c)))

    def test_changed_redraws_and_busy_keeps_drawing(self):
        class Slow(fc.Tree):
            pending = True

            def busy(self):
                return self.pending
        p = Slow(MAIL)
        c = self.canvas(provider=p)
        self.assertTrue(c.frame(80, 20, animate=False)[1])
        p.pending = False
        self.assertFalse(c.frame(80, 20, animate=False)[1])


class FileSystemProvider(unittest.TestCase):
    def test_is_the_default_and_keeps_its_look(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ('b', 'a', '.hidden'):
                os.mkdir(os.path.join(d, name))
            open(os.path.join(d, 'z.txt'), 'w').close()
            c = fc.Canvas(d, options(git=False))
            self.assertIsInstance(c.source, fc.FileSystem)
            self.assertEqual([k.label() for k in c.focus.parent.shown()], ['a/', 'b/', '.hidden/', 'z.txt'])
            self.assertEqual(c.source.dim(c.focus.parent.shown()[2]), 0.75)
            c = fc.Canvas(os.path.join(d, 'z'), options(git=False))   # half-typed: filtered
            self.assertEqual(c.focus.path, os.path.join(d, 'z.txt'))


if __name__ == '__main__':
    unittest.main()
