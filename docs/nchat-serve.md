# Task: a headless `nchat --serve` mode

You're working in **github.com/3-mmc/nchat**, the maintainer's fork of
[d99kris/nchat](https://github.com/d99kris/nchat) (Telegram, WhatsApp, Signal, Instagram
in the terminal). Add a mode that runs nchat's protocols **without its curses UI** and speaks a
small JSON protocol over stdin/stdout, so another program can be the UI.

That program is **msgcanvas** (github.com/3-mmc/msgcanvas, Python). It draws mail and chats on a
canvas: profiles branch into chats, chats into messages. Its chat provider will start
`nchat --serve` as a child process and talk to it as described below. You don't need to write any
Python. Your job is the nchat side, plus a written description of the protocol precise enough to
build against.

## Where things are

- `lib/common/src/protocol.h`: the whole boundary between nchat's UI and its protocols. A
  `Protocol` takes `RequestMessage`s (`SendRequest`) and calls one handler with
  `ServiceMessage`s (`SetMessageHandler`). Every request and notification type is listed in
  `enum MessageType`. Chats, messages and contacts are `ChatInfo`, `ChatMessage` and
  `ContactInfo`.
- `src/main.cpp`: argument parsing (`-d/--confdir`, `-s/--setup`, …), loading every profile
  under `<confdir>/profiles/<Protocol>_<id>` (each behind a `ScopedDirLock`), building
  the `Ui`, wiring `Ui::MessageHandler` to every protocol and to `MessageCache`, then `Login()`.
  The serve mode is this same start-up with the `Ui` swapped for a JSON server.
- `lib/ncutil/src/messagecache.*`: nchat's sqlite cache of chats and messages. The UI reads
  history from it. Reuse it rather than asking the network again.
- `lib/duchat`: the **Dummy** protocol (`HAS_DUMMY`, on by default). It needs no account, so it's
  what the tests use. A profile is just a directory, `profiles/Dummy_<anything>`.
- The fork is 6 commits ahead of and 4 behind upstream. Don't merge upstream as part of this
  task. If something upstream would be needed, say so.

## Rules

- **Nothing changes for people using nchat normally.** Without `--serve`, behaviour, output and
  config are exactly as before. Don't refactor the UI to make room. Add alongside it.
- **The protocol libraries aren't touched**, unless a bug blocks you, and then only as a separate,
  explained commit.
- **Follow the code around you:** its style, naming, logging (`LOG_*`), and how it's built
  (CMake options, the existing `make.sh`). The serve mode builds by default, with no new
  dependencies. If you need JSON, check what's already vendored before adding anything; a
  small hand-written encoder and decoder is fine.
- Work on a branch, `feature/serve`, off the fork's `master`. Push it, and don't merge it into
  `master`: the maintainer does that.

## The mode

`nchat --serve [-d CONFDIR]`

- Loads the same profiles, with the same locks, as the normal start-up. A profile that's locked
  (nchat already open on it) is skipped and reported in `hello` (see below). Never steal a lock.
- Never touches the terminal: no curses, no raw mode, nothing printed to stdout except
  protocol lines. Logs go to nchat's log file as usual. stderr is only for a fatal error before
  the protocol starts.
- Doesn't set profiles up. QR codes and phone codes stay with `nchat --setup` in a terminal.
  If a protocol asks for the UI's control at run time (`ProtocolUiControlNotify`, a
  re-login), send the `needs_setup` event and carry on with the other profiles.
- Ends cleanly on EOF on stdin, on a `quit` request, or on SIGTERM/SIGINT: it logs out, closes
  profiles and releases locks as the normal exit does. Exit status 0.

## The protocol

UTF-8 JSON, **one object per line**, both ways. Lines from different threads must never
interleave, so put one mutex around writing to stdout. Protocol threads call the message handler,
so reading stdin and writing stdout are both yours to make safe.

**Requests** (on stdin) carry an `id` chosen by the client. Every request gets exactly one
**reply** with the same `id`, sent as soon as the request has been handed to the protocol:

```json
{"id": 7, "op": "get_messages", "profile": "Telegram_+4912345", "chat": "-100123", "limit": 50}
{"id": 7, "ok": true}
{"id": 8, "ok": false, "error": "no profile Telegram_+49999"}
```

What comes back from the network then arrives as **events**, which carry `event`, not `id`. That
mirrors nchat: requests are fire-and-forget, notifications come later and on their own.

```json
{"event": "messages", "profile": "Telegram_+4912345", "chat": "-100123", "from": "", "messages": [ … ]}
```

### Starting

The first line nchat writes, once profiles are loaded and before any `Login()` has finished:

```json
{"event": "hello", "version": 1, "nchat": "<nchat version>",
 "profiles": [{"profile": "Telegram_+4912345", "protocol": "Telegram", "name": "<display name>",
               "self": "<self id>", "features": ["pin_chat", "archive_chat", …]}],
 "skipped": [{"profile": "WhatsAppMd_+4912345", "why": "locked"}]}
```

`features` are the `ProtocolFeature` flags the profile has (`HasFeature`), in snake_case
without the `Feature` prefix.

### Requests (`op`)

Each maps to the `RequestMessage` named. Names and fields are snake_case versions of nchat's own.

