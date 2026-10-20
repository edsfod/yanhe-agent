"""The AutoSlides app: the Yanhe 2.0 sign-in it keeps and its command line, played by a stand-in script."""

import json
import sys
import time
from http.cookiejar import Cookie

import pytest

import autoslides
import client

DAY_MS = 86_400_000


def keep_sessions(sessions):
  folder = autoslides.user_data()
  folder.mkdir(parents=True, exist_ok=True)
  (folder / 'autoslides-config.json').write_text(json.dumps({'yanhe2Sessions': sessions}), encoding='utf-8')


def test_tokens_are_the_unexpired_sessions_of_the_app():
  assert autoslides.tokens() == []
  now = time.time() * 1000
  keep_sessions({
    '1120000001': {'jwt': 'eyJlive', 'expiresAt': now + DAY_MS},
    '1120000002': {'jwt': 'eyJold', 'expiresAt': now - 1},
    '1120000003': {'jwt': '', 'expiresAt': 0},
  })  # fmt: skip
  assert autoslides.tokens() == ['eyJlive']


class Answer:
  def __init__(self, code):
    self.ok, self.code = True, code

  def json(self):
    return {'code': self.code, 'params': {'id': 7, 'tenant_id': 21}}


def accepting(*good):
  """A stand-in for the session's GET that accepts only the given JWTs."""

  def get(session, url, **kwargs):
    return Answer(200 if session.headers.get('Authorization', '')[7:] in good else 403)

  return get


def jwt(name):
  return f'eyJ{name}.eyJ{name}.{name}'


def save_cookie(instance, value):
  instance.session.cookies.set_cookie(
    Cookie(0, '_token', value, None, False, '.yanhekt.cn', True, True, '/', True, False, None, False, None, None, {})
  )


def test_the_app_sign_in_comes_first_and_stays_out_of_the_cookies(monkeypatch):
  keep_sessions({'1120000001': {'jwt': jwt('app'), 'expiresAt': time.time() * 1000 + DAY_MS}})
  monkeypatch.setattr(client.PlatformSession, 'get', accepting(jwt('app'), jwt('own')), raising=False)
  instance = client.Yanhekt()
  save_cookie(instance, jwt('own'))
  assert instance.authenticate()
  assert instance.source == 'AutoSlides'
  assert instance.session.headers['Authorization'] == 'Bearer ' + jwt('app')
  assert not (client.CONFIG / 'cookies.txt').exists()


def test_a_rejected_app_sign_in_falls_back_to_the_own_one(monkeypatch):
  keep_sessions({'1120000001': {'jwt': jwt('app'), 'expiresAt': time.time() * 1000 + DAY_MS}})
  monkeypatch.setattr(client.PlatformSession, 'get', accepting(jwt('own')), raising=False)
  instance = client.Yanhekt()
  save_cookie(instance, jwt('own'))
  assert instance.authenticate()
  assert instance.source == 'yanhe-agent'
  monkeypatch.setattr(client.PlatformSession, 'get', accepting(), raising=False)
  assert not client.Yanhekt().authenticate()


@pytest.fixture
def app(tmp_path, monkeypatch):
  """A stand-in for the app's command line that answers `fail` with sign_in_required and echoes anything else."""
  script = tmp_path / 'autoslides_cli.py'
  script.write_text(
    'import json, sys\n'
    "args = sys.argv[sys.argv.index('--') + 1:]\n"
    "if 'fail' in args:\n"
    "  print(json.dumps({'ok': False, 'error': {'code': 'sign_in_required', 'message': 'signed out'}}))\n"
    '  sys.exit(4)\n'
    "print(json.dumps({'ok': True, 'data': {'args': args}}))\n",
    encoding='utf-8',
  )
  monkeypatch.setenv('YANHE_AGENT_AUTOSLIDES', str(script))
  return script


def test_run_returns_the_data_of_the_json_answer(app):
  assert autoslides.command() == [sys.executable, str(app)]
  assert autoslides.run('course', 70101) == {'args': ['course', '70101', '--json']}


def test_run_turns_an_error_code_into_what_the_user_should_do(app):
  with pytest.raises(autoslides.AutoSlidesError, match='Sign in to Yanhekt in the AutoSlides window') as error:
    autoslides.run('fail')
  assert error.value.code == 'sign_in_required'


def test_run_asks_for_the_app_to_be_opened_rather_than_starting_it(app, monkeypatch):
  monkeypatch.setattr(autoslides, 'running', lambda: False)
  with pytest.raises(autoslides.AutoSlidesError, match='Open AutoSlides from its shortcut'):
    autoslides.run('course', 70101)
