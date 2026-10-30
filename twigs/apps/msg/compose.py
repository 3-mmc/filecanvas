"""Writing mail: drafts as text files you edit in your editor, headers on top, as mutt does.

    From: Aaron <aaron@example.org>
    To: ann@example.org
    Subject: Re: Kiln quote
    Attach: ~/Downloads/schedule.pdf          (any number of these; ⌃A on the canvas adds one)

    The body, under a blank line.

A draft is kept under ~/.local/share/msgcanvas/drafts/<account>/ until it's sent or thrown away.
Sending builds the MIME message, hands it to msmtp (accounts.MSMTPRC) and files a copy in the
account's Sent folder, so it's there before the next sync."""
import email, email.policy, email.utils, mimetypes, os, re, shutil, subprocess, time

from . import accounts

DRAFTS = os.path.join(accounts.DATA, 'drafts')
HEADERS = ('From', 'To', 'Cc', 'Bcc', 'Subject')
HIDDEN = ('In-Reply-To', 'References', 'Forward')      # kept in the draft, but under the body's notice
SEND = None     # the command that sends (tests replace it): msmtp with msgcanvas's config, by default


def editor():
    return os.environ.get('VISUAL') or os.environ.get('EDITOR') or 'nano'


def edit_command(path):
    """The editor on `path`, its cursor on the body where it knows how."""
    cmd = editor().split()
    with open(path) as f:
        lines = f.read().split('\n')
    body = next((i for i, l in enumerate(lines) if not l.strip()), len(lines)) + 2
    if os.path.basename(cmd[0]) in ('nano', 'vi', 'vim', 'nvim', 'emacs', 'kak', 'hx'):
        cmd.append(f'+{body}')
    return cmd + [path]


def addresses(text):
    return [a for _, a in email.utils.getaddresses([text or '']) if a]


def mailbox(account):
    return email.utils.formataddr((account.real_name, account.address))


def new(account, to='', subject='', body='', extra=()):
    """A draft file for `account`, its path."""
    d = os.path.join(DRAFTS, account.key)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, time.strftime('%Y%m%d-%H%M%S') + f'-{os.getpid()}-{len(os.listdir(d))}.eml')
    head = [f'From: {mailbox(account)}', f'To: {to}', 'Cc: ', 'Bcc: ', f'Subject: {subject}']
    head += [f'{k}: {v}' for k, v in extra if v]
    with open(path, 'w') as f:
        f.write('\n'.join(head) + '\n\n' + body)
    return path


def original(path):
    with open(path, 'rb') as f:
        return email.message_from_binary_file(f, policy=email.policy.default)


def text_of(msg):
    part = msg.get_body(preferencelist=('plain', 'html'))
    if part is None:
        return ''
    try:
        body = part.get_content()
    except (LookupError, ValueError):
        body = (part.get_payload(decode=True) or b'').decode('utf-8', 'replace')
    if part.get_content_type() == 'text/html':
        from .mail import html_text
        body = html_text(body)
    return body


def reply(account, path, everyone=False, mine=()):
    """A reply to the message in `path`: to whoever it says to reply to, and with `everyone`,
    to all it went to, less your own addresses (`mine`)."""
    m = original(path)
    to = m['Reply-To'] or m['From'] or ''
    cc = ''
    if everyone:
        own = {a.lower() for a in (account.address, *mine)}
        seen = {a.lower() for a in addresses(to)} | own
        rest = [f for f in email.utils.getaddresses([m['To'] or '', m['Cc'] or ''])
                if f[1] and f[1].lower() not in seen and not seen.add(f[1].lower())]
        cc = ', '.join(email.utils.formataddr(f) for f in rest)
    subject = m['Subject'] or ''
    if not re.match(r'(?i)re:', subject):
        subject = 'Re: ' + subject
    # its References and its id; with no References, what it replied to stands in (RFC 5322 3.6.4)
    before = (m['References'] or m['In-Reply-To'] or '').strip()
    refs = ' '.join(x for x in (before, m['Message-ID'] or '') if x)
    who, date = m['From'] or 'someone', m['Date'] or ''
    quoted = '\n'.join('> ' + l if l else '>' for l in text_of(m).strip().split('\n'))
    p = new(account, to=to, subject=subject, body=f'\n\nOn {date}, {who} wrote:\n{quoted}\n',
            extra=[('In-Reply-To', m['Message-ID']), ('References', refs)])
    if cc:
        set_header(p, 'Cc', cc)
    return p


def forward(account, path):
    """The message in `path` passed on: its text inline, and itself attached, as it was."""
    m = original(path)
    subject = m['Subject'] or ''
    if not re.match(r'(?i)fwd?:', subject):
        subject = 'Fwd: ' + subject
    head = '\n'.join(f'{k}: {m[k]}' for k in ('From', 'Date', 'Subject', 'To', 'Cc') if m[k])
    return new(account, subject=subject,
               body=f'\n\n---------- Forwarded message ----------\n{head}\n\n{text_of(m).strip()}\n',
               extra=[('Forward', path)])


