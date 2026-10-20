"""The local sign-in page, which follows the sign-in's state through server-sent events.

`login` runs the page in a process of its own. While it runs, the process writes the page's address to
`signin.json` next to the cookies and refreshes it, so that every command and every session finds the same page.
"""

import json
import logging
import secrets
import threading
import time
import traceback
import webbrowser
from pathlib import Path

import requests
from flask import Flask, Response, abort, request, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.serving import make_server

from auth import SignIn, SignInError, saved_password, saved_username
from client import CONFIG
from jobs import BEAT, STALE, detached
from workspace import read_json, write_json

UI = Path(__file__).with_name('ui')  # index.html is the whole page
PAGE = CONFIG / 'signin.json'
OPEN_FOR = 1800  # seconds the page waits for the user


def live_page():
  """The address of a sign-in page whose process is still running, or None."""
  page = read_json(PAGE, None)
  return page['url'] if page and time.time() - page['beat'] < STALE else None


def open_page(timeout=20):
  """The address of the running sign-in page, starting one if there is none; None when the new process signed in on
  its own, with the saved password, and so ended without serving a page."""
  if url := live_page():
    return url
  PAGE.unlink(missing_ok=True)
  process = detached(['login', '--serve'], CONFIG, CONFIG / 'signin.log')
  give_up = time.monotonic() + timeout
  while time.monotonic() < give_up:
    if url := live_page():
      return url
    if process.poll() is not None:
      if process.returncode == 0:
        return None
      break
    time.sleep(0.2)
  raise RuntimeError(f'The sign-in page did not start; see {CONFIG / "signin.log"}')


class State:
  """Values the page shows, which every change sends to the open pages."""

  def __init__(self, **values):
    self.values = values
    self.version = self.delivered = 0
    self.changed = threading.Condition()

  def update(self, **values):
    with self.changed:
      self.values.update(values)
      self.version += 1
      self.changed.notify_all()

  def events(self):
    """The event stream of one page: the values after every change, and a comment every 15 seconds besides, whose
    write tells when the tab was closed."""
    seen = None
    while True:
      with self.changed:
        news = self.changed.wait_for(lambda last=seen: self.version != last, timeout=15)
        seen = self.version
        payload = json.dumps(self.values, ensure_ascii=False) if news else None
      yield f'data: {payload}\n\n' if news else ': keep-alive\n\n'
      with self.changed:  # the server asks for the next event only once this one is written out
        self.delivered = max(self.delivered, seen)
        self.changed.notify_all()

  def flush(self, timeout=5):
    """Wait until a page has the latest values."""
    with self.changed:
      self.changed.wait_for(lambda: self.delivered >= self.version, timeout)


def describe(error):
  """What the page says about an error, in Chinese and without addresses, tokens or account data; the terminal gets
  the traceback."""
  if isinstance(error, SignInError):
    return str(error)
  traceback.print_exception(error)
  if isinstance(error, requests.RequestException):
    return '网络连接失败，请稍后重试'
  return '出错了，详情见终端'


def sign_in(client):
  """Serve the sign-in page until the user has signed in or OPEN_FOR seconds have passed, announcing its address in
  `signin.json`; when the remembered SSO session or the saved password still works, sign in at once instead."""
  username = saved_username()
  state = State(
    stage='credentials',  # credentials, verify or done
    username=username,
    saved=bool(saved_password(username)),
    captcha=None,
    phone='',
    sending=False,
    resend_at=0,
    message='',
  )
  steps = SignIn(client, state.update)
  signed_in = threading.Event()
  one_at_a_time = threading.Lock()

  def run(step):
    if not one_at_a_time.acquire(blocking=False):
      raise SignInError('请稍候')
    try:
      if step():
        state.update(stage='done', captcha=None)
        signed_in.set()
    finally:
      one_at_a_time.release()

  try:
    run(steps.start)
  except Exception as error:  # noqa: BLE001 - the page shows it; the details go to the terminal
    state.update(message=describe(error))
  if signed_in.is_set():
    return

  app = Flask(__name__, static_folder=None)
  token = secrets.token_urlsafe(32)

  @app.before_request
  def guard():
    if request.host != f'127.0.0.1:{server.port}':  # a page reached under another name, as in DNS rebinding
      abort(403)
    if request.path.startswith('/api/'):
      given = request.headers.get('X-Token') or request.args.get('token', '')
      if not secrets.compare_digest(given, token):
        abort(403)

  @app.get('/', defaults={'name': 'index.html'})
  @app.get('/<path:name>')
  def page(name):
    return send_from_directory(UI, name, max_age=0)

  @app.get('/api/events')
  def events():
    return Response(state.events(), mimetype='text/event-stream', headers={'Cache-Control': 'no-store'})

  @app.errorhandler(Exception)
  def refused(error):
    return error if isinstance(error, HTTPException) else ({'error': describe(error)}, 400)

  @app.post('/api/login')
  def login():
    form = request.json
    run(lambda: steps.password(form['username'], form['password'], form['captcha'], form['remember']))
    return {}

  @app.post('/api/resend')
  def resend():
    run(steps.send_code)
    return {}

  @app.post('/api/verify')
  def verify():
    run(lambda: steps.verify(request.json['code']))
    return {}

  @app.post('/api/cancel')
  def cancel():
    steps.reset()
    return {}

  logging.getLogger('werkzeug').setLevel(logging.WARNING)
  server = make_server('127.0.0.1', 0, app, threaded=True)
  threading.Thread(target=server.serve_forever, daemon=True).start()
  url = f'http://127.0.0.1:{server.port}/#{token}'
  webbrowser.open(url)
  print(f'Sign-in page: {url}', flush=True)
  close_at = time.monotonic() + OPEN_FOR
  try:
    while time.monotonic() < close_at and not signed_in.is_set():
      write_json(PAGE, {'url': url, 'beat': time.time()})
      signed_in.wait(BEAT)  # waiting in steps keeps Ctrl-C working on Windows
    if signed_in.is_set():
      state.flush()
  finally:
    PAGE.unlink(missing_ok=True)
    server.shutdown()
