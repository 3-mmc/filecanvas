"""The chat provider: nchat's events kept and shown, and the real `nchat --serve` on a Dummy
profile (skipped where nchat isn't installed: NCHAT names another one).

    python3 -m unittest discover tests"""
import atexit, os, re, shutil, tempfile, time, unittest

import tests                                    # the private test home, before msgcanvas

from msgcanvas import chat, filecanvas as fc, together

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
        s = chat.Serve.__new__(chat.Serve)
        s.profiles, s.skipped = {}, []
        s.ready = __import__('threading').Event()
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

    def test_a_skipped_profile_is_kept(self):
        s, _ = self.serve()
        self.assertEqual(s.skipped, [{'profile': 'Signal_+492', 'why': 'locked'}])


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
        press(c, 'right')                           # asks for the chats; they come as events
        self.assertTrue(until(lambda: len(chats()) > 1))
        press(c, 'right')
        self.assertEqual(c.focus.kind, 'chat')
        self.assertTrue(until(lambda: any(k.label() != k.data[1] for k in chats())))    # contacts' names
        times = [p.chat(k).get('last_message_time', -1) for k in chats()]
        self.assertEqual(times, sorted(times, reverse=True))                         # the latest first
        self.assertTrue(until(lambda: '(fetching…)' not in '\n'.join(screen(c))))     # its history came
        press(c, 'right')
        self.assertEqual(c.focus.kind, 'chat-message')
        self.assertIn(': ', c.focus.label())

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
