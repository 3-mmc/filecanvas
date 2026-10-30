"""A deterministic MCP client; the bundled upstream bridge owns session discovery."""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading

BRIDGE = Path(__file__).with_name('vendor') / 'mcp-bridge.cjs'
READ_TOOLS = frozenset(('listAccounts', 'listFolders', 'searchMessages', 'getMessage'))
MAX_REPLY = 64 * 1024 * 1024


class Client:
    def __init__(self, config=None, command=None, timeout=35):
        self.config = config or {}
        self.command = command
        self.timeout = timeout
        self.process = None
        self.lock = threading.Lock()
        self.sequence = 0
        self.closed = False

    def start(self):
        if self.closed:
            raise OSError('Thunderbird connection closed')
        command = self.command
        env = dict(os.environ)
        env.pop('THUNDERBIRD_MCP_NO_AUTOSTART', None)
        env.pop('THUNDERBIRD_MCP_DEBUG', None)
        if self.config.get('connection_file'):
            env['THUNDERBIRD_MCP_CONNECTION_FILE'] = self.config['connection_file']
        if command is None:
            node = self.config.get('node') or shutil.which('node')
            if not node:
                raise OSError('Install Node.js 18+; run twigs doctor thunderbird')
            bridge = str(BRIDGE)
            if str(node).lower().endswith('.exe') and os.name == 'posix':
                # Windows Node discovers Windows Thunderbird's session, including its temp directory.
                try:
                    got = subprocess.run(['wslpath', '-w', bridge], capture_output=True, text=True, timeout=5)
                except (OSError, subprocess.SubprocessError):
                    raise OSError('Cannot translate the bridge path for Windows Node') from None
                if got.returncode:
                    raise OSError('Cannot translate the bridge path for Windows Node')
                bridge = got.stdout.strip()
                shared = [v for v in env.get('WSLENV', '').split(':')
                          if v.split('/')[0] not in ('THUNDERBIRD_MCP_CONNECTION_FILE', 'THUNDERBIRD_MCP_DEBUG', 'THUNDERBIRD_MCP_NO_AUTOSTART')]
                # WSL only forwards explicitly shared variables to Windows programs.
                if self.config.get('connection_file'):
                    shared.append('THUNDERBIRD_MCP_CONNECTION_FILE/w')
                env['WSLENV'] = ':'.join(v for v in shared if v)
            command = [node, bridge]
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, env=env)
        except OSError:
            raise OSError('Cannot start the Thunderbird bridge. Check the configured Node executable.') from None
        if self.closed:
            self.close()
            raise OSError('Thunderbird connection closed')
        self.replies = queue.Queue()
        process, replies = self.process, self.replies
        def read():
            try:
                while True:
                    line = process.stdout.readline(MAX_REPLY + 1)
                    if not line or len(line) > MAX_REPLY:
                        break
                    replies.put(json.loads(line))
            except (OSError, ValueError):
                pass
            finally:
                replies.put(None)
        threading.Thread(target=read, daemon=True).start()
        result = self.rpc('initialize', dict(protocolVersion='2025-11-25', capabilities={},
                         clientInfo=dict(name='twigs', version='0.1.0')), timeout=10)
        if not isinstance(result, dict) or 'serverInfo' not in result:
            self.close()
            raise OSError('Invalid Thunderbird bridge initialization')
        self.write(dict(jsonrpc='2.0', method='notifications/initialized'))

    def write(self, message):
        try:
            self.process.stdin.write((json.dumps(message) + '\n').encode())
            self.process.stdin.flush()
        except (OSError, AttributeError):
            raise OSError('Thunderbird bridge disconnected') from None

    def rpc(self, method, params, timeout=None):
        self.sequence += 1
        ident = self.sequence
        self.write(dict(jsonrpc='2.0', id=ident, method=method, params=params))
        # One request at a time: notifications may be interleaved, response IDs must match.
        import time
        end = time.monotonic() + (timeout or self.timeout)
        while True:
            try:
                reply = self.replies.get(timeout=max(0, end-time.monotonic()))
            except queue.Empty:
                self.close()
                raise OSError('Thunderbird request timed out. Check Thunderbird before retrying.') from None
            if reply is None:
                self.close()
                raise OSError('Thunderbird bridge closed without a complete response')
            if not isinstance(reply, dict):
                self.close()
                raise OSError('Invalid Thunderbird response')
            if 'id' not in reply:
                continue
            if reply['id'] != ident:
                self.close()
                raise OSError('Unexpected Thunderbird response ID')
            if reply.get('error'):
                error = reply['error']
                raise OSError(str(error.get('message', 'Thunderbird request failed') if isinstance(error, dict) else error))
            return reply.get('result')

    def call(self, name, arguments=None):
        if name not in READ_TOOLS:
            raise ValueError('This Thunderbird adapter only supports browsing, searching and reading')
        with self.lock:
            if self.process is None:
                self.start()
            result = self.rpc('tools/call', dict(name=name, arguments=arguments or {}),
                              timeout=135 if name == 'getMessage' else self.timeout)
            if not isinstance(result, dict):
                raise OSError('Invalid Thunderbird tool response')
            blocks = result.get('content', [])
            if not isinstance(blocks, list):
                raise OSError('Invalid Thunderbird content blocks')
            texts = [b['text'] for b in blocks if isinstance(b, dict) and b.get('type') == 'text' and isinstance(b.get('text'), str)]
            if result.get('isError'):
                raise OSError('\n'.join(texts) or 'Thunderbird tool failed')
            data = result.get('structuredContent')
            if data is None:
                try:
                    data = json.loads('\n'.join(texts))
                except ValueError:
                    raise OSError('Thunderbird returned an unsupported result format') from None
            if isinstance(data, dict) and data.get('error'):
                raise OSError(str(data['error']))
            return data

    def close(self):
        self.closed = True
        process, self.process = self.process, None
        if process is not None:
            try:
                process.stdin.close()
                process.wait(timeout=0.3)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            finally:
                process.stdout.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
