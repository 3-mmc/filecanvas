"""The mail provider over a real notmuch index of a small maildir, and the configs accounts.toml
makes. Needs notmuch and python3-notmuch2.

    python3 -m unittest discover tests"""
import atexit, contextlib, io, os, re, shutil, subprocess, tempfile, textwrap, time, unittest

TMP = tempfile.mkdtemp(prefix='msgcanvas-test-')
atexit.register(shutil.rmtree, TMP, True)
os.environ['XDG_CONFIG_HOME'] = os.path.join(TMP, 'config')
os.environ['XDG_DATA_HOME'] = os.path.join(TMP, 'data')
os.environ['XDG_STATE_HOME'] = os.path.join(TMP, 'state')

from msgcanvas import accounts, filecanvas as fc, mail   # noqa: E402

ANSI = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]')
MAILDIR = os.path.join(TMP, 'Mail')


def deliver(folder, name, text, seen=False):
    """A message file in a maildir folder: cur/ with S (seen) or new/."""
    d = os.path.join(MAILDIR, folder)
    for sub in ('cur', 'new', 'tmp'):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    path = os.path.join(d, 'cur', name + ':2,S') if seen else os.path.join(d, 'new', name)
    with open(path, 'w') as f:
        f.write(textwrap.dedent(text).lstrip())


def letter(mid, frm, subject, date, body, reply=None, extra=''):
    return f'''
        From: {frm}
        To: Aaron <aaron@example.org>
        Subject: {subject}
        Date: {date}
        Message-ID: <{mid}>
        {'In-Reply-To: <' + reply + '>' if reply else 'X-Nothing: 1'}
        {extra}
        {body}
        '''


def build():
    deliver('home/INBOX', 'm1', letter('k1@x', 'Ann Lee <ann@x.org>', 'Kiln quote',
                                       'Mon, 05 Oct 2026 09:00:00 +0000', 'Hold at 1100 for twenty minutes.'), seen=True)
    deliver('home/INBOX', 'm2', letter('k2@x', 'Bo <bo@x.org>', 'Re: Kiln quote',
                                       'Tue, 06 Oct 2026 10:00:00 +0000', 'Thursday works.', reply='k1@x'))
    deliver('home/INBOX', 'm3', letter('k3@x', 'Ann Lee <ann@x.org>', 'Re: Kiln quote',
                                       'Wed, 07 Oct 2026 11:00:00 +0000', 'Great.', reply='k2@x'), seen=True)
    deliver('home/INBOX', 'm4', letter('h1@x', 'Rail <no-reply@rail.cz>', 'Your pass',
                                       'Thu, 08 Oct 2026 12:00:00 +0000',
                                       '<html><head><style>p{}</style></head><body><p>Expires <b>14 Nov</b>.</p>'
                                       '<p>Thanks</p></body></html>',
                                       extra='Content-Type: text/html; charset=utf-8\n'), seen=True)
    deliver('home/Sent', 's1', letter('s1@x', 'Aaron <aaron@example.org>', 'Hello',
                                      'Fri, 02 Oct 2026 08:00:00 +0000', 'hi'), seen=True)
    deliver('home/Archive', 'a1', '''
        From: Uni <office@uni.example>
        Subject: Forms
        Date: Thu, 01 Oct 2026 08:00:00 +0000
        Message-ID: <a1@x>
        MIME-Version: 1.0
        Content-Type: multipart/mixed; boundary="B"

        --B
        Content-Type: text/plain

        Attached.
        --B
        Content-Type: application/pdf
        Content-Disposition: attachment; filename="form.pdf"
        Content-Transfer-Encoding: base64

        JVBERi0xLjQK
        --B--
        ''', seen=True)
    os.makedirs(os.path.join(MAILDIR, 'empty'), exist_ok=True)      # set up, never synced


def toml(text):
    os.makedirs(accounts.CONFIG, exist_ok=True)
    with open(accounts.ACCOUNTS, 'w') as f:
        f.write(textwrap.dedent(text))


