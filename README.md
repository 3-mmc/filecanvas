# msgcanvas

Mail and chat on one canvas you move across: accounts branch into folders (their unread counts
in a column of their own), folders into groups (by date: Today, Yesterday, This week, Last week,
Earlier this month, each month, each year; or by sender; or none: **⌃L** switches, and it's
remembered), groups into threads, threads into who replied to whom. The message you're on opens
onto what it says, on the canvas: its subject, who it's from and to (each in their colour, their
address beside), when, its body a paragraph at a time (a long one's start, then **↓ more**), and
what's attached. Pictures show in a pane beside.
Drawn by [treecanvas](https://github.com/3-mmc/treecanvas) through its
providers, vendored here (`tools/vendor-treecanvas.py`).

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

An account's `name` is what's shown, anything (its address, say). Its `key` is what its mail
folder (`~/Mail/<key>`), password (`pass`: `mail/<key>`) and token are filed under; it's made from
the name if you leave it out, so give one before renaming an account that already has mail.

`max_messages` (2000 by default) is how many of each folder's newest messages are kept here; the
server keeps everything. `0` keeps it all.

Passwords are `pass` entries (`mail/<name>`); OAuth tokens are gpg-encrypted files under
`~/.local/share/msgcanvas/tokens/`, kept fresh by neomutt's `mutt_oauth2.py`.

## Chats

Chats come from [nchat](https://github.com/3-mmc/nchat) (the `feature/serve` branch until it's
merged), which msgcanvas runs as `nchat --serve`: profiles (WhatsApp, Signal, Instagram, …) sit
beside the mail accounts, each opening into its chats (pinned first, then the latest). A chat
opens on the canvas itself into its latest 20 messages, oldest at the top: each person's messages
in a row hang off their name (a chip in their own colour, the same in every chat, and never two
alike in one), each whole, ending in its time and, if
it's yours, ✓ (there) or ✓✓ (read). You land on the newest and stay on it as more come; **↑
earlier** (Enter) shows 20 more. A chat you've opened wears its colour too (the other person's,
or the group's), so open chats stand apart. A photo shows its picture in the pane beside.

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

## What you see happen

- **Sending** spins in the bar until it's gone (✓) or hasn't (✗).
- **A message arriving** where you aren't makes its chat pulse, says who in the bar and shows a
  desktop notification (Windows' under WSL), unless the chat is muted; new **mail** in an inbox
  does the same for its account and folder, once for all that came together.
- **Read receipts**: going into a chat (→ onto its messages, not passing it in the list) marks
  what's there read and tells the sender. **Typing** in a chat's line tells them you're typing;
  someone typing to you spins in the bar.
- New lines in a conversation resolve out of noise; a draft thrown away goes to dust; the opening
  screen assembles MSGCANVAS while your accounts load. `CDECK_CALM=1` holds all of it still.

In accounts.toml, `[chat]` takes `read_receipts`, `typing` and `notify` (each `false` to stop it),
and `[defaults]` takes `notify` (mail's) and `intro`.

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
