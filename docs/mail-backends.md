# Adding another mail backend

Twigs presents mail through its canvas `Provider` interface. Thunderbird MCP is one
adapter, alongside the native notmuch mail provider. Another adapter, such as Apple
Mail, can use a different API or transport and still contribute an **Emails** section.
Apple Mail support is not implemented yet.

## Existing extension points

An external Python package registers a `twigs.apps` entry point. Its value names a
module exposing an `APP` dictionary, as described in `twigs/kit/apps.py`. The dictionary
names a CLI function and a `sections()` function returning `(title, Provider)` pairs.
The canvas consumes `Node` objects and the methods in `twigs/core.py:Provider`; it does
not need to know whether mail arrived through MCP, a local database, or another API.

Keep three concerns separate when adding an adapter:

- **Transport and data access.** Account and folder discovery, message paging,
  searches, and lazy body reads belong to the adapter. Thunderbird's deterministic
  MCP client is in `apps/thunderbird/connection.py`. It permits only the four read
  tools that this adapter uses; a different provider need not implement those MCP
  tool names or depend on Node.js.
- **Presentation.** Reuse the backend-independent display helpers in
  `apps/msg/common.py` for names, dates, folder ordering, and body limits. The
  Thunderbird canvas currently translates its own response dictionaries into
  `Node` objects. Those dictionaries are not a universal mail schema. If a second
  adapter needs the same rendering, extract the shared rendering around normalized
  mail records then, preserving opaque, source-scoped account/folder/message IDs.
- **Application lifecycle.** Installation, profile selection, process inspection,
  GUI/headless switching, and login startup remain specific to the host app.
  `apps/thunderbird/host.py` contains that policy for Thunderbird only. A future
  Apple Mail provider should expose whichever lifecycle operations it actually
  supports, through its own CLI/actions; it need not emulate Thunderbird profiles,
  extensions, or headless operation.

Keep account selection and overlap preferences explicit. Thunderbird's overlap
routing is currently implemented in its settings module; another provider will
need to participate in common source routing before both can be selected for the
same address. Do not silently substitute a new provider for existing accounts.
Read-only browsing must not implicitly authorize sending, deleting, saving
attachments, or changing flags. Add supported actions deliberately and keep missing
capabilities discoverable without offering actions that cannot run.

## Compatibility checks

Use fake transports for pagination, errors, stale responses, unread/thread grouping,
and message rendering. Use synthetic profiles/processes and temporary login entries
for host tests. Exercise the real launcher/parser where available. Keep live checks
separate: OS permissions, account access, extension installation, login startup, and
the GUI/headless round trip need validation on each supported host application and OS.