def parse(path):
    """(headers {name: value}, attachments [path], body) of a draft."""
    with open(path) as f:
        text = f.read()
    head, _, body = text.partition('\n\n')
    headers, attach, last = {}, [], None
    for line in head.split('\n'):
        if line[:1] in (' ', '\t') and last:            # a header that goes on to the next line
            headers[last] += ' ' + line.strip()
            continue
        name, _, value = line.partition(':')
        name, value = name.strip(), value.strip()
        if name.lower() == 'attach':
            if value:
                attach.append(os.path.expanduser(value))
            last = None
        elif name:
            last = next((h for h in HEADERS + HIDDEN if h.lower() == name.lower()), name)
            headers[last] = value
    return headers, attach, body


def set_header(path, name, value):
    headers, attach, body = parse(path)
    headers[name] = value
    write(path, headers, attach, body)


def attach(path, file):
    headers, files, body = parse(path)
    write(path, headers, files + [file], body)


def write(path, headers, attach, body):
    lines = [f'{k}: {v}' for k, v in headers.items()] + [f'Attach: {a}' for a in attach]
    with open(path, 'w') as f:
        f.write('\n'.join(lines) + '\n\n' + body)


def build(path):
    """The draft as a message to send: an email.message.EmailMessage."""
    headers, files, body = parse(path)
    if not addresses(headers.get('To', '')) + addresses(headers.get('Cc', '')) + addresses(headers.get('Bcc', '')):
        raise ValueError('nobody to send it to: To, Cc and Bcc are empty')
    missing = [f for f in files if not os.path.isfile(f)]
    if missing:
        raise ValueError('no such file to attach: ' + ', '.join(missing))
    msg = email.message.EmailMessage(policy=email.policy.SMTP)
    for k in HEADERS + ('In-Reply-To', 'References'):
        if headers.get(k):
            msg[k] = headers[k]
    domain = (addresses(headers.get('From', '')) or ['x@msgcanvas'])[0].split('@')[-1]
    msg['Date'] = email.utils.formatdate(localtime=True)
    msg['Message-ID'] = email.utils.make_msgid(domain=domain)
    msg.set_content(body.rstrip() + '\n')
    for f in files:
        kind = mimetypes.guess_type(f)[0] or 'application/octet-stream'
        main, sub = kind.split('/', 1)
        with open(f, 'rb') as data:
            msg.add_attachment(data.read(), maintype=main, subtype=sub, filename=os.path.basename(f))
    if headers.get('Forward') and os.path.isfile(headers['Forward']):
        with open(headers['Forward'], 'rb') as data:
            msg.add_attachment(email.message_from_binary_file(data, policy=email.policy.default),
                               filename='forwarded.eml')
    return msg


def send(path, account):
    """Send the draft from `account`, keep a copy in its Sent folder and drop the draft. What it
    was sent to."""
    msg = build(path)
    cmd = SEND or ['msmtp', '-C', accounts.MSMTPRC, '-a', account.key, '--read-envelope-from', '-t']
    done = subprocess.run(cmd, input=msg.as_bytes(), capture_output=True, timeout=120)
    if done.returncode:
        why = (done.stderr or done.stdout).decode(errors='replace').strip().split('\n')[-1]
        raise OSError(0, why or f'msmtp exited {done.returncode}')
    if 'Bcc' in msg:
        del msg['Bcc']          # msmtp leaves it out of what it sends; so does the copy
    file_sent(msg, account)
    os.remove(path)
    return ', '.join(addresses(', '.join(msg.get_all('To', []) + msg.get_all('Cc', []))))


def file_sent(msg, account):
    """A copy in the account's Sent folder (the first that looks like one), seen, for notmuch."""
    from .mail import folder_rank
    folders = [os.path.relpath(top, account.maildir) for top, dirs, _ in os.walk(account.maildir)
               if 'cur' in dirs]
    sent = [f for f in folders if folder_rank(f)[0] == 1]
    if not sent:
        return None
    name = f'{int(time.time())}.{os.getpid()}_{time.monotonic_ns()}.msgcanvas:2,S'
    target = os.path.join(account.maildir, sent[0], 'cur', name)
    with open(target, 'wb') as f:
        f.write(msg.as_bytes())
    subprocess.run(['notmuch', 'new', '--quiet'], capture_output=True)
    return target


def drafts(account):
    """The account's unsent drafts, newest first."""
    d = os.path.join(DRAFTS, account.key)
    try:
        return sorted((os.path.join(d, f) for f in os.listdir(d) if f.endswith('.eml')), reverse=True)
    except OSError:
        return []


def discard(path):
    if os.path.exists(path):
        os.remove(path)
