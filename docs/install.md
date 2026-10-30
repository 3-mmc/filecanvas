# Installing twigs

The Python distribution is **twigs-cli**; the command and Python namespace are **twigs**.
PyPI's `twigs` and `tuido` names belong to unrelated projects. The initial package metadata is
0.1.0; this checkout has not yet been published to PyPI.

From a checkout, with uv:

```sh
uv tool install .
twigs doctor
```

Or with pipx: `pipx install .`. Once a release has been published, use `uv tool install twigs-cli`
or `pipx install twigs-cli`. Both create isolated environments. The wheel provides `twigs`,
`msgcanvas`, and `calcanvas` console commands; it does not require a source checkout or symlinks.
`twigs install files tasks` selects enabled twigs; it does not install system dependencies.
Source checkouts can still use `./twigs-launcher install` for the original symlink workflow.

Files, calendars, and tasks have no third-party Python dependencies. The TuiDo model is bundled;
the standalone TuiDo app and Textual are unnecessary. `twigs` opens a new task desk on first run.
See [the model provenance and shared database](tasks-model.md).

## Diagnose optional features

```sh
twigs doctor
twigs doctor mail
twigs doctor chat
twigs doctor oauth
twigs doctor thunderbird
twigs doctor --platform macos
twigs doctor --platform arch --json
```

Doctor is read-only: it checks commands, imports the mail binding, tests an in-memory SQLite
snapshot, checks whether an installed nchat advertises `--serve`, and checks Node's version for
optional browser mail. It does not inspect browser sessions, log in, print
credentials, install packages, or open your task database. Plain `doctor` reports optional
missing features without failing; selecting a feature gives a nonzero exit status if that
feature or the base runtime is unavailable. `--platform` changes guidance, not the runtime checks.

## Mail dependencies

Ubuntu / Debian:

```sh
sudo apt install isync notmuch python3-notmuch2 msmtp pass gnupg
```

Arch Linux:

```sh
sudo pacman -S isync notmuch python-cffi msmtp pass gnupg
```

Arch's `notmuch` includes the CFFI `notmuch2` binding. macOS with Homebrew:

```sh
brew install isync notmuch msmtp pass gnupg
```

The **Python running twigs** must be able to import `notmuch2`. A system package installed by
apt/pacman/brew will not automatically appear in an isolated uv/pipx environment. The supported
initial mail installation is a venv using the package manager's Python with system packages:

```sh
# Ubuntu/Debian/Arch; install python3-venv on Ubuntu/Debian if Python requests it.
/usr/bin/python3 -m venv --system-site-packages ~/.local/share/twigs-venv
~/.local/share/twigs-venv/bin/python -m pip install .
~/.local/share/twigs-venv/bin/twigs doctor mail
```

On macOS use the Homebrew Python version matching the installed Notmuch binding instead of
`/usr/bin/python3`. Keep its venv with `--system-site-packages`. Do not copy a compiled binding
between Python versions. The PyPI `notmuch2` package is a separate distribution of the binding;
it is not a mandatory twigs dependency or a tested replacement for every system Notmuch version.

Mail uses `pass`/GPG for passwords. OAuth also needs NeoMutt's `mutt_oauth2.py` helper and an
XOAUTH2 SASL plugin for mbsync. Ubuntu/Debian provide `neomutt` and
`libsasl2-modules-kdexoauth2`; Arch provides `neomutt` (verify your SASL plugins separately);
Homebrew provides `neomutt`. If the helper is in a different location, set
`TWIGS_OAUTH_HELPER=/absolute/path/to/mutt_oauth2.py` before setup/login. TLS uses Python's
system CA path, with `SSL_CERT_FILE` as an override.

## Chats and images

Chats work independently of mail: they do not require notmuch, mbsync, msmtp, or pass.
They need [the twigs nchat fork](https://github.com/3-mmc/nchat) with `--serve` and the desired
protocols compiled in. A distribution nchat package may lack these features. See
[nchat's serve interface](nchat-serve.md). Doctor checks `--serve` but does not log in or
claim that every protocol was compiled into that binary.

`twigs msg chat-setup` starts nchat setup. For Instagram, run
`twigs msg chat-setup instagram --help` for browser-cookie instructions.

`chafa` is optional for image previews: install it with apt, pacman, or brew.
Linux and macOS are supported; use WSL on Windows.

## Thunderbird accounts

The optional `thunderbird` twig connects to a running Thunderbird with the Thunderbird MCP
extension installed. It bundles the Node bridge, requires Node.js 18+, and needs no additional
Python packages or separate provider login. Use `twigs doctor thunderbird` and follow
[Thunderbird setup](thunderbird.md), including overlap detection, Windows/WSL setup and the
headless CPU troubleshooting note.

## Building and checking a release

```sh
uv build
uv run --extra test pytest
```

Test the resulting wheel outside the source directory in a clean environment. A successful
build is not a PyPI publication. Before publishing the first release, choose the project's
license, configure PyPI Trusted Publishing, and run the platform CI checks.

Package references: [Debian binding](https://packages.debian.org/trixie/python3-notmuch2),
[Arch binding files](https://archlinux.org/packages/extra/x86_64/notmuch/files/),
[Homebrew Notmuch](https://formulae.brew.sh/formula/notmuch),
[uv tool isolation](https://docs.astral.sh/uv/concepts/tools/).
