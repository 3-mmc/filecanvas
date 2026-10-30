"""Providers: a tree that isn't the file system, on the same canvas.

    python3 -m unittest test_filecanvas"""
import os, re, tempfile, threading, unittest

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


class Live(fc.Tree):
    """A tree that changes underneath, and keeps the canvas open."""
    def choose(self, node):
        return None if node.is_dir else node.path if node.name != 'stay' else None


class StayingOpen(unittest.TestCase):
    def canvas(self, data, start='', **settings):
        return fc.Canvas(start, options(provider=Live(data), **settings))

    def test_enter_stays_and_opens_a_branch(self):
        c = self.canvas({'a': {'stay': 1, 'go': 2}})
        self.assertIsNone(press(c, '\r'))
        self.assertEqual(c.focus.path, '/a/stay')
        self.assertIsNone(press(c, '\r'))
        self.assertEqual(press(c, 'down', '\r'), ('done', '/a/go'))

    def test_the_pane_shows_the_preview_and_scrolls(self):
        c = self.canvas({'note': '\n'.join(f'line {i}' for i in range(40))})
        text = screen(c, 100, 12)
        self.assertTrue(text[0].endswith('│ note'))
        self.assertIn('1–8 of 40', text[1])
        self.assertTrue(text[2].endswith('line 0'))
        press(c, 'pgdn')
        self.assertTrue(screen(c, 100, 12)[2].endswith('line 8'))
        press(c, 'pgdn', 'pgdn', 'pgdn', 'pgdn', 'pgdn')
        self.assertTrue(screen(c, 100, 12)[2].endswith('line 32'))   # the last page, no further
        self.assertNotIn('│', ''.join(screen(c, 50, 12)))            # too narrow: no pane
        self.assertNotIn('│', ''.join(screen(self.canvas({'x': 1}, pane=False))))

    def test_invalidate_keeps_what_is_open_and_chosen(self):
        data = {'Inbox': {'t1': 'one', 't2': 'two'}, 'Sent': {}}
        c = self.canvas(data, 'Inbox')
        press(c, 'down')
        self.assertEqual(c.focus.path, '/Inbox/t2')
        data['Inbox'] = {'t0': 'new', 't1': 'one', 't2': 'two, edited'}
        t = threading.Thread(target=c.source.invalidate, args=('/Inbox',))
        t.start(); t.join()
        self.assertTrue(c.source.changed)
        text = '\n'.join(screen(c))
        self.assertEqual(c.focus.path, '/Inbox/t2')
        self.assertIn('t0', text)
        self.assertIn('two, edited', text)
        self.assertEqual(c.focus.parent.sel, 2)
        press(c, 'up')
        self.assertEqual(c.focus.path, '/Inbox/t1')

    def test_a_reload_keeps_the_pane_where_it_was_scrolled(self):
        data = {'note': '\n'.join(f'line {i}' for i in range(40))}
        c = self.canvas(data)
        screen(c, 100, 12)
        press(c, 'pgdn')
        data['note'] += '\nline 40'
        c.source.invalidate()
        text = screen(c, 100, 12)
        self.assertTrue(text[2].endswith('line 8'))
        self.assertIn('of 41', text[1])

    def test_a_conversation_opens_at_its_end_and_follows_it(self):
        class Talk(Live):
            def preview_end(self, node):
                return True
        data = {'chat': '\n'.join(f'line {i}' for i in range(40))}
        c = fc.Canvas('', options(provider=Talk(data)))
        text = screen(c, 100, 12)
        self.assertTrue(text[-3].endswith('line 39'))
        data['chat'] += '\nline 40'
        c.source.invalidate()
        self.assertTrue(screen(c, 100, 12)[-3].endswith('line 40'))     # at the end: follows
        press(c, 'pgup')
        screen(c, 100, 12)
        data['chat'] += '\nline 41'
        c.source.invalidate()
        self.assertFalse(screen(c, 100, 12)[-3].endswith('line 41'))    # scrolled up: stays put

    def test_a_removed_focus_lands_beside_it(self):
        data = {'Inbox': {'t1': 1, 't2': 2, 't3': 3}}
        c = self.canvas(data, 'Inbox')
        press(c, 'down')
        del data['Inbox']['t2']
        c.source.invalidate()           # everything
        screen(c)
        self.assertEqual(c.focus.path, '/Inbox/t3')
        data.clear()
        c.source.invalidate()
        screen(c)
        self.assertIs(c.focus, c.root)



class Acting(fc.Tree):
    """A provider with actions: ⌃R asks for a reply, ⌃D asks y/n, ⌃O steps outside, ⌃E ends."""
    def __init__(self, data):
        super().__init__(data)
        self.sent, self.ran = [], []

    def actions(self, node):
        return [('\x12', 'reply', '⌃R reply'), ('\x04', 'delete', '⌃D delete'),
                ('\x0f', 'open', '⌃O open'), ('\x05', 'end', '⌃E end')] if not node.is_dir else []

    def act(self, node, name, canvas):
        if name == 'reply':
            canvas.ask('reply to ' + node.name, lambda text: self.sent.append((node.name, text)) or
                       canvas.say('sent'))
        elif name == 'delete':
            canvas.ask('delete? [y/n]', lambda k: canvas.say('deleted' if k == 'y' else 'kept'), choices='yn')
        elif name == 'open':
            canvas.outside(lambda: self.ran.append(node.name))
            canvas.outside(lambda: 1 / 0)
        elif name == 'end':
            return 'ended on ' + node.name