| op | fields | nchat request |
| --- | --- | --- |
| `get_chats` | `profile` | `GetChatsRequest` |
| `get_contacts` | `profile` | `GetContactsRequest` |
| `get_messages` | `profile`, `chat`, `from` (message id, `""` = newest), `limit` | `GetMessagesRequest` (answer from `MessageCache` where the UI does) |
| `get_message` | `profile`, `chat`, `message` | `GetMessageRequest` |
| `set_current_chat` | `profile`, `chat` (or `""` for none) | `SetCurrentChatRequest`: some protocols only send read receipts for the current chat |
| `send_message` | `profile`, `chat`, `text`, optional `quoted` (message id), optional `file` (a path) | `SendMessageRequest`, with `clientRequestId` = the request's `id` |
| `edit_message` | `profile`, `chat`, `message`, `text` | `EditMessageRequest` |
| `delete_message` | `profile`, `chat`, `message` | `DeleteMessageRequest` |
| `mark_read` | `profile`, `chat`, `message` | `MarkMessageReadRequest` |
| `send_typing` | `profile`, `chat`, `typing` (bool) | `SendTypingRequest` |
| `send_reaction` | `profile`, `chat`, `message`, `emoji` | `SendReactionRequest` |
| `download_file` | `profile`, `chat`, `message` | `DownloadFileRequest` |
| `get_members` | `profile`, `chat` | `GetGroupMembersRequest` |
| `pin_chat` / `archive_chat` | `profile`, `chat`, `on` (bool) | `PinChatRequest` / `ArchiveChatRequest` |
| `quit` | | ends the mode |

An unknown `op`, a missing field or an unknown profile gets `"ok": false` with an `error` saying
which. Never crash on bad input.

### Events

One per `ServiceMessage` type, carrying `profile`. Lists are always lists, even of one.

| event | from | fields |
| --- | --- | --- |
| `connected` | `ConnectNotify` | `ok` |
| `chats` | `NewChatsNotify` | `chats`: [chat] |
| `contacts` | `NewContactsNotify` | `contacts`: [contact] |
| `messages` | `NewMessagesNotify` | `chat`, `from`, `messages`: [message] |
| `sent` | `SendMessageNotify` | `chat`, `ok`, `request` (the `send_message` request's `id`, where the profile has `send_acknowledgement`), `message` when there is one |
| `message_status` | `NewMessageStatusNotify` | `chat`, `message`, `read` |
| `message_file` | `NewMessageFileNotify` | `chat`, `message`, `file` (path, status) |
| `deleted` | `DeleteMessageNotify` / `DeleteChatNotify` | `chat`, `message` (absent for a chat) |
| `typing` | `ReceiveTypingNotify` | `chat`, `user`, `typing` |
| `status` | `ReceiveStatusNotify` | `user`, `online`, `last_seen` |
| `reactions` | `NewMessageReactionsNotify` | `chat`, `message`, `reactions` |
| `pinned`, `muted`, `archived` | `NewMessageIsPinnedNotify`, `UpdatePinNotify`, `UpdateMuteNotify`, `UpdateArchivedNotify` | `chat`, (`message`), `on` |
| `members` | `NewGroupMembersNotify` | `chat`, `members` |
| `needs_setup` | `ProtocolUiControlNotify` | |
| `exit` | `RequestAppExitNotify` | |

Anything else is sent as `{"event": "other", "profile": …, "type": <MessageType number>}`, so
nothing is ever silently dropped.

**Objects** use nchat's fields in snake_case, with nchat's units (times in **milliseconds since
the epoch**, as `timeSent` already is):

- chat: `id`, `name` (as the UI shows it), `unread`, `unread_mention`, `muted`, `pinned`,
  `archived`, `last_message_time`
- message: `id`, `sender`, `text`, `time`, `outgoing`, `read`, `edited`, `deleted`, `pinned`,
  `quoted` {`id`, `text`, `sender`} or null, `file` (from `fileInfo`: path and status, decoded,
  or null), `reactions`, `mentions`
- contact: `id`, `name`, `phone`, `self`

Files never travel as bytes, only as paths that nchat has downloaded.

## Tests

Use nchat's own test setup (`tests/`, `HAS_TESTS`) if it fits. Otherwise a small script beside it
(it has `run_ui_regression.py`). Against a temporary `--confdir` holding one
`profiles/Dummy_1` profile:

1. `hello` comes first and names the dummy profile, with its features.
2. `get_chats` gets `ok`, then a `chats` event. `get_messages` on one of those chats gets
   `messages`.
3. `send_message` gets `ok`, then `sent` (with `request` matching, if the dummy profile has
   `send_acknowledgement`).
4. Bad input (not JSON, an unknown op, an unknown profile, a missing field) gets an error reply,
   and the next good request still works.
5. Closing stdin ends the process with status 0, and the profile's lock is released: a second
   `--serve` on the same confdir starts.
6. A profile locked by another process shows up in `skipped`.

Make sure the normal build and nchat's existing tests still pass.

## When you're done

- Branch `feature/serve` pushed, in commits that each say what changed for a person using nchat.
- **`doc/serve.md`** in the nchat repo: the protocol as built. This file was the plan; that one is
  the truth, including anything you had to change and why.
- A line in nchat's README and `--help` for `--serve`.
- A short report back: what works, what the protocols didn't let you do (for example a
  notification the dummy profile never sends), and anything you were unsure of, so the
  maintainer can decide.

Out of scope: setting profiles up, multiple clients at once, sockets or networking (stdio
only), and anything on the msgcanvas side.