class Configs(unittest.TestCase):
    def test_a_template_first_then_configs_from_it(self):
        if os.path.exists(accounts.ACCOUNTS):
            os.remove(accounts.ACCOUNTS)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(accounts.setup('A', 'a@gmail.com'), [])
        self.assertIn('a@gmail.com', open(accounts.ACCOUNTS).read())
        toml(f'''
            [defaults]
            real_name = "Aaron"
            maildir = "{MAILDIR}"
            max_messages = 500
            [[account]]
            name = "home"
            address = "aaron@gmail.com"
            type = "gmail"
            [[account]]
            name = "uni"
            address = "a@uni.example"
            type = "outlook"
            [[account]]
            name = "cz"
            address = "a@seznam.cz"
            type = "seznam"
            max_messages = 0
            ''')
        made = accounts.setup()
        self.assertEqual([a.name for a in made], ['home', 'uni', 'cz'])
        sync = open(accounts.MBSYNCRC).read()
        self.assertIn('PassCmd "pass show mail/home"', sync)
        self.assertIn('Patterns * "![Gmail]/All Mail" "![Gmail]/Important"', sync)
        self.assertIn('Sync Pull', sync)
        self.assertEqual(sync.count('MaxMessages 500'), 2)          # cz keeps everything
        uni = sync[sync.index('IMAPAccount uni'):sync.index('IMAPAccount cz')]
        self.assertIn('AuthMechs XOAUTH2', uni)
        self.assertIn('mutt_oauth2.py', uni)
        send = open(accounts.MSMTPRC).read()
        self.assertIn('host smtp.seznam.cz\nport 465\ntls_starttls off', send)
        self.assertIn('auth xoauth2', send)
        self.assertEqual(oct(os.stat(accounts.MSMTPRC).st_mode & 0o777), '0o600')
        nm = open(accounts.NOTMUCH).read()
        self.assertIn('other_email=a@uni.example;a@seznam.cz', nm)

    def test_bad_accounts_say_why(self):
        for entry, why in ((dict(name='Bad Name', address='a@b'), 'lower-case'),
                           (dict(name='x', address='nope'), 'no address'),
                           (dict(name='x', address='a@b', type='imap'), 'imap_host'),
                           (dict(name='x', address='a@b', type='pop'), "isn't one of")):
            with self.assertRaisesRegex(ValueError, why):
                accounts.Account(entry, {})

    def test_never_over_someone_elses_file(self):
        path = os.path.join(TMP, 'theirs')
        with open(path, 'w') as f:
            f.write('# mine\n')
        with self.assertRaises(SystemExit):
            accounts.write(path, 'x')


