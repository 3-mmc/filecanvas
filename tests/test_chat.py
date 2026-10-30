"""The chat provider: nchat's events kept and shown, and the real `nchat --serve` on a Dummy
profile (skipped where nchat isn't installed: NCHAT names another one).

    python3 -m unittest discover tests"""
import atexit, os, re, shutil, tempfile, time, unittest

import tests                                    # the private test home, before msgcanvas

from msgcanvas import chat, treecanvas as fc, together

NCHAT = os.environ.get('NCHAT') or shutil.which('nchat')
TMP = tempfile.mkdtemp(prefix='msgcanvas-chat-')
atexit.register(shutil.rmtree, TMP, True)
ANSI = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]')


def screen(c, w=120, h=24):
    return [ANSI.sub('', l).rstrip() for l in c.frame(w, h, animate=False)[0]]


def press(c, *keys):
    for k in keys:
        fc.apply(c, k, c.opts)


def options(provider):
    return fc.options(provider=provider, backdrop='off', ease=1, glyphs='plain', colmax=40)


def dummy_conf(name):
    """A confdir with one Dummy profile, as nchat's own serve tests make one."""
    conf = os.path.join(TMP, name)
    os.makedirs(os.path.join(conf, 'profiles', 'Dummy_1'))
    for v in ('version', 'profiles/version'):
        with open(os.path.join(conf, v), 'w') as f:
            f.write('31')
    return conf


