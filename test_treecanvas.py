"""Providers: a tree that isn't the file system, on the same canvas.

    python3 -m unittest test_treecanvas"""
import os, re, tempfile, threading, unittest

import treecanvas as fc

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


class Motion(unittest.TestCase):
    """With a clock the test turns."""
    def canvas(self, data=None, provider=None):
        c = fc.Canvas('', options(provider=provider or Live(data or {'a': 'one', 'b': 'two'})))
        c.now = 100.0
        c.clock = lambda: c.now
        return c

    def frame(self, c, animate=True, w=100, h=12):
        lines, moving = c.frame(w, h, animate=animate)
        return lines, [ANSI.sub('', l).rstrip() for l in lines], moving

    def test_a_pulse_flashes_the_row_then_settles(self):
        c = self.canvas()
        settled = self.frame(c)[0]
        c.source.pulse('/b')
        flash, _, moving = self.frame(c)
        self.assertTrue(moving)
        self.assertNotEqual(flash, settled)
        self.assertIn(c.paint.fg(fc.blend(c.theme['sel_bg'], c.theme['sel_bg'], 0), 1, True) + 'b', ''.join(flash))
        c.now += fc.PULSE
        again, _, moving = self.frame(c)
        self.assertEqual((again, moving), (settled, False))

    def test_status_chips_spin_tick_and_go(self):
        c = self.canvas()
        t = threading.Thread(target=c.source.status, args=('send', 'sending'))
        t.start(); t.join()
        _, text, moving = self.frame(c)
        self.assertTrue(moving and fc.SPIN[0] + ' sending' in text[-2])
        c.now += 0.1
        self.assertIn(fc.SPIN[1] + ' sending', self.frame(c)[1][-2])
        c.source.status('send', 'sent', 'ok')
        self.assertIn('✓ sent', self.frame(c)[1][-2])
        c.now += fc.SHOWN_OK
        self.assertNotIn('sent', self.frame(c)[1][-2])
        c.status('send', 'not sent', 'failed')
        self.assertIn('✗ not sent', self.frame(c)[1][-2])
        c.now += fc.SHOWN_OK
        self.assertIn('✗ not sent', self.frame(c)[1][-2])          # failures stay longer

    def test_what_is_gone_turns_to_dust(self):
        c = self.canvas()
        c.status('d', 'thrown away', 'gone')
        self.assertIn('thrown away', self.frame(c)[1][-2])
        c.now += fc.DISSOLVE * 0.5
        middle = self.frame(c)[1][-2]
        self.assertNotIn('thrown away', middle)
        self.assertTrue(any(ch in middle for ch in fc.DUST[:3]))
        c.now += fc.DISSOLVE
        self.frame(c)
        self.assertFalse(c.statuses)

    def test_new_lines_at_the_end_resolve_out_of_noise(self):
        class Talk(Live):
            def preview_end(self, node):
                return True
        data = {'chat': '\n'.join(f'line {i}' for i in range(5))}
        c = self.canvas(provider=Talk(data))
        self.frame(c)
        data['chat'] += '\nhello there'
        c.source.invalidate()
        _, text, moving = self.frame(c)
        self.assertTrue(moving)
        self.assertFalse(any(l.endswith('hello there') for l in text))      # noise at first
        self.assertTrue(any(l.endswith('line 4') for l in text))            # the old lines don't move
        self.assertTrue(any(l.endswith('│ ' + fc.scramble('hello there', 1, 5)) for l in text))
        c.now += fc.REVEAL
        self.assertTrue(any(l.endswith('hello there') for l in self.frame(c)[1]))

    def test_calm_frames_hold_still(self):
        c = self.canvas()
        c.source.pulse('/b')
        c.status('s', 'sending')
        lines, text, _ = self.frame(c, animate=False)
        self.assertIn('… sending', text[-2])
        c.now += 0.5
        self.assertEqual(self.frame(c, animate=False)[0], lines)

    def test_notes_stay_a_while_and_the_provider_hears_where_you_look(self):
        seen = []

        class Watch(Live):
            def focus(self, node):
                seen.append(node.path)
        c = self.canvas(provider=Watch({'a': 1, 'b': 2}))
        c.status('n', 'Ann: hello', 'note')
        self.assertIn('Ann: hello', self.frame(c)[1][-2])
        press(c, 'down')
        self.frame(c)
        self.frame(c)
        self.assertEqual(seen, ['/a', '/b'])                       # once per move
        c.now += fc.SHOWN_NOTE
        self.assertNotIn('Ann: hello', self.frame(c)[1][-2])

    def test_changed_hears_each_edit_and_the_close(self):
        heard = []
        c = self.canvas()
        c.ask('say', lambda t: None, changed=heard.append)
        press(c, 'h', 'i', '\x7f', '\r')
        c.ask('say', lambda t: None, changed=heard.append)
        press(c, 'x', 'esc')
        self.assertEqual(heard, ['h', 'hi', 'h', None, 'x', None])


