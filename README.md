# msgcanvas

Mail and chat on one canvas you move across: accounts branch into folders, folders into
threads (newest first), threads into who replied to whom, and the message you're on is open in a
reading pane beside them. Drawn by [filecanvas](https://github.com/3-mmc/filecanvas) through its
providers, vendored here (`tools/vendor-filecanvas.py`).

Mail is synced by mbsync into `~/Mail/<account>/`, indexed by notmuch and sent by msmtp. Syncing
only fetches (`Sync Pull`): reading, flags and deleting don't reach the server yet.

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
Nothing is marked read yet.

```
git clone -b feature/serve https://github.com/3-mmc/nchat && cd nchat && mkdir build && cd build
cmake .. -DHAS_TELEGRAM=OFF -DHAS_SIGNAL=ON -DHAS_INSTAGRAM=ON && make -j    # WhatsApp is on by default
ln -s "$PWD/bin/nchat" ~/.local/bin/nchat
msgcanvas chat-setup     # each account: nchat's own setup, a QR code or a code; then quit nchat
```

A profile nchat is open on is locked: msgcanvas shows it greyed until nchat is closed. Its
`[chat]` settings in accounts.toml can name another `nchat`, its `confdir`, or turn chats off
(`enabled = false`).

## Writing

| key | on | does |
| --- | --- | --- |
| ⌃N | mail anywhere / a chat | a new mail, in your `$VISUAL` / `$EDITOR` (nano otherwise) / a message, on the line at the bottom |
| ⌃R | a mail / a chat message | reply / reply quoting it |
| ⌃A | a mail / a chat | reply to everyone / send a file, picked on the file canvas |
| ⌃F | a mail | forward it, itself attached |
| ⌃O ⌃S | something attached | open it (Windows' app under WSL) / save it to Downloads; a chat's file is fetched first |
| ⌃E ⌃D | an unsent draft | go on writing it / throw it away |

A mail draft has its headers on top (`To:`, `Cc:`, `Bcc:`, `Subject:`, and an `Attach: path` line
per file) and the body under a blank line. When the editor closes: **y** send, **e** edit, **a**
attach a file, **d** keep it for later (the account's *Unsent* folder), **q** throw it away. A sent
mail is filed in the account's Sent folder at once.

## Pictures

Images attached to a mail, and a chat's photos (fetched when you look at one), are drawn in the
pane by [chafa](https://hpjansson.org/chafa/) (`apt install chafa`) as coloured blocks: they scroll
with the text, in any terminal and through tmux. ⌃O opens one full size.

## Keys

The canvas's own: ↑↓ messages, → open a folder or thread, ← back, type to filter, PgUp/PgDn to
scroll the pane, Esc to leave.

## Tests

`python3 -m unittest` (from the repo) builds a small maildir, indexes it with notmuch, and drives
the canvas over it; the chat tests run the real `nchat --serve` on a Dummy profile (skipped
without nchat, `NCHAT=path` for another build).
