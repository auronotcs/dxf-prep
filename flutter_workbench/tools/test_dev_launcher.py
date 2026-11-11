import io
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dev_launcher import FlutterSession, make_server


class FakeFlutter:
    def __init__(self):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()

    def poll(self):
        return None


class ReloadTests(unittest.TestCase):
    def setUp(self):
        self.process = FakeFlutter()
        self.session = FlutterSession(self.process)
        self.session.receive({'event': 'app.start', 'params': {'appId': 'test-app'}})
        self.session.receive({'event': 'app.started'})

    def reply_on_flush(self, packet):
        self.process.stdin.flush = lambda: self.session.receive({'id': 1, **packet})

    def test_success_is_hot_reload_only(self):
        self.reply_on_flush({'result': {'code': 0, 'message': 'reloaded'}})
        self.assertEqual(self.session.reload()[0], 200)
        command = json.loads(self.process.stdin.getvalue())[0]
        self.assertEqual(command['method'], 'app.restart')
        self.assertFalse(command['params']['fullRestart'])
        self.assertFalse(command['params']['pause'])
        self.assertEqual(command['params']['appId'], 'test-app')

    def test_compiler_error_does_not_restart(self):
        self.reply_on_flush({'result': {'code': 1, 'message': 'Compile error'}})
        status, result = self.session.reload()
        self.assertEqual(status, 422)
        self.assertIn('Compile error', result['message'])
        self.assertEqual(len(self.process.stdin.getvalue().splitlines()), 1)

    def test_timeout_and_not_ready(self):
        self.assertEqual(self.session.reload(timeout=0)[0], 504)
        self.assertFalse(self.session.pending)
        self.session.receive({'event': 'app.stop'})
        self.assertEqual(self.session.reload()[0], 503)

    def test_duplicate_reload_rejected(self):
        self.session.reload_lock.acquire()
        self.assertEqual(self.session.reload()[0], 409)
        self.assertEqual(self.process.stdin.getvalue(), '')
        self.session.reload_lock.release()

    def test_http_requires_session_token_and_only_exposes_reload(self):
        self.reply_on_flush({'result': {'code': 0}})
        server = make_server('test-secret')
        server.session = self.session
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{server.server_address[1]}'
            for path, token, status in [('/reload', '', 403), ('/restart', 'test-secret', 404)]:
                request = Request(base + path, method='POST', headers={'Authorization': 'Bearer ' + token})
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request)
                self.assertEqual(raised.exception.code, status)
                self.assertEqual(self.process.stdin.getvalue(), '')
            request = Request(base + '/reload', method='POST', headers={'Authorization': 'Bearer test-secret'})
            with urlopen(request) as response:
                self.assertTrue(json.load(response)['ok'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
