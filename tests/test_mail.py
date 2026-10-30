"""The mail provider over a real notmuch index of a small maildir, and the configs accounts.toml
makes. Needs notmuch and python3-notmuch2.

    python3 -m unittest discover tests"""
import atexit, contextlib, io, os, re, shutil, subprocess, tempfile, textwrap, time, unittest

from tests import TMP                            # the private home every test runs in
from msgcanvas import accounts, treecanvas as fc, mail

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
        self.assertIn('AuthMechs PLAIN LOGIN', sync[:sync.index('IMAPAccount uni')])
        self.assertIn('"!Kalender" "!Kalender/*"', sync)
        self.assertNotIn('PipelineDepth', sync)                  # only where a server needs it
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
        for entry, why in ((dict(name='x', key='Bad Key', address='a@b'), 'lower-case'),
                           (dict(name='  ', address='a@b'), 'needs a name'),
                           (dict(name='x', address='nope'), 'no address'),
                           (dict(name='x', address='a@b', type='imap'), 'imap_host'),
                           (dict(name='x', address='a@b', type='pop'), "isn't one of")):
            with self.assertRaisesRegex(ValueError, why):
                accounts.Account(entry, {})

    def test_a_name_is_shown_and_a_key_files_it(self):
        a = accounts.Account(dict(name='aaron@gmail.com', address='aaron@gmail.com', type='gmail'), {'maildir': '/m'})
        self.assertEqual((a.name, a.key, a.maildir), ('aaron@gmail.com', 'aaron-gmail-com', '/m/aaron-gmail-com'))
        b = accounts.Account(dict(name='Uni mail', key='lse', address='x@uni.ac.uk', type='outlook'), {'maildir': '/m'})
        self.assertEqual((b.key, b.maildir, b.token.endswith('/tokens/lse.gpg')), ('lse', '/m/lse', True))
        self.assertIn('mail/aaron-gmail-com', a.secret())
        made = accounts.mbsyncrc([a, b]) + accounts.msmtprc([a, b], '/log')
        self.assertIn('IMAPAccount lse', made)
        self.assertIn('account aaron-gmail-com', made)
        self.assertNotIn('@gmail.com-far', made)                 # names never reach the configs
        toml("""
            [[account]]
            name = "a@x.org"
            key = "same"
            address = "a@x.org"
            type = "seznam"
            [[account]]
            name = "b@x.org"
            key = "same"
            address = "b@x.org"
            type = "seznam"
            """)
        with self.assertRaisesRegex(ValueError, 'two accounts have this key'):
            accounts.load()

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

    def canvas(self, start='home', grouping='all', peek=False):
        return fc.Canvas(start, fc.options(provider=mail.Mail(self.accounts, watch=0, grouping=grouping), backdrop='off',
                                           ease=1, glyphs='plain', colmax=40, peek=peek))

    def screen(self, c, w=140, h=30):
        return [ANSI.sub('', l).rstrip() for l in c.frame(w, h, animate=False)[0]]

    def press(self, c, *keys):
        for k in keys:
            self.assertIsNone(fc.apply(c, k, c.opts))

    def test_folders_inbox_first_unread_in_a_column(self):
        c = self.canvas()
        folders = c.focus.parent.shown()
        self.assertEqual([k.label() for k in folders], ['Inbox', 'Sent', 'Archive'])
        self.assertEqual([c.source.count(k) for k in folders], [('1', 'git_dirty', True), None, None])
        self.assertIn('1 unread', self.screen(c)[-2])

    def test_threads_newest_first_and_the_reply_tree(self):
        c = self.canvas('home/INBOX')
        threads = c.focus.parent.shown()
        self.assertEqual([k.label() for k in threads], ['Your pass', 'Kiln quote'])
        self.assertEqual(c.source.count(threads[1]), ('3', 'stub', False))   # its messages, in the column
        self.assertEqual(c.source.mark(threads[1])[1], '●')     # Bo's reply is unread
        self.press(c, 'down', 'right')
        self.assertEqual(c.focus.label(), 'Ann Lee · 5 Oct')
        replies = [k for k in c.focus.load() if k.kind == 'message']
        self.assertEqual([k.label() for k in replies], ['Bo · 6 Oct'])
        self.assertEqual([k.label() for k in replies[0].load() if k.kind == 'message'], ['Ann Lee · 7 Oct'])

    def test_a_message_opens_onto_what_it_says(self):
        c = self.canvas('home/INBOX')                           # Your pass: one message, an HTML one
        parts = c.focus.load()
        kinds = [k.kind for k in parts]
        self.assertEqual(kinds[:3], ['subject', 'person', 'person'])
        self.assertEqual(parts[0].label(), 'Your pass')
        sender, to = parts[1], parts[2]
        self.assertEqual((sender.label(), c.source.count(sender)[0]), ('Rail', 'from  no-reply@rail.cz'))
        self.assertTrue(c.source.chip(sender)[1].startswith('person'))       # in their colour
        self.assertEqual(c.source.chip(to), ('home_fg', 'home_bg'))         # you, in yours
        body = [k.label() for k in parts if k.kind == 'body']
        self.assertEqual(body, ['Expires 14 Nov.', 'Thanks'])            # html, as text, a paragraph each
        self.assertFalse(c.source.pane_for(c.focus))            # no pictures: no pane
        c = self.canvas('home/Archive')
        files = [k for k in c.focus.load() if k.kind == 'attachment']
        self.assertEqual([k.label() for k in files], ['📎 form.pdf'])
        self.assertEqual(c.source.count(files[0])[0], '0 KB')

    def test_a_long_body_shows_its_start_and_more_on_enter(self):
        long = '\n\n'.join(f'paragraph {i}' for i in range(100))
        deliver('home/INBOX', 'm-long', 'From: Di <di@x.org>\nTo: aaron@example.org\nSubject: Newsletter\n'
                'Date: Thu, 01 Oct 2026 09:00:00 +0000\nMessage-ID: <long@x>\n\n' + long + '\n', seen=True)
        subprocess.run(['notmuch', 'new', '--quiet'], check=True)
        self.addCleanup(lambda: (os.remove(os.path.join(MAILDIR, 'home', 'INBOX', 'cur', 'm-long:2,S')),
                                 subprocess.run(['notmuch', 'new', '--quiet'])))
        c = self.canvas('home/INBOX')
        news = next(k for k in c.focus.parent.shown() if k.label() == 'Newsletter')
        more = [k for k in news.load() if k.kind == 'more']
        bodies = [k for k in news.load() if k.kind == 'body']
        self.assertEqual((len(bodies), more[0].label()), (20, '↓ 159 more lines'))
        self.assertIs(c.source.choose(more[0]), fc.STAY)
        self.screen(c)
        self.assertEqual(len([k for k in news.load() if k.kind == 'body']), 60)

    def test_inbox_by_date_by_sender_or_not_and_l_switches(self):
        c = self.canvas('home/INBOX', grouping='date')
        groups = c.focus.parent.shown()
        self.assertTrue(all(k.kind == 'group' for k in groups))
        self.assertEqual(sum(len(k.data['tids']) for k in groups), 2)    # each thread in one group
        self.assertEqual(c.source.count(groups[0])[1], 'stub')
        threads = groups[0].load()
        self.assertTrue(threads and all(k.kind == 'thread' for k in threads))
        self.assertIn('by date', self.screen(c)[-2])
        self.press(c, '\x0c')                                   # ⌃L: by sender
        self.screen(c)
        self.assertEqual(sorted(k.label() for k in c.focus.parent.shown() if k.kind == 'group'), ['Ann Lee', 'Rail'])
        self.press(c, '\x0c')                                   # ⌃L: not grouped
        self.screen(c)
        self.assertEqual([k.kind for k in c.focus.parent.load()][:2], ['thread', 'thread'])

    def test_t_splits_unread_then_flagged_apart_a_thread_whole(self):
        c = self.canvas('home/INBOX', grouping='all')
        self.press(c, 'left', '\x14')                           # on the folder, ⌃T: unread apart
        self.screen(c)
        parts = c.focus.shown()
        self.assertEqual([k.label() for k in parts], ['Unread', 'Read'])
        self.assertEqual([[t.label() for t in k.load()] for k in parts], [['Kiln quote'], ['Your pass']])
        self.assertEqual(c.source.mark(parts[0])[1], '●')
        self.assertIn('unread apart', self.screen(c)[-2])
        subprocess.run(['notmuch', 'tag', '+flagged', 'subject:"Your pass"'], check=True)
        self.addCleanup(subprocess.run, ['notmuch', 'tag', '-flagged', 'subject:"Your pass"'])
        self.press(c, '\x14')                                   # ⌃T: flagged apart
        self.screen(c)
        flagged, rest = c.focus.shown()
        self.assertEqual((flagged.label(), rest.label(), c.source.mark(flagged)[1]), ('Flagged', 'The rest', '★'))
        self.assertEqual([t.label() for t in flagged.load()], ['Your pass'])
        subprocess.run(['notmuch', 'tag', '-flagged', 'subject:"Your pass"'], check=True)
        c.source.invalidate()
        self.screen(c)
        self.assertEqual([k.label() for k in c.focus.shown()], ['The rest'])    # none now: just the rest
        self.press(c, '\x14')                                   # ⌃T: not split
        self.screen(c)
        self.assertEqual([k.kind for k in c.focus.shown()], ['thread', 'thread'])

    def test_unread_threads_and_messages_are_beacons_from_their_newest_unread(self):
        c = self.canvas('home/INBOX')
        kiln = next(k for k in c.focus.parent.shown() if k.label() == 'Kiln quote')
        bo = next(m for m in mail.Mail.children(c.source, next(k for k in kiln.load() if k.kind == 'message'))
                  if m.kind == 'message')
        self.assertEqual(c.source.beacon(kiln), bo.data['date'])          # Bo's reply: the one unread
        self.assertEqual(c.source.beacon(bo), bo.data['date'])
        self.assertIsNone(c.source.beacon(next(k for k in c.focus.parent.shown() if k.label() == 'Your pass')))
        c.source.beacon_days = (time.time() - bo.data['date']) / 86400 - 1     # older than that: just ●
        self.assertIsNone(c.source.beacon(kiln))

    def test_the_grouping_is_remembered(self):
        kept = os.path.join(TMP, 'view.json')
        p = mail.Mail(self.accounts, watch=0, remember=kept)
        p.grouping, p.split = 'sender', 'flagged'
        p.keep()
        again = mail.Mail(self.accounts, watch=0, remember=kept)
        self.assertEqual((again.grouping, again.split), ('sender', 'flagged'))

    def test_enter_stays_and_an_empty_account_says_so(self):
        c = self.canvas('home/INBOX')
        self.press(c, '\r')                                     # a thread of one: onto what it says
        self.assertEqual(c.focus.kind, 'subject')
        self.press(c, 'down', '\r')                             # Enter on its sender: nothing, it stays
        self.assertEqual(c.focus.kind, 'person')
        c = self.canvas('')
        self.press(c, 'down', 'right')
        self.assertIn('(no mail yet', '\n'.join(self.screen(c)))

    def test_new_mail_appears(self):
        p = mail.Mail(self.accounts, watch=0.05, grouping='all')
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

    def test_new_mail_is_told(self):
        import email.utils
        from msgcanvas import files
        told, signals = [], []
        real = files.notify
        files.notify = lambda title, body: told.append((title, body))
        self.addCleanup(setattr, files, 'notify', real)
        p = mail.Mail(self.accounts, watch=0)
        p.signal = signals.append
        with mail.database() as db:
            before = db.revision().rev
        now = email.utils.formatdate(localtime=True)
        deliver('home/INBOX', 'm9', letter('n9@x', 'Cy <cy@x.org>', 'Glaze test', now, 'new'))
        deliver('home/INBOX', 'm10', letter('n10@x', 'Di <di@x.org>', 'Seen already', now, 'old'), seen=True)
        subprocess.run(['notmuch', 'new', '--quiet'], check=True)
        with mail.database() as db:
            p.arrived(before, db.revision().rev)
        self.assertEqual(told, [('New mail', 'Cy: Glaze test')])          # the unread one, not the seen
        self.assertIn(('pulse', '/home'), signals)
        self.assertIn(('pulse', '/home/INBOX'), signals)
        self.assertIn(('status', 'mail-new', 'Cy: Glaze test', 'note'), signals)
        os.remove(os.path.join(MAILDIR, 'home', 'INBOX', 'cur', 'm10:2,S'))

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

    def test_date_groups(self):
        now = time.mktime((2026, 10, 30, 12, 0, 0, 0, 0, -1))           # a Friday
        at = lambda *d: time.mktime((*d, 0, 0, -1))
        self.assertEqual(mail.when_group(at(2026, 10, 30, 9, 0, 0), now), 'Today')
        self.assertEqual(mail.when_group(at(2026, 10, 29, 23, 0, 0), now), 'Yesterday')
        self.assertEqual(mail.when_group(at(2026, 10, 26, 9, 0, 0), now), 'This week')
        self.assertEqual(mail.when_group(at(2026, 10, 20, 9, 0, 0), now), 'Last week')
        self.assertEqual(mail.when_group(at(2026, 10, 3, 9, 0, 0), now), 'Earlier this month')
        self.assertEqual(mail.when_group(at(2026, 3, 3, 9, 0, 0), now), 'March')
        self.assertEqual(mail.when_group(at(2024, 3, 3, 9, 0, 0), now), '2024')
        self.assertEqual(mail.first_author('Ann Lee, Bo| Cy'), 'Ann Lee')

    def test_names_and_folders(self):
        self.assertEqual(mail.who('"Lee, Ann" <ann@x.org>'), 'Lee, Ann')
        self.assertEqual(mail.who('bo@x.org'), 'bo')
        self.assertEqual(mail.folder_name('[Gmail]/Sent Mail'), 'Sent Mail')
        folders = ['Junk-E-Mail', 'Archiv', 'Gesendete Elemente', 'Zeug', 'INBOX', 'Entwürfe', 'Koš']
        self.assertEqual(sorted(folders, key=mail.folder_rank),
                         ['INBOX', 'Gesendete Elemente', 'Entwürfe', 'Archiv', 'Junk-E-Mail', 'Koš', 'Zeug'])
        self.assertEqual(mail.quote('a "b"'), '"a ""b"""')


if __name__ == '__main__':
    unittest.main()
