"""Writing mail: drafts, replies, forwards, attachments, sending (through a stand-in for msmtp
that keeps what it's given), and the canvas's part: the editor outside, then y/e/a/d/q.

    python3 -m unittest discover tests"""
import email, email.policy, os, shutil, subprocess, sys, tempfile, unittest

import tests                                    # the private test home, before msgcanvas

import tests.test_mail as tm          # its maildir, notmuch index and environment
from msgcanvas import accounts, compose, files, filecanvas as fc, mail

OUT = os.path.join(tm.TMP, 'sent-by-msmtp.eml')
FAKE_MSMTP = [sys.executable, '-c', f'import sys; open({OUT!r}, "wb").write(sys.stdin.buffer.read())']


def account(name='home'):
    return accounts.Account(dict(name=name, address='aaron@example.org', type='seznam', real_name='Aaron'),
                            {'maildir': tm.MAILDIR})


def forget_sent():
    """Take the copies sending filed back out of the shared maildir: other tests count its mail."""
    sent = os.path.join(tm.MAILDIR, 'home', 'Sent', 'cur')
    gone = [f for f in os.listdir(sent) if '.msgcanvas:' in f] if os.path.isdir(sent) else []
    for f in gone:
        os.remove(os.path.join(sent, f))
    if gone:
        subprocess.run(['notmuch', 'new', '--quiet'], check=True)


def path_of(mid):
    with mail.database() as db:
        return str(db.find(mid).path)


class Drafts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tm.Provider.setUpClass()       # the maildir, indexed

    def tearDown(self):
        forget_sent()

    def setUp(self):
        compose.SEND = FAKE_MSMTP
        shutil.rmtree(compose.DRAFTS, ignore_errors=True)
        if os.path.exists(OUT):
            os.remove(OUT)

    def test_a_reply_answers_the_sender_quotes_them_and_threads(self):
        p = compose.reply(account(), path_of('k2@x'))
        h, attach, body = compose.parse(p)
        self.assertEqual((h['To'], h['Subject'], h['In-Reply-To']), ('Bo <bo@x.org>', 'Re: Kiln quote', '<k2@x>'))
        self.assertEqual(h['References'], '<k1@x> <k2@x>')
        self.assertIn('Bo <bo@x.org> wrote:\n> Thursday works.', body)
        self.assertEqual(h['From'], 'Aaron <aaron@example.org>')

    def test_reply_all_leaves_out_your_own_addresses(self):
        m = os.path.join(tm.TMP, 'group.eml')
        with open(m, 'w') as f:
            f.write('From: Ann <ann@x.org>\nTo: aaron@example.org, Bo <bo@x.org>\nCc: other@me.cz, Cy <cy@x.org>\n'
                    'Subject: Re: Plans\nMessage-ID: <g@x>\n\nhello\n')
        h, _, _ = compose.parse(compose.reply(account(), m, everyone=True, mine=['other@me.cz']))
        self.assertEqual(h['To'], 'Ann <ann@x.org>')
        self.assertEqual(h['Cc'], 'Bo <bo@x.org>, Cy <cy@x.org>')
        self.assertEqual(h['Subject'], 'Re: Plans')                      # not Re: Re:

    def test_a_forward_carries_the_original(self):
        p = compose.forward(account(), path_of('a1@x'))
        compose.set_header(p, 'To', 'z@x.org')
        msg = compose.build(p)
        self.assertEqual(msg['Subject'], 'Fwd: Forms')
        self.assertIn('---------- Forwarded message ----------', msg.get_body().get_content())
        self.assertEqual([a.get_filename() for a in msg.iter_attachments()], ['forwarded.eml'])

    def test_attachments_and_what_stops_a_send(self):
        p = compose.new(account(), subject='hi', body='text')
        with self.assertRaisesRegex(ValueError, 'nobody'):
            compose.build(p)
        compose.set_header(p, 'Bcc', 'secret@x.org')
        pdf = os.path.join(tm.TMP, 'plan.pdf')
        with open(pdf, 'wb') as f:
            f.write(b'%PDF-1.4 x')
        compose.attach(p, pdf)
        compose.attach(p, os.path.join(tm.TMP, 'gone.png'))
        with self.assertRaisesRegex(ValueError, 'gone.png'):
            compose.build(p)
        h, files_, body = compose.parse(p)
        compose.write(p, h, files_[:1], body)
        msg = compose.build(p)
        part = next(msg.iter_attachments())
        self.assertEqual((part.get_filename(), part.get_content_type()), ('plan.pdf', 'application/pdf'))
        self.assertTrue(msg['Message-ID'].endswith('@example.org>'))

    def test_sending_hands_it_over_files_a_copy_and_drops_the_draft(self):
        p = compose.new(account(), to='Ann <ann@x.org>', subject='Sent one', body='hello')
        compose.set_header(p, 'Bcc', 'secret@x.org')
        self.assertEqual(compose.send(p, account()), 'ann@x.org')
        with open(OUT, 'rb') as f:
            given = email.message_from_binary_file(f, policy=email.policy.default)
        self.assertEqual((given['Subject'], given['Bcc']), ('Sent one', 'secret@x.org'))   # msmtp drops Bcc itself
        self.assertFalse(os.path.exists(p))
        with mail.database() as db:
            copy = next(db.messages(f'id:{given["Message-ID"].strip("<>")}'))
            self.assertIn('/home/Sent/cur/', str(copy.path))
            self.assertNotIn('unread', set(copy.tags))
        with open(str(copy.path), 'rb') as f:
            self.assertNotIn(b'secret@x.org', f.read())                                  # nor does the copy

    def test_a_failed_send_says_why_and_keeps_the_draft(self):
        compose.SEND = ['sh', '-c', 'echo "msmtp: authentication failed (method PLAIN)" >&2; exit 77']
        p = compose.new(account(), to='ann@x.org', subject='x', body='y')
        with self.assertRaisesRegex(OSError, 'authentication failed'):
            compose.send(p, account())
        self.assertTrue(os.path.exists(p))