class Conversation(Live):
    """Messages under people: wrapped, the people as chips, opening on (and keeping to) the last."""
    def width(self, node):
        return 20 if not node.is_dir else None

    def chip(self, node):
        return ('person_fg', 'person0') if node.is_dir and node.parent and node.parent.parent else None

    def start_at(self, node):
        return -1

    def pane_for(self, node):
        return node.name == 'photo'


class TallNodes(unittest.TestCase):
    def canvas(self, data):
        c = fc.Canvas('', options(provider=Conversation(data)))
        return c

    def test_a_long_message_wraps_and_its_neighbour_starts_below_it(self):
        data = {'chat': {'ann': {'m1': 1, 'm2': 2}}}
        long = 'a message long enough to wrap onto three lines here'
        c = fc.Canvas('chat/ann', options(provider=Conversation(data)))
        data['chat']['ann'] = {long: 1, 'short one': 2}
        c.source.invalidate()
        lines, _ = c.frame(100, 20, animate=False)
        text = [ANSI.sub('', l).rstrip() for l in lines]
        rows = [i for i, l in enumerate(text[:-2]) if 'a message long' in l or 'enough to wrap' in l or 'three lines here' in l
                or 'short one' in l]
        self.assertEqual(len(rows), 4)                              # three for the long one, one for its neighbour
        self.assertEqual(rows, list(range(rows[0], rows[0] + 4)))   # one under the other, none on top of another
        self.assertTrue(all(fc.cells(l) <= 100 for l in text))

    def test_the_choice_lights_every_line_and_chips_draw_in_their_colours(self):
        data = {'chat': {'ann': {'one two three four five six seven': 1}}}
        c = fc.Canvas('chat/ann', options(provider=Conversation(data)))
        lines, _ = c.frame(100, 16, animate=False)
        lit = c.paint.on('sel_fg', 'sel_bg', True)
        self.assertEqual(sum(1 for l in lines if lit in l), 2)     # both of its lines
        self.assertIn(c.paint.on('person_fg', 'person0', True) + ' ann ', ''.join(lines))   # its sender, a chip

    def test_it_opens_on_the_last_and_keeps_to_it(self):
        data = {'chat': {'ann': {'m1': 1, 'm2': 2, 'm3': 3}}}
        c = fc.Canvas('chat', options(provider=Conversation(data)))
        press(c, 'right')
        self.assertEqual(c.focus.name, 'm3')
        data['chat']['ann']['m4'] = 4
        c.source.invalidate()
        c.frame(100, 16, animate=False)
        self.assertEqual(c.focus.name, 'm4')                        # on the last: it follows
        press(c, 'up')
        data['chat']['ann']['m5'] = 5
        c.source.invalidate()
        c.frame(100, 16, animate=False)
        self.assertEqual(c.focus.name, 'm3')                        # further up: it stays
        press(c, 'left', 'right')
        self.assertEqual(c.focus.name, 'm3')                        # back in: where you were

    def test_going_into_something_still_coming_goes_in_when_it_comes(self):
        class Slow(Live):
            ready = False

            def children(self, node):
                if node.name == 'chat' and not self.ready:
                    raise OSError(0, 'fetching…')
                return super().children(node)
        p = Slow({'chat': {'a': 1, 'b': 2}, 'other': 1})
        c = fc.Canvas('', options(provider=p))
        press(c, 'right')
        self.assertEqual((c.focus.name, c.focus.error), ('chat', 'fetching…'))
        p.ready = True
        p.invalidate('/chat')
        c.frame(100, 16, animate=False)
        self.assertEqual(c.focus.name, 'a')                         # in, once it came
        press(c, 'left')
        p.ready = False
        c.source.invalidate('/chat')
        c.frame(100, 16, animate=False)
        press(c, 'down', 'up')                                      # moved off and back: no surprise jump
        p.ready = True
        p.invalidate('/chat')
        c.frame(100, 16, animate=False)
        self.assertEqual(c.focus.name, 'chat')

    def test_the_pane_shows_only_where_it_should(self):
        c = fc.Canvas('', options(provider=Conversation({'photo': 1, 'text': 2})))
        self.assertIn('│', ''.join(screen(c)))
        press(c, 'down')
        self.assertNotIn('│ ', ''.join(screen(c)))