def until(test, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        if test():
            return True
        time.sleep(0.05)
    return False


class Events(unittest.TestCase):
    """Serve.apply, with no nchat: what each event does to what's kept."""
    def serve(self):
        s = chat.Serve.detached()
        s.apply({'event': 'hello', 'profiles': [{'profile': 'WhatsAppMd_+491', 'protocol': 'WhatsAppMd',
                                                 'self': 'me', 'features': []}],
                 'skipped': [{'profile': 'Signal_+492', 'why': 'locked'}]})
        return s, s.profiles['WhatsAppMd_+491']

    def test_contacts_name_chats_and_new_messages_move_them_up(self):
        s, p = self.serve()
        s.apply({'event': 'chats', 'profile': p.id, 'chats': [
            {'id': 'ann', 'name': 'ann', 'last_message_time': 100},
            {'id': 'grp', 'name': 'Kiln club', 'last_message_time': 200}]})
        self.assertEqual(p.chat_name('ann'), 'ann')
        changed = s.apply({'event': 'contacts', 'profile': p.id, 'contacts': [{'id': 'ann', 'name': 'Ann Lee'}]})
        self.assertEqual(changed, ['/@WhatsAppMd_+491'])
        self.assertEqual((p.chat_name('ann'), p.chat_name('grp')), ('Ann Lee', 'Kiln club'))
        s.apply({'event': 'messages', 'profile': p.id, 'chat': 'ann', 'messages': [
            {'id': 'm1', 'sender': 'ann', 'text': 'hi', 'time': 300}]})
        self.assertEqual(p.chats['ann']['last_message_time'], 300)
        self.assertEqual(p.who('me'), 'You')

    def test_typing_deleting_and_reading(self):
        s, p = self.serve()
        s.apply({'event': 'messages', 'profile': p.id, 'chat': 'ann', 'messages': [
            {'id': 'm1', 'text': 'a', 'time': 1, 'read': False}, {'id': 'm2', 'text': 'b', 'time': 2}]})
        s.apply({'event': 'message_status', 'profile': p.id, 'chat': 'ann', 'message': 'm1', 'read': True})
        self.assertTrue(p.messages['ann']['m1']['read'])
        s.apply({'event': 'deleted', 'profile': p.id, 'chat': 'ann', 'message': 'm2', 'ok': True})
        self.assertEqual(list(p.messages['ann']), ['m1'])
        s.apply({'event': 'typing', 'profile': p.id, 'chat': 'ann', 'user': 'ann', 'typing': True})
        self.assertEqual(p.typing['ann'], {'ann'})
        s.apply({'event': 'typing', 'profile': p.id, 'chat': 'ann', 'user': 'ann', 'typing': False})
        self.assertEqual(p.typing['ann'], set())
        self.assertEqual(s.apply({'event': 'chats', 'profile': 'nobody', 'chats': []}), [])

    def test_people_without_names_read_as_numbers(self):
        s, p = self.serve()
        s.apply({'event': 'chats', 'profile': p.id, 'chats': [
            {'id': '491701234567@s.whatsapp.net', 'name': '491701234567@s.whatsapp.net'},
            {'id': '1203630-16@g.us', 'name': '1203630-16@g.us'}]})
        self.assertEqual(p.chat_name('491701234567@s.whatsapp.net'), '+491701234567')
        self.assertEqual(p.chat_name('1203630-16@g.us'), 'group')
        self.assertEqual(p.who('Stanley_0'), 'Stanley_0')

    def test_everyone_has_their_own_colour_and_no_two_share_one_in_a_chat(self):
        s, p = self.serve()
        order = ['ann', 'me', 'bo', 'ann', 'cy', 'bo', 'di', 'ed']
        got = {who: p.colour('grp', who) for who in order}
        others = [got[w] for w in got if w != 'me']
        self.assertEqual(len(others), len(set(others)))                 # none the same in the chat
        self.assertEqual(got['ann'], chat.preferred('ann'))             # the first keeps their own
        self.assertEqual(p.colour('grp', 'ann'), got['ann'])            # and keeps it
        self.assertEqual(p.colour('just-cy', 'cy'), chat.preferred('cy'))    # alone, everyone has their own
        clash = next(w for w in (f'x{i}' for i in range(200)) if chat.preferred(w) == chat.preferred('ann'))
        self.assertNotEqual(p.colour('grp', clash), got['ann'])         # someone with ann's: the next free
        c = chat.Chat(serve=s)
        run = lambda who: fc.Node('r', who, True, None, 'run', source=c, data=(p, 'grp', [], who))
        self.assertEqual(c.chip(run('bo')), ('person_fg', f'person{got["bo"]}'))
        self.assertEqual(c.chip(run('me')), ('home_fg', 'home_bg'))

    def test_an_open_chat_wears_its_colour(self):
        s, p = self.serve()
        s.apply({'event': 'chats', 'profile': p.id, 'chats': [{'id': 'ann', 'name': 'Ann'}, {'id': 'me', 'name': 'me'}]})
        c = chat.Chat(serve=s)
        node = lambda cid: fc.Node('/@x' + cid, cid, True, None, 'chat', source=c, data=(p, cid))
        ann = node('ann')
        self.assertIsNone(c.chip(ann))                                  # closed: plain
        ann.open = True
        self.assertEqual(c.chip(ann), ('person_fg', f'person{chat.preferred("ann")}'))   # Ann's, as in her messages
        self.assertEqual(c.chip(ann), c.chip(fc.Node('r', 'ann', True, None, 'run', source=c, data=(p, 'ann', [], 'ann'))))
        mine = node('me')
        mine.open = True
        self.assertEqual(c.chip(mine), ('home_fg', 'home_bg'))         # your own chat

    def test_a_skipped_profile_is_kept(self):
        s, _ = self.serve()
        self.assertEqual(s.skipped, [{'profile': 'Signal_+492', 'why': 'locked'}])


class Behaviour(unittest.TestCase):
    """What the provider does with what nchat says: sending, arrivals, typing, reading."""
    def setUp(self):
        self.asked, self.notified = [], []
        s = chat.Serve.detached()
        s.request = lambda op, **f: self.asked.append((op, f)) or len(self.asked)
        self.real_notify = chat.files.notify
        chat.files.notify = lambda title, body: self.notified.append((title, body))
        self.addCleanup(setattr, chat.files, 'notify', self.real_notify)
        self.c = chat.Chat(serve=s)
        self.signals = []
        self.c.signal = self.signals.append
        s.apply({'event': 'hello', 'profiles': [{'profile': 'W_1', 'protocol': 'WhatsAppMd', 'self': 'me',
                                                 'features': ['send_acknowledgement']}]})
        s.apply({'event': 'chats', 'profile': 'W_1', 'chats': [{'id': 'ann', 'name': 'Ann'}, {'id': 'bo', 'name': 'Bo'}]})
        self.p, self.s = s.profiles['W_1'], s

    def feed(self, e):
        with self.s.lock:
            self.s.apply(dict(e, profile='W_1'))
            then, self.s.then = self.s.then, []
        for fn in then:
            fn()

    def statuses(self):
        return [(w[2], w[3]) for w in self.signals if w[0] == 'status']

    def test_sending_spins_then_ticks_or_fails(self):
        canvas = fc.Canvas('', options(fc.Tree({})))
        self.c.send(canvas, self.p, 'ann', 'hi')
        self.assertEqual(self.asked[-1], ('send_message', {'profile': 'W_1', 'chat': 'ann', 'text': 'hi'}))
        self.assertEqual(self.statuses(), [('to Ann', 'busy')])
        self.feed({'event': 'sent', 'chat': 'ann', 'ok': True, 'request': len(self.asked)})
        self.assertEqual(self.statuses()[-1], ('sent to Ann', 'ok'))
        self.c.send(canvas, self.p, 'ann', 'again')
        self.feed({'event': 'sent', 'chat': 'ann', 'ok': False, 'request': len(self.asked)})
        self.assertEqual(self.statuses()[-1], ('not sent to Ann', 'failed'))
        self.c.send(canvas, self.p, 'bo', 'hello?')
        self.c.no_answer(len(self.asked))
        self.assertEqual(self.statuses()[-1], ('no answer sending to Bo', 'failed'))
        for _, _, _, _, timer in self.c.sending.values():
            timer.cancel()

    def test_an_arrival_elsewhere_pulses_notes_and_notifies(self):
        now = time.time() * 1000
        self.feed({'event': 'messages', 'chat': 'ann', 'messages': [{'id': 'm1', 'sender': 'ann', 'text': 'hello', 'time': now}]})
        self.assertIn(('pulse', '/@W_1\x1fann'), self.signals)
        self.assertIn(('Ann: hello', 'note'), self.statuses())
        self.assertEqual(self.notified, [('Ann', 'Ann: hello')])
        self.feed({'event': 'messages', 'chat': 'ann', 'messages': [{'id': 'm2', 'sender': 'ann', 'text': 'more', 'time': now}]})
        self.assertEqual(len(self.notified), 1)                         # not again within 10 s
        self.feed({'event': 'messages', 'chat': 'bo', 'messages': [{'id': 'old', 'sender': 'bo', 'text': 'x', 'time': 1000}]})
        self.assertEqual(len(self.notified), 1)                         # history isn't news
        self.p.chats['bo']['muted'] = True
        self.feed({'event': 'messages', 'chat': 'bo', 'messages': [{'id': 'b1', 'sender': 'bo', 'text': 'x', 'time': now}]})
        self.assertEqual(len(self.notified), 1)                         # muted: no notification
        self.c.looking = ('W_1', 'ann')
        n = len(self.signals)
        self.feed({'event': 'messages', 'chat': 'ann', 'messages': [{'id': 'm3', 'sender': 'ann', 'text': 'here', 'time': now}]})
        self.assertEqual(self.signals[n:], [('pulse', '/@W_1\x1fann\x1fm3')])   # you're in it: it flashes in, no more

    def test_typing_comes_and_goes(self):
        self.feed({'event': 'typing', 'chat': 'ann', 'user': 'ann', 'typing': True})
        self.assertEqual(self.statuses()[-1], ('Ann typing in Ann', 'busy'))
        since = self.p.typed[('ann', 'ann')]
        self.feed({'event': 'typing', 'chat': 'ann', 'user': 'ann', 'typing': True})
        self.c.stop_typing(self.p, 'ann', 'ann', since, 'k')            # an older timer: not the last word
        self.assertEqual(self.p.typing['ann'], {'ann'})
        self.c.stop_typing(self.p, 'ann', 'ann', self.p.typed[('ann', 'ann')], 'k')
        self.assertEqual((self.p.typing['ann'], self.statuses()[-1][1]), (set(), 'gone'))
        self.feed({'event': 'typing', 'chat': 'ann', 'user': 'me', 'typing': True})
        self.assertNotIn('You', ' '.join(t for t, _ in self.statuses()))  # your own typing isn't news

    def test_going_into_a_chat_reads_it(self):
        now = time.time() * 1000
        self.feed({'event': 'messages', 'chat': 'ann', 'messages': [
            {'id': 'a', 'sender': 'ann', 'text': '1', 'time': now, 'read': False},
            {'id': 'b', 'sender': 'me', 'text': '2', 'time': now, 'outgoing': True, 'read': False}]})
        self.p.chats['ann']['unread'] = True
        node = lambda kind: fc.Node('x', 'x', False, None, kind, source=self.c,
                                    data=(self.p, 'ann') if kind == 'chat' else (self.p, 'ann', {}))
        self.c.focus(node('chat'))                                      # passing by: not read
        self.assertFalse([a for a in self.asked if a[0] == 'mark_read'])
        self.c.focus(node('chat-message'))
        self.assertEqual([a[1]['message'] for a in self.asked if a[0] == 'mark_read'], ['a'])
        self.assertFalse(self.p.chats['ann']['unread'])
        self.c.read_receipts = False
        self.p.messages['ann']['c'] = {'id': 'c', 'sender': 'ann', 'read': False}
        self.c.focus(node('chat-message'))
        self.assertEqual(len([a for a in self.asked if a[0] == 'mark_read']), 1)   # read here, not told
        self.assertTrue(self.p.messages['ann']['c']['read'])

    def test_your_typing_is_told_and_stopped(self):
        changed = self.c.typing_in(self.p, 'ann')
        for text in ('h', 'hi', None):
            changed(text)
        self.assertEqual([a[1]['typing'] for a in self.asked if a[0] == 'send_typing'], [True, False])
        self.c.send_typing = False
        changed('x')
        self.assertEqual(len([a for a in self.asked if a[0] == 'send_typing']), 2)

    def test_each_message_says_what_then_when_and_whether_it_was_read(self):
        at = time.mktime((2026, 10, 30, 14, 32, 0, 0, 0, -1))
        mine = {'id': 'x', 'sender': 'me', 'text': 'hi', 'outgoing': True, 'time': at * 1000}
        self.assertEqual(self.c.message_label(self.p, mine), 'hi  14:32 ✓')
        mine['read'] = True
        self.assertEqual(self.c.message_label(self.p, mine), 'hi  14:32 ✓✓')
        self.assertEqual(self.c.message_label(self.p, dict(mine, sender='ann', outgoing=False)), 'hi  14:32')
        now = time.mktime((2026, 10, 30, 18, 0, 0, 0, 0, -1))
        self.assertEqual(chat.stamp(at, now), '14:32 · today')
        self.assertEqual(chat.stamp(at - 86400, now), '14:32 · yesterday')
        self.assertEqual(chat.stamp(at - 5 * 86400, now), '14:32 · 25 Oct')
        self.assertEqual(chat.stamp(time.mktime((2024, 3, 2, 9, 5, 0, 0, 0, -1)), now), '09:05 · 2 Mar 2024')


@unittest.skipUnless(NCHAT, 'nchat not installed')
class Live(unittest.TestCase):
    def setUp(self):
        self.providers = []

    def tearDown(self):
        for p in self.providers:
            p.serve.close()

    def start(self, conf):
        p = chat.Chat(NCHAT, conf, os.path.join(TMP, 'serve.log'))
        self.providers.append(p)
        return p

    def test_profiles_chats_and_the_conversation(self):
        p = self.start(dummy_conf('a'))
        c = fc.Canvas('', options(p))
        profile = c.focus
        self.assertEqual(profile.label(), 'Dummy 1')

        def chats():
            screen(c)                               # a frame takes in what nchat has said
            return [k for k in profile.load() if k.kind == 'chat']
        press(c, 'right')                           # asks for the chats; they come as events…
        self.assertTrue(until(lambda: len(chats()) > 1))
        self.assertEqual(c.focus.kind, 'chat')      # …and once they're here, it's gone into them
        self.assertTrue(until(lambda: any(k.label() != k.data[1] for k in chats())))    # contacts' names
        times = [p.chat(k).get('last_message_time', -1) for k in chats()]
        self.assertEqual(times, sorted(times, reverse=True))                         # the latest first
        the_chat = c.focus
        self.assertTrue(until(lambda: screen(c) and the_chat.data[1] in p.serve.profiles['Dummy_1'].asked))
        press(c, 'right')                           # its history was asked for on the way past
        self.assertTrue(until(lambda: screen(c) and c.focus.kind == 'run'))
        self.assertEqual((c.focus.kind, c.focus is the_chat.shown()[-1]), ('run', True))          # the latest
        self.assertEqual(the_chat.shown()[0].kind, 'earlier')
        press(c, 'right')
        self.assertEqual(c.focus.kind, 'chat-message')
        self.assertRegex(c.focus.label(), r'  \d\d:\d\d')                    # what it says, then when
        self.assertFalse(p.pane_for(c.focus))                                 # no pane: it's on the canvas,
        self.assertIn(c.focus.label().split('  ')[0][:20], ''.join(screen(c)[:-2]))   # drawn there in full

    def test_beside_mail_and_a_locked_profile(self):
        conf = dummy_conf('b')
        self.start(conf).serve.ready.wait(10)
        second = self.start(conf)                   # the same profile, which the first has locked
        second.serve.ready.wait(10)
        self.assertEqual([k['profile'] for k in second.serve.skipped], ['Dummy_1'])
        both = together.Together([Mailish({}), second])
        kids = fc.Canvas('', options(both)).root.load()
        self.assertEqual([k.name for k in kids], ['home', 'Dummy_1'])
        self.assertIn('locked', both.preview(kids[1]))

    def test_no_nchat_says_so_and_mail_stays(self):
        both = together.Together([Mailish({}), chat.Chat(os.path.join(TMP, 'no-such-nchat'), dummy_conf('c'))])
        kids = fc.Canvas('', options(both)).root.load()
        self.assertEqual([k.kind for k in kids], ['account', 'broken'])
        self.assertIn('nchat', both.preview(kids[1]))


class TogetherPassesActionsOn(unittest.TestCase):
    def test_each_nodes_own_provider_has_its_keys(self):
        class Writes(Mailish):
            def actions(self, node):
                return [('\x0e', 'new', '⌃N new')]

            def act(self, node, name, canvas):
                canvas.say(f'{name} on {node.name}')
        both = together.Together([Writes({}), Mailish({})])
        c = fc.Canvas('', options(both))
        self.assertIn('⌃N new', screen(c)[-1])
        press(c, '\x0e')
        self.assertIn('new on home', screen(c)[-2])


class Mailish(fc.Tree):
    """Stands in for the mail provider: one account."""
    KINDS = ('account',)

    def children(self, node):
        return [fc.Node('/home', 'home', False, node, 'account')] if node.kind == 'root' else []

if __name__ == '__main__':
    unittest.main()