class Actions(unittest.TestCase):
    def canvas(self):
        return fc.Canvas('', options(provider=Acting({'a': 'one', 'b': 'two'})))

    def test_hints_name_them_and_their_keys_do_them(self):
        c = self.canvas()
        self.assertIn('⌃R reply', screen(c)[-1])
        press(c, '\x12')
        self.assertTrue(screen(c)[-2].startswith(' reply to a '))
        press(c, *'hello worx', '\x7f', 'l', 'd')
        self.assertIn('hello world▏', screen(c)[-2])
        self.assertIn('⏎ done', screen(c)[-1])
        press(c, 'down')                                   # arrows don't move while typing
        self.assertEqual(c.focus.name, 'a')
        press(c, '\x17', *'there', '\r')                   # ⌃W: a word back
        self.assertEqual(c.source.sent, [('a', 'hello there')])
        self.assertIn('sent', screen(c)[-2])
        press(c, 'down')
        self.assertNotIn('sent', screen(c)[-2])            # until the next key

    def test_esc_closes_the_line_and_choices_take_one_key(self):
        c = self.canvas()
        press(c, '\x12', 'x', 'esc')
        self.assertEqual((c.asking, c.source.sent), (None, []))
        press(c, '\x04', 'q')                              # not a choice: still asking
        self.assertIsNotNone(c.asking)
        press(c, 'Y')
        self.assertIn('deleted', screen(c)[-2])

    def test_outside_runs_after_and_a_failure_is_said(self):
        c = self.canvas()
        press(c, '\x0f')
        self.assertEqual(c.source.ran, [])                 # waits for the terminal
        c.drain()
        self.assertEqual(c.source.ran, ['a'])
        self.assertIn('failed: division by zero', screen(c)[-2])

    def test_an_action_can_end_the_canvas(self):
        self.assertEqual(press(self.canvas(), 'down', '\x05'), ('done', 'ended on b'))
        c = fc.Canvas('', options(provider=Acting({'dir': {'x': 1}})))
        self.assertNotIn('⌃R', screen(c)[-1])              # nothing to do on a branch


class Pictures(unittest.TestCase):
    def test_only_colours_get_through(self):
        pic = fc.Picture('/x.png')
        out = ('\x1b[?25l\x1b[0m\x1b[38;2;4;20;159m▄\x1b[2J\x1b]0;title\x07\x1b[48;5;12m▀\x1bc\x07x\n'
               '\x1b[?25h\n')
        self.assertEqual(pic.clean(out), ['\x1b[0m\x1b[38;2;4;20;159m▄\x1b[48;5;12m▀x\x1b[0m'])

    def test_in_the_pane_beside_text_and_without_chafa_a_note(self):
        class Shown(fc.Tree):
            def preview(self, node):
                return ['a photo:', fc.Picture('/nowhere/cat.jpg', rows=6), 'after']
        c = fc.Canvas('', options(provider=Shown({'x': 1})))
        real = fc.shutil.which
        fc.shutil.which = lambda name: None
        try:
            text = screen(c, 100, 12)
        finally:
            fc.shutil.which = real
        self.assertTrue(text[2].endswith('a photo:') and text[3].endswith('🖼 cat.jpg') and text[4].endswith('after'))


class ChipLines(unittest.TestCase):
    def test_a_line_of_chips_in_the_pane_as_the_bar_draws_them(self):
        class Talk(fc.Tree):
            def preview(self, node):
                return [fc.Chips([('Ann', 'home_fg', 'home_bg', True)], ' · 5 Oct'), 'hello',
                        fc.Chips([('A very long name indeed, longer than the pane', 'seg_fg', 'seg_bg', False)])]
        c = fc.Canvas('', options(provider=Talk({'x': 1}), glyphs='plain'))
        lines, _ = c.frame(100, 12, animate=False)
        plain = [ANSI.sub('', l).rstrip() for l in lines]
        self.assertTrue(plain[2].endswith('│  Ann  · 5 Oct'))
        self.assertIn(c.paint.on('home_fg', 'home_bg', True) + ' Ann ', lines[2])
        self.assertTrue(plain[3].endswith('hello'))
        self.assertLessEqual(fc.cells(plain[4]), 100)


class People(unittest.TestCase):
    def test_every_theme_has_six_readable_colours_for_people(self):
        def lum(c):
            r = [x / 255 for x in fc.rgb(c)]
            r = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in r]
            return 0.2126 * r[0] + 0.7152 * r[1] + 0.0722 * r[2]
        for name, t in fc.THEMES.items():
            for i in range(fc.PEOPLE):
                a, b = sorted((lum(t['person_fg']), lum(t[f'person{i}'])), reverse=True)
                self.assertGreaterEqual((a + 0.05) / (b + 0.05), 4.5, f'{name} person{i}')

class Wrap(unittest.TestCase):
    def test_breaks_at_spaces_and_cuts_long_words(self):
        self.assertEqual(fc.wrap('the quick brown fox', 10), ['the quick', 'brown fox'])
        self.assertEqual(fc.wrap('abcdefghijkl', 5), ['abcde', 'fghij', 'kl'])
        self.assertEqual(fc.wrap('a\r\n\nb\tc', 10), ['a', '', 'b   c'])
        self.assertTrue(all(fc.cells(l) <= 5 for l in fc.wrap('漢字漢字漢字 ok', 5)))

    def test_a_hanging_indent_and_never_past_the_width(self):
        self.assertEqual(fc.wrap('  one two three four', 10), ['  one two', '  three', '  four'])
        for text in ('  a bcdefghijklmnop qrstuvwxyz', '    x ' + 'y' * 30, '  ' + '漢' * 20):
            got = fc.wrap(text, 9)
            self.assertTrue(all(fc.cells(l) <= 9 for l in got), got)
            self.assertEqual(''.join(got).replace(' ', ''), text.replace(' ', ''))


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
