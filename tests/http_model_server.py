"""Controllable external HTTP model boundary for API and browser acceptance."""
import json
import select
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Lock, Thread


class ModelHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port=0):
        super().__init__(('127.0.0.1', port), Handler)
        self.requests = []
        self.started = Event()
        self.release = Event()
        self.disconnected = Event()
        self.lock = Lock()

    @property
    def base_url(self):
        return f'http://127.0.0.1:{self.server_port}/v1'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == '/requests':
            with self.server.lock:
                raw = json.dumps(self.server.requests, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        else:
            self.send_error(404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or '{}')
        if self.path == '/release':
            self.server.release.set()
            self.send_response(200)
            self.end_headers()
            return
        if self.path == '/reset':
            with self.server.lock:
                self.server.requests.clear()
            self.server.release.clear()
            self.server.started.clear()
            self.send_response(200)
            self.end_headers()
            return
        with self.server.lock:
            self.server.requests.append({'path': self.path, 'body': body, 'authorization': self.headers.get('Authorization'), 'x-api-key': self.headers.get('x-api-key')})
        if body['model'].startswith('headers-hold'):
            self.server.started.set()
            deadline = time.monotonic() + 15
            while not self.server.release.is_set() and time.monotonic() < deadline:
                ready, _, _ = select.select([self.connection], [], [], .05)
                if ready and not self.connection.recv(1):
                    self.server.disconnected.set()
                    return
        if body['model'].startswith('error'):
            self.send_response(401)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Connection', 'close')
        self.end_headers()
        anthropic = self.path.endswith('/messages')
        system = body.get('system') or '\n'.join(x.get('content', '') for x in body['messages'] if x['role'] == 'system')
        if 'MUJIAN_SCRIPT_JSON' in system:
            request = json.loads(body['messages'][-1]['content'])
            path = request['target_path']
            fragment = {'id': path.get('fragment', 'generated'), 'blocks': [{'type': 'ACTION', 'text': 'HTTP生成正文'}]}
            scene = {'number': path.get('scene', 1), 'heading': '夜', 'fragments': [fragment]}
            episode = {'number': path.get('episode', 1), 'title': 'HTTP生成', 'scenes': [scene]}
            parts = [json.dumps({'WORK': {'episodes': [episode]}, 'EPISODE': episode, 'SCENE': scene, 'FRAGMENT': fragment}[request['target_type']], ensure_ascii=False)]
        else:
            parts = ['第一段回复', '，第二段完成。']
        try:
            for index, part in enumerate(parts):
                event = {'type': 'content_block_delta', 'delta': {'text': part}} if anthropic else {'choices': [{'delta': {'content': part}, 'finish_reason': None}]}
                self.wfile.write(('data: ' + json.dumps(event, ensure_ascii=False) + '\n\n').encode())
                self.wfile.flush()
                if index == 0:
                    self.server.started.set()
                    if body['model'].startswith('hold'):
                        self.server.release.wait(15)
                    if body['model'].startswith('drop'):
                        return
            finish = {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}} if anthropic else {'choices': [{'delta': {}, 'finish_reason': 'stop'}]}
            self.wfile.write(('data: ' + json.dumps(finish) + '\n\ndata: [DONE]\n\n').encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


@contextmanager
def model_server(port=0):
    server = ModelHTTPServer(port)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