class Going(unittest.TestCase):
    def test_go_opens_the_way_and_chooses_it(self):
        c = fc.Canvas('', options(provider=fc.Tree({'a': {'b': {'c': 1, 'd': 2}}, 'z': 1})))
        press(c, 'down', 'z')                                       # elsewhere, and a filter in the way
        self.assertTrue(c.go(['/a', '/a/b', '/a/b/d']))
        self.assertEqual(c.focus.path, '/a/b/d')
        self.assertTrue(c.root.kids[0].open and c.root.kids[0].kids[0].open)
        self.assertEqual(c.root.filter, '')
        press(c, 'left', 'right')
        self.assertEqual(c.focus.path, '/a/b/d')                    # going back in lands where it went
        self.assertFalse(c.go(['/a', '/nowhere']))
        self.assertEqual(c.focus.path, '/a/b/d')


class Tasks(Live):
    """A task tree: states before the titles, lines in their colour, Enter marks without opening."""
    done = {'/plan/a'}

    def mark(self, node):
        if node.name == 'note':
            return None
        return ('state_done', '✓', 'x') if node.path in self.done else ('stub', '○', '.')

    def mark_before(self, node):
        return True

    def line(self, node):
        return 'state_done' if node.path in self.done else None

    def label(self, node):
        return node.name + ('\n███ ░░░' if node.is_dir else '')

    def choose(self, node):
        self.done.add(node.path)
        return fc.STAY

    def pane_for(self, node):
        return False


class TaskTrees(unittest.TestCase):
    def canvas(self):
        return fc.Canvas('plan', options(provider=Tasks({'plan': {'a': 1, 'b': {'c': 1}, 'note': 1}}), glyphs='plain'))

    def test_marks_go_before_and_titles_line_up(self):
        c = self.canvas()
        text = [ANSI.sub('', l).rstrip() for l in c.frame(100, 16, animate=False)[0]]
        rows = {name: next(l for l in text if l.endswith(' ' + name)) for name in ('a', 'b', 'note')}
        cols = {name: len(r) - len(name) for name, r in rows.items()}
        self.assertEqual(cols['b'], cols['note'])                  # b and the unmarked note start together
        self.assertEqual(cols['a'], cols['b'] + 1)                 # (the choice's block pads it by one)
        self.assertEqual(rows['a'][cols['a'] - 3], 'x')            # each mark just before its name
        self.assertEqual(rows['b'][cols['b'] - 2], '.')
        self.assertIn('███ ░░░', '\n'.join(text))                  # a branch's second line
        self.assertNotIn('?', text[-2])                            # the bar has its first line only

    def test_the_line_into_a_finished_one_is_its_colour_and_enter_stays(self):
        c = self.canvas()
        press(c, 'down')                                           # on b: the line into a is a's own
        lines, _ = c.frame(100, 16, animate=False)
        green = c.paint.fg('state_done', fc.STRENGTH[0])
        a_row = next(l for l in lines if ANSI.sub('', l).rstrip().endswith('a'))
        self.assertIn(green + '╭─', a_row)                          # the bracket into it, green
        press(c, '\r')                                             # b is a branch: marked, not opened
        self.assertIn('/plan/b', c.source.done)
        self.assertEqual(c.focus.name, 'b')

    def test_state_colours_in_every_theme_read_well(self):
        def lum(c):
            r = [x / 255 for x in fc.rgb(c)]
            r = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in r]
            return 0.2126 * r[0] + 0.7152 * r[1] + 0.0722 * r[2]
        for name, t in fc.THEMES.items():
            for role in ('state_done', 'state_doing'):
                a, b = sorted((lum(t[role]), lum(t['bg'])), reverse=True)
                self.assertGreaterEqual((a + 0.05) / (b + 0.05), 4.5, f'{name} {role}')

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
