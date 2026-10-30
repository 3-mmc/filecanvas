# msgcanvas

Mail and chat on one canvas you move across: accounts branch into folders, folders into
threads (newest first), threads into who replied to whom, and the message you're on is open in a
reading pane beside them. Drawn by [filecanvas](https://github.com/3-mmc/filecanvas) through its
providers, vendored here (`tools/vendor-filecanvas.py`).

Mail is synced by mbsync into `~/Mail/<account>/`, indexed by notmuch and sent by msmtp. For now it
only reads: mbsync fetches (`Sync Pull`) and nothing changes on the server.

```
sudo apt install isync notmuch python3-notmuch2 msmtp pass libsasl2-modules-kdexoauth2 neomutt
ln -s "$PWD/msgcanvas-launcher" ~/.local/bin/msgcanvas    # once: `msgcanvas` anywhere
msgcanvas setup          # starts ~/.config/msgcanvas/accounts.toml: fill it in,
msgcanvas import-thunderbird   # or add the accounts Thunderbird has (not its passwords)
msgcanvas setup          # again: makes the mbsync, msmtp and notmuch configs from it
msgcanvas login NAME     # each account: a password into pass, or an OAuth sign-in
msgcanvas sync           # fetch and index
msgcanvas [NAME[/FOLDER]]
```

The configs it makes live in `~/.config/msgcanvas/` and are handed to each tool, so your own
`~/.mbsyncrc`, `~/.msmtprc` and `~/.notmuch-config` are never touched.

## Accounts

| type | signs in with | |
| --- | --- | --- |
| `gmail` | an app password (2-step verification on, then myaccount.google.com/apppasswords), or with `auth = "google"` OAuth through your own Google Cloud "Desktop app" client | All Mail and Important aren't fetched: they repeat the other folders |
| `outlook` | OAuth (Microsoft 365, device code: open a link, type a code) | uses Thunderbird's public client id; a tenant that blocks it needs DavMail instead |
| `seznam` | your password | turn on access from other apps in Seznam's settings if it's refused |
| `yahoo` | an app password (login.yahoo.com/account/security) | |
| `imap` | a password | give `imap_host` and `smtp_host` |

`max_messages` (2000 by default) is how many of each folder's newest messages are kept here; the
server keeps everything. `0` keeps it all.

Passwords are `pass` entries (`mail/<name>`); OAuth tokens are gpg-encrypted files under
`~/.local/share/msgcanvas/tokens/`, kept fresh by neomutt's `mutt_oauth2.py`.

## Chats

Chats come from [nchat](https://github.com/3-mmc/nchat) (the `feature/serve` branch until it's
merged), which msgcanvas runs as `nchat --serve`: profiles (WhatsApp, Signal, Instagram, …) sit
beside the mail accounts, each opening into its chats (pinned first, then the latest), and the
pane shows a chat as a conversation, newest at the bottom, following new messages as they come.
Nothing is sent or marked read yet.

```
git clone -b feature/serve https://github.com/3-mmc/nchat && cd nchat && mkdir build && cd build
cmake .. -DHAS_TELEGRAM=OFF -DHAS_SIGNAL=ON -DHAS_INSTAGRAM=ON && make -j    # WhatsApp is on by default
ln -s "$PWD/bin/nchat" ~/.local/bin/nchat
msgcanvas chat-setup     # each account: nchat's own setup, a QR code or a code; then quit nchat
```

A profile nchat is open on is locked: msgcanvas shows it greyed until nchat is closed. Its
`[chat]` settings in accounts.toml can name another `nchat`, its `confdir`, or turn chats off
(`enabled = false`).

## Keys

The canvas's own: ↑↓ messages, → open a folder or thread, ← back, type to filter, PgUp/PgDn to
scroll the pane, Esc to leave.

## Tests

`python3 -m unittest discover tests` builds a small maildir, indexes it with notmuch, and drives
the canvas over it; the chat tests run the real `nchat --serve` on a Dummy profile (skipped
without nchat, `NCHAT=path` for another build).