class Provider(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build()
        cfg = os.path.join(TMP, 'notmuch-config')
        with open(cfg, 'w') as f:
            f.write(f'[database]\npath={MAILDIR}\n[new]\ntags=inbox;\n[maildir]\nsynchronize_flags=true\n')
        os.environ['NOTMUCH_CONFIG'] = cfg
        subprocess.run(['notmuch', 'new', '--quiet'], check=True)
        defaults = {'maildir': MAILDIR}
        cls.accounts = [accounts.Account(dict(name='home', address='aaron@example.org', type='seznam'), defaults),
                        accounts.Account(dict(name='empty', address='e@example.org', type='seznam'), defaults)]

    def canvas(self, start='home'):
        return fc.Canvas(start, fc.options(provider=mail.Mail(self.accounts, watch=0), backdrop='off',
                                           ease=1, glyphs='plain', colmax=40))

    def screen(self, c, w=120, h=24):
        return [ANSI.sub('', l).rstrip() for l in c.frame(w, h, animate=False)[0]]

    def press(self, c, *keys):
        for k in keys:
            self.assertIsNone(fc.apply(c, k, c.opts))

    def test_folders_inbox_first_with_unread(self):
        c = self.canvas()
        self.assertEqual([k.label() for k in c.focus.parent.shown()], ['Inbox 1', 'Sent', 'Archive'])
        self.assertIn('1 unread', self.screen(c)[-2])

    def test_threads_newest_first_and_the_reply_tree(self):
        c = self.canvas('home/INBOX')
        threads = c.focus.parent.shown()
        self.assertEqual([k.label() for k in threads], ['Your pass', 'Kiln quote (3)'])
        self.assertFalse(threads[0].is_dir)                     # one message: nothing to open
        self.assertEqual(c.source.mark(threads[1])[1], '●')     # Bo's reply is unread
        self.press(c, 'down', 'right')
        self.assertEqual(c.focus.label(), 'Ann Lee · 5 Oct')
        self.press(c, 'right')
        self.assertEqual(c.focus.label(), 'Bo · 6 Oct')
        self.press(c, 'right')
        self.assertEqual(c.focus.label(), 'Ann Lee · 7 Oct')
        self.assertIn('Great.', '\n'.join(self.screen(c)))

    def test_the_pane_reads_messages(self):
        c = self.canvas('home/INBOX')
        text = '\n'.join(self.screen(c))
        self.assertIn('Expires 14 Nov.', text)                  # html, as text
        self.assertNotIn('p{}', text)
        self.press(c, 'down')
        self.assertIn('3 messages', '\n'.join(self.screen(c)))  # a thread: who wrote, in order
        c = self.canvas('home/Archive')
        self.assertIn('📎 form.pdf', '\n'.join(self.screen(c)))

    def test_enter_stays_and_an_empty_account_says_so(self):
        c = self.canvas('home/INBOX')
        self.press(c, '\r', 'down', '\r')
        self.assertEqual(c.focus.kind, 'message')
        c = self.canvas('')
        self.press(c, 'down', 'right')
        self.assertIn('(no mail yet', '\n'.join(self.screen(c)))

    def test_new_mail_appears(self):
        p = mail.Mail(self.accounts, watch=0.05)
        c = fc.Canvas('home/INBOX', fc.options(provider=p, backdrop='off', ease=1, colmax=40))
        time.sleep(0.2)
        deliver('home/INBOX', 'm9', letter('n9@x', 'Cy <cy@x.org>', 'Fresh', 'Fri, 09 Oct 2026 08:00:00 +0000', 'new'))
        subprocess.run(['notmuch', 'new', '--quiet'], check=True)
        for _ in range(50):
            time.sleep(0.05)
            if p.changed:
                break
        self.screen(c)
        self.assertEqual(c.focus.parent.shown()[0].label(), 'Fresh')
        self.assertEqual(c.focus.label(), 'Your pass')          # where you were

    def tearDown(self):
        fresh = os.path.join(MAILDIR, 'home', 'INBOX', 'new', 'm9')
        if os.path.exists(fresh):
            os.remove(fresh)
            subprocess.run(['notmuch', 'new', '--quiet'], check=True)


class Thunderbird(unittest.TestCase):
    def test_imap_accounts_and_how_they_sign_in(self):
        from msgcanvas import thunderbird
        path = os.path.join(TMP, 'prefs.js')
        with open(path, 'w') as f:
            f.write(textwrap.dedent('''
                user_pref("mail.accountmanager.accounts", "account1,account2,account3,account4,account5");
                user_pref("mail.account.account1.identities", "id1");
                user_pref("mail.account.account1.server", "server1");
                user_pref("mail.identity.id1.useremail", "me@gmail.com");
                user_pref("mail.server.server1.authMethod", 10);
                user_pref("mail.server.server1.hostname", "imap.gmail.com");
                user_pref("mail.server.server1.type", "imap");
                user_pref("mail.server.server1.userName", "me@gmail.com");
                user_pref("mail.account.account2.server", "server2");
                user_pref("mail.server.server2.type", "none");
                user_pref("mail.account.account3.identities", "id3");
                user_pref("mail.account.account3.server", "server3");
                user_pref("mail.identity.id3.useremail", "a.b@uni.ac.uk");
                user_pref("mail.server.server3.hostname", "outlook.office365.com");
                user_pref("mail.server.server3.type", "imap");
                user_pref("mail.account.account4.identities", "id4");
                user_pref("mail.account.account4.server", "server4");
                user_pref("mail.identity.id4.useremail", "x@small.example");
                user_pref("mail.identity.id4.smtpServer", "smtp4");
                user_pref("mail.server.server4.hostname", "mail.small.example");
                user_pref("mail.server.server4.port", 143);
                user_pref("mail.server.server4.type", "imap");
                user_pref("mail.server.server4.userName", "xuser");
                user_pref("mail.smtpserver.smtp4.hostname", "smtp.small.example");
                user_pref("mail.smtpserver.smtp4.port", 465);
                user_pref("mail.account.account5.identities", "id5");
                user_pref("mail.account.account5.server", "server5");
                user_pref("mail.identity.id5.useremail", "me@small.example");
                user_pref("mail.server.server5.hostname", "mail.small.example");
                user_pref("mail.server.server5.type", "imap");
                '''))
        got = thunderbird.accounts(path)
        self.assertEqual([(e['name'], e['type'], e.get('auth')) for e in got],
                         [('me', 'gmail', 'google'), ('uni', 'outlook', None),
                          ('small', 'imap', None), ('small-2', 'imap', None)])
        self.assertEqual((got[2]['user'], got[2]['imap_port'], got[2]['smtp_host'], got[2]['smtp_port']),
                         ('xuser', 143, 'smtp.small.example', 465))
        made = [accounts.Account(e, {}) for e in got]           # each one is a valid account
        self.assertEqual(made[0].secret().split()[-1], made[0].token)
        self.assertIn('name = "uni"', thunderbird.toml(got, 'here'))
        self.assertNotIn('_thunderbird', thunderbird.toml(got, 'here'))

class Helpers(unittest.TestCase):
    def test_short_dates(self):
        now = time.mktime((2026, 10, 30, 12, 0, 0, 0, 0, -1))
        self.assertEqual(mail.when(now - 3600, now), '11:00')
        self.assertEqual(mail.when(time.mktime((2026, 3, 2, 9, 0, 0, 0, 0, -1)), now), '2 Mar')
        self.assertEqual(mail.when(time.mktime((2024, 3, 2, 9, 0, 0, 0, 0, -1)), now), '2 Mar 2024')

    def test_names_and_folders(self):
        self.assertEqual(mail.who('"Lee, Ann" <ann@x.org>'), 'Lee, Ann')
        self.assertEqual(mail.who('bo@x.org'), 'bo')
        self.assertEqual(mail.folder_name('[Gmail]/Sent Mail'), 'Sent Mail')
        self.assertEqual(mail.quote('a "b"'), '"a ""b"""')


if __name__ == '__main__':
    unittest.main()
