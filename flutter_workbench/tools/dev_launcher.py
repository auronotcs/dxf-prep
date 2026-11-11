"""Own flutter run --machine and expose only authenticated, local hot reload.

No hot-restart fallback: rejected patches must leave the current drawing alive.
Protocol: .flutter-sdk/packages/flutter_tools/doc/daemon.md.
"""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FlutterSession:
    def __init__(self, process):
        self.process = process
        self.app_id = None
        self.ready = False
        self.sequence = 0
        self.pending = {}
        self.lock = threading.Lock()
        self.reload_lock = threading.Lock()

    def receive(self, packet):
        event = packet.get('event')
        params = packet.get('params', {})
        if event == 'app.start':
            self.app_id = params['appId']
        elif event == 'app.started':
            self.ready = True
        elif event == 'app.stop':
            self.ready = False
        with self.lock:
            pending = self.pending.get(packet.get('id'))
            if pending is not None:
                pending[1].update(packet)
                pending[0].set()

    def read_output(self):
        try:
            for line in self.process.stdout:
                print(line.rstrip(), flush=True)
                try:
                    packets = json.loads(line)
                    if isinstance(packets, list):
                        for packet in packets:
                            if isinstance(packet, dict):
                                self.receive(packet)
                except (ValueError, KeyError):
                    pass
        finally:
            self.ready = False
            with self.lock:
                for event, result in self.pending.values():
                    result['error'] = 'Flutter development session ended.'
                    event.set()

    def reload(self, timeout=120):
        if not self.reload_lock.acquire(blocking=False):
            return 409, {'ok': False, 'message': 'A UI reload is already running.'}
        request_id = None
        try:
            if not self.ready or self.process.poll() is not None:
                return 503, {'ok': False, 'message': 'Flutter is not ready. See build/development.log.'}
            event, result = threading.Event(), {}
            with self.lock:
                self.sequence += 1
                request_id = self.sequence
                self.pending[request_id] = (event, result)
            command = {'id': request_id, 'method': 'app.restart', 'params': {
                'appId': self.app_id, 'fullRestart': False, 'pause': False,
                'reason': 'Reload UI button',
            }}
            self.process.stdin.write(json.dumps([command]) + '\n')
            self.process.stdin.flush()
            if not event.wait(timeout):
                return 504, {'ok': False, 'message': 'Reload timed out; check development.log before retrying. The app was not restarted.'}
            response = result.get('result') or {}
            if 'error' in result or response.get('code') != 0:
                return 422, {'ok': False, 'message': str(result.get('error') or response.get('message') or 'Flutter rejected the reload. See development.log.')}
            return 200, {'ok': True, 'message': 'UI reloaded. Drawing and session retained.'}
        except (OSError, ValueError) as error:
            return 503, {'ok': False, 'message': f'Reload connection failed: {error}'}
        finally:
            with self.lock:
                self.pending.pop(request_id, None)
            self.reload_lock.release()


def make_server(token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            if self.path != '/reload':
                status, body = 404, {'ok': False, 'message': 'Unknown action.'}
            elif not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                status, body = 403, {'ok': False, 'message': 'Invalid development session.'}
            else:
                status, body = self.server.session.reload()
            data = json.dumps(body).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except OSError:
                pass  # The app may have closed while the compiler was working.

    return ThreadingHTTPServer(('127.0.0.1', 0), Handler)


def main():
    root = Path(__file__).resolve().parents[1]
    build = root / 'build'
    build.mkdir(exist_ok=True)
    # Keep a Windows file lock for the whole session, including initial build.
    import msvcrt
    lock_file = (build / 'development.lock').open('a+b')
    lock_file.seek(0)
    if not lock_file.read(1):
        lock_file.write(b'0')
        lock_file.flush()
    lock_file.seek(0)
    try:
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock_file.close()
        return 0  # Already running or building; do not create another editor.
    log = (build / 'development.log').open('w', encoding='utf-8', buffering=1)
    sys.stdout = sys.stderr = log
    token = secrets.token_urlsafe(32)
    server = make_server(token)
    port = server.server_address[1]
    flutter = root.parent / '.flutter-sdk/bin/flutter.bat'
    session_file = build / 'development-session.json'
    try:
        print('Starting Flutter development mode; closing the app ends this session.', flush=True)
        process = subprocess.Popen(
            [str(flutter), 'run', '-d', 'windows', '--debug', '--machine',
             f'--dart-define=WORKBENCH_RELOAD_PORT={port}',
             f'--dart-define=WORKBENCH_RELOAD_TOKEN={token}'],
            cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        session = FlutterSession(process)
        server.session = session
        session_file.write_text(json.dumps({'pid': os.getpid(), 'port': port, 'token': token}), encoding='utf-8')
        threading.Thread(target=session.read_output, daemon=True).start()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        code = process.wait()
        server.shutdown()
        print(f'Flutter development session ended ({code}).', flush=True)
        return code
    finally:
        server.server_close()
        session_file.unlink(missing_ok=True)
        lock_file.close()


if __name__ == '__main__':
    raise SystemExit(main())