class OnTheCanvas(unittest.TestCase):
    """The provider's part: ⌃R, the editor outside, then the choice."""
    @classmethod
    def setUpClass(cls):
        tm.Provider.setUpClass()

    def tearDown(self):
        forget_sent()

    def setUp(self):
        compose.SEND = FAKE_MSMTP
        shutil.rmtree(compose.DRAFTS, ignore_errors=True)
        self.edited = []
        self.real_terminal = fc.terminal
        mail.fc.terminal = lambda cmd: self.edited.append(cmd) or 0     # the editor, as if you'd saved
        self.addCleanup(setattr, mail.fc, 'terminal', self.real_terminal)
        self.opened = []
        self.real_open = files.open_file
        files.open_file = self.opened.append
        self.addCleanup(setattr, files, 'open_file', self.real_open)

    def canvas(self, start='home/INBOX'):
        p = mail.Mail([account()], watch=0, pick=lambda start: os.path.join(tm.TMP, 'plan.pdf'))
        return fc.Canvas(start, fc.options(provider=p, backdrop='off', ease=1, glyphs='plain', colmax=40))

    def press(self, c, *keys):
        for k in keys:
            r = fc.apply(c, k, c.opts)
        return r

    def screen(self, c):
        return [tm.ANSI.sub('', l).rstrip() for l in c.frame(120, 24, animate=False)[0]]

    def test_reply_edit_attach_send(self):
        c = self.canvas()
        self.press(c, 'down', 'right')                  # Kiln quote: Ann's message
        self.assertIn('⌃R reply', self.screen(c)[-1])
        self.press(c, '\x12')
        c.drain()                                       # the editor
        self.assertTrue(self.edited and self.edited[0][-1].endswith('.eml'))
        self.assertIn('y send', self.screen(c)[-2])
        with open(os.path.join(tm.TMP, 'plan.pdf'), 'wb') as f:
            f.write(b'%PDF')
        self.press(c, 'a')
        c.drain()                                       # the file canvas, then asked again
        self.assertIn('1 attached', self.screen(c)[-2])
        self.press(c, 'y')
        self.assertIn('sent to ann@x.org', self.screen(c)[-2])
        with open(OUT, 'rb') as f:
            msg = email.message_from_binary_file(f, policy=email.policy.default)
        self.assertEqual((msg['In-Reply-To'], [a.get_filename() for a in msg.iter_attachments()]),
                         ('<k1@x>', ['plan.pdf']))

    def test_kept_for_later_it_waits_in_unsent(self):
        c = self.canvas('home')
        self.press(c, '\x0e')
        c.drain()
        self.press(c, 'd')
        self.screen(c)
        folders = [k.label() for k in c.focus.parent.load()]
        self.assertIn('Unsent 1', folders)
        while c.focus.kind != 'drafts':
            self.press(c, 'down')
        self.press(c, 'right')
        self.assertEqual(c.focus.kind, 'draft')
        self.assertIn('⌃E edit', self.screen(c)[-1])
        self.press(c, '\x04', 'y')
        self.screen(c)
        self.assertEqual(compose.drafts(account()), [])

    def test_attached_files_open_and_save(self):
        c = self.canvas('home/Archive')
        self.assertIn('⌃O open attached', self.screen(c)[-1])
        self.press(c, '\x0f')
        self.assertTrue(self.opened[0].endswith('form.pdf'))
        with open(self.opened[0], 'rb') as f:
            self.assertTrue(f.read().startswith(b'%PDF'))
        real = files.downloads
        files.downloads = lambda: os.path.join(tm.TMP, 'dl')
        self.addCleanup(setattr, files, 'downloads', real)
        self.press(c, '\x13')
        self.assertEqual(os.listdir(os.path.join(tm.TMP, 'dl')), ['form.pdf'])


if __name__ == '__main__':
    unittest.main()
