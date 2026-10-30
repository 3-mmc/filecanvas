# Thunderbird mail

`twigs thunderbird` browses accounts, folders and messages from a running Thunderbird with
[Thunderbird MCP](https://github.com/TKasperczyk/thunderbird-mcp) installed. Twigs calls the
bridge directly; no agent, language model, AI account, or AI-client configuration is involved.
Thunderbird handles mail synchronization, provider authentication and token renewal.

This first version supports folder browsing, unread indicators, paginated message lists,
folder searches and plain-text message bodies with attachment metadata. It does not send,
move or delete mail, save attachments, or explicitly change read flags. The extension and
Thunderbird may fetch missing message content from the server. Existing Thunderbird filters
and background behavior still apply.

## Setup

1. Configure and sign in to your accounts in Thunderbird normally. Allow the initial sync to
   finish and open the folders you intend to use so their local indexes are initialized.
2. Install the upstream extension's `dist/thunderbird-mcp.xpi` through **Tools → Add-ons and
   Themes → gear menu → Install Add-on From File**, then restart Thunderbird. In its Options,
   check the server status and restrict account/tool access as desired. The add-on uses
   Experiment APIs; if your Release build disables it, use a supported Thunderbird ESR build.
3. Install Node.js 18 or newer. The upstream bridge is bundled with Twigs, with its MIT license;
   the extension is installed separately. No additional Python packages are needed.
4. Keep Thunderbird running and run:

```sh
twigs doctor thunderbird
twigs thunderbird accounts
twigs thunderbird connect
twigs thunderbird
```

The connected accounts also appear under **Emails** alongside manually configured accounts, with a small “via Thunderbird”
note in the status bar when selected.
Use **Ctrl+F** within a folder to search; submit an empty query to clear it. Searches use the
extension's header/preview matching, including `from:`, `subject:`, `to:` and `cc:` operators.
Use **Ctrl+R** to request current data from Thunderbird. A “More messages” entry fetches the next
page. Partial results and unconfirmed server refreshes are shown explicitly. Message bodies
are requested only when opened. The adapter does not poll continuously or launch Thunderbird.

`twigs thunderbird sync` verifies the connection and reports selected-account folder counts.
It does not independently synchronize every folder with the provider.

## Accounts already configured in Twigs

Setup compares every Thunderbird identity's email address with existing `msgcanvas` mail
accounts, ignoring address case. Matches are skipped by default;
your existing account configuration, authentication and mail storage remain in use.
`accounts` identifies overlaps before you connect. Local Folders is not selected automatically;
choose its account ID explicitly if you want it.

To select particular Thunderbird account IDs (shown by `accounts`):

```sh
twigs thunderbird connect --account account2 --account account4
```

To use Thunderbird as the source for an overlapping mailbox, avoiding a duplicate empty manual
view or a second OAuth setup:

```sh
twigs thunderbird connect --account account6 --prefer-thunderbird
```

This hides the matching manual mail entry from the Emails branch and excludes it from
`twigs msg sync`. The original accounts.toml, credentials and local mail are preserved.
A named manual sync points you to Thunderbird rather than attempting its separate login.
Other manual accounts keep their existing source. To restore both views, reconnect with
`--include-existing`. Each connect command replaces the selected Thunderbird account IDs;
repeat `--account` for every account you want to keep connected. No Thunderbird autostart is added.

To deliberately show an overlapping account through Thunderbird as well:

```sh
twigs thunderbird connect --account account2 --include-existing
```

This creates a second view of the mailbox; it does not migrate or disable the original source.
Without either source-selection option, overlap detection also runs when loading the canvas, so adding
an account to another Twigs mail source later hides its Thunderbird duplicate. Address matching
does not guess Gmail dot aliases or `+` aliases. Account identities should be accurate in
Thunderbird. The extension's account restrictions remain the access boundary; Twigs selection
only controls what appears in its interface.

## Windows Thunderbird with Twigs in WSL

Use **Windows Node** to run the bridge so it discovers Windows Thunderbird's session file.
The usual Linux `node` searches Linux session locations. Supply the WSL path to `node.exe`:

```sh
twigs thunderbird accounts --node '/mnt/c/Program Files/nodejs/node.exe'
twigs thunderbird connect --node '/mnt/c/Program Files/nodejs/node.exe'
```

Twigs translates the bundled bridge's path to a Windows/UNC path. WSL interoperability must
be enabled. The bridge connects to Thunderbird on Windows localhost, so there is no need to
expose the server on the network. Do not enable “Listen on all interfaces” for this setup.
A portable Windows Node executable works too. Keep its path stable after connecting.

## Session and storage

Twigs stores account IDs, the Node executable (if overridden), an optional connection-file
path and the overlap preference in `~/.config/twigs/thunderbird.json`, under `$XDG_CONFIG_HOME`
when set. It does not store provider passwords, OAuth refresh tokens, the extension's bearer
token, or a mail-content cache. Fetched mail is held in memory. Thunderbird retains its usual
profile, mail cache and credentials.

The extension may withhold encrypted content, or content whose encryption status it cannot
establish, under its default privacy settings. Twigs displays that notice. Open the message
in Thunderbird to investigate; Twigs does not change the extension’s privacy settings.

The unmodified upstream bridge discovers its local port and session token, including native,
Snap, Flatpak and macOS session locations, and repeats discovery after a stale connection.
For nonstandard locations, pass `--connection-file PATH` to `accounts` or `connect`. That path
must be in the filesystem understood by the selected Node runtime; for Windows Node, use a
Windows path. Do not paste the connection file's contents into configuration or bug reports.
Closing Twigs closes only its bridge subprocess; it leaves Thunderbird running.

## Optional headless operation and CPU troubleshooting

First complete account setup and extension installation in the visible application. Quit
Thunderbird before starting the same profile headlessly:

```sh
thunderbird --headless -P 'your-profile-name'
```

On Windows, use PowerShell, adjusting the install location:

```powershell
& 'D:\Program Files\Thunderbird\thunderbird.exe' --headless -P 'your-profile-name'
```

Stop the headless instance normally before reopening that profile visibly. Twigs does not
start, stop or change settings in your Thunderbird profile. Login expiry or another prompt
may require returning to the visible application. Running headlessly still runs the full
Thunderbird engine.

[Mozilla bug 1958931](https://bugzilla.mozilla.org/show_bug.cgi?id=1958931) reports high CPU
usage from donation/new-release tabs in headless mode. If CPU remains high after startup and
sync settle, return to visible Thunderbird, close those tabs and inspect other pending prompts
before retrying. Use Task Manager on Windows, Activity Monitor on macOS, or `top` on Linux.
Keep a visible/minimized session as a fallback. Do not disable updates as a workaround.

An isolated Linux Thunderbird **153.6.0esr** profile with extension **0.10.0** was checked for
headless extension startup, account/folder access, search and message-body rendering.
The disposable fixture profile explicitly enabled content access for its decoding test; the
default privacy-withheld response was also checked. Real profile settings were not changed. A short idle sample showed 0% main-process
CPU; this is not a long-term or cross-platform performance guarantee. Windows with your real
accounts requires a separate live check after extension installation.
