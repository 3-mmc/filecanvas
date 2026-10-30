# msgcanvas

Mail (and, next, chat) on one canvas you move across: accounts branch into folders, folders into
threads (newest first), threads into who replied to whom, and the message you're on is open in a
reading pane beside them. Drawn by [filecanvas](https://github.com/3-mmc/filecanvas) through its
providers, vendored here (`tools/vendor-filecanvas.py`).

Mail is synced by mbsync into `~/Mail/<account>/`, indexed by notmuch and sent by msmtp. For now it
only reads: mbsync fetches (`Sync Pull`) and nothing changes on the server.

```
sudo apt install isync notmuch python3-notmuch2 msmtp pass libsasl2-modules-kdexoauth2 neomutt
python3 -m msgcanvas setup          # starts ~/.config/msgcanvas/accounts.toml: fill it in
python3 -m msgcanvas setup          # again: makes the mbsync, msmtp and notmuch configs from it
python3 -m msgcanvas login NAME     # each account: a password into pass, or an OAuth sign-in
python3 -m msgcanvas sync           # fetch and index
python3 -m msgcanvas [NAME[/FOLDER]]
```

The configs it makes live in `~/.config/msgcanvas/` and are handed to each tool, so your own
`~/.mbsyncrc`, `~/.msmtprc` and `~/.notmuch-config` are never touched.

## Accounts

| type | signs in with | |
| --- | --- | --- |
| `gmail` | an app password (2-step verification on, then myaccount.google.com/apppasswords) | All Mail and Important aren't fetched: they repeat the other folders |
| `outlook` | OAuth (Microsoft 365, device code: open a link, type a code) | uses Thunderbird's public client id; a tenant that blocks it needs DavMail instead |
| `seznam` | your password | turn on access from other apps in Seznam's settings if it's refused |
| `imap` | a password | give `imap_host` and `smtp_host` |

`max_messages` (2000 by default) is how many of each folder's newest messages are kept here; the
server keeps everything. `0` keeps it all.

Passwords are `pass` entries (`mail/<name>`); OAuth tokens are gpg-encrypted files under
`~/.local/share/msgcanvas/tokens/`, kept fresh by neomutt's `mutt_oauth2.py`.

## Keys

The canvas's own: ↑↓ messages, → open a folder or thread, ← back, type to filter, PgUp/PgDn to
scroll the pane, Esc to leave.

## Tests

`python3 -m unittest discover tests` builds a small maildir, indexes it with notmuch, and drives
the canvas over it.
