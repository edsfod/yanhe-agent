"""The unified sign-in's pieces, with the sign-in service played by a table of answers."""

import base64
import hashlib
import json

import pytest

import auth

FORM = """<html><body>
<p id="login-croypto">{key}</p><p id="login-page-flowkey">flow-1</p>
<form action="/cas/other"></form><form action="/cas/login?service=x"></form>
</body></html>"""
KEY = base64.b64encode(b'0123456789abcdef').decode()


class Reply:
  def __init__(self, url='https://sso.bit.edu.cn/cas/login', text='', body=None, headers=None):
    self.url, self.text, self.body, self.headers, self.content = url, text, body, headers or {}, b'png'

  def json(self):
    return self.body


class FakeClient:
  """Stands for the platform client: `authenticate` says whether 延河 2.0 took the session."""

  def __init__(self):
    self.accepts = False
    self.session = None

  def authenticate(self):
    return self.accepts


def test_seal_is_aes_ecb_in_base64_and_unseal_reverses_it():
  key = b'0123456789abcdef'
  sealed = auth.seal('密码 secret', key)
  assert len(base64.b64decode(sealed)) % 16 == 0
  assert auth.unseal(sealed, key) == '密码 secret'


def test_csrf_value_is_the_md5_of_the_wrapped_key_around_itself():
  headers = auth.csrf_headers()
  wrapped = base64.b64encode(headers['Csrf-Key'].encode()).decode()
  half = len(wrapped) // 2
  assert headers['Csrf-Value'] == hashlib.md5((wrapped[:half] + wrapped + wrapped[half:]).encode()).hexdigest()
  assert headers['Sid-Language'] == 'zh_CN'


def test_a_page_reads_values_by_id_and_posts_to_its_last_form():
  page = auth.CasPage(Reply(text=FORM.format(key=KEY)))
  assert page['login-page-flowkey'] == 'flow-1'
  assert page['user-object-id'] == ''
  assert page.action == 'https://sso.bit.edu.cn/cas/login?service=x'


@pytest.fixture
def signin(monkeypatch):
  """A SignIn on the password form, whose calls are answered from `answers` and recorded in `calls`."""
  published, calls = [], []
  client = FakeClient()
  steps = auth.SignIn(client, lambda **values: published.append(values))
  steps.page = auth.CasPage(Reply(text=FORM.format(key=KEY)))
  answers = {}

  def call(method, url, **kwargs):
    calls.append((method, url, kwargs))
    for fragment, reply in answers.items():
      if fragment in url:
        return reply(kwargs) if callable(reply) else reply
    raise AssertionError(f'unexpected call {method} {url}')

  monkeypatch.setattr(steps, 'call', call)
  monkeypatch.setattr(auth, 'saved_password', lambda username: None)
  monkeypatch.setattr(steps, 'keep_credentials', lambda: published.append({'kept': steps.username}))
  steps.client_, steps.published, steps.calls, steps.answers = client, published, calls, answers
  return steps


def test_a_captcha_is_shown_before_the_password_is_sent(signin):
  signin.answers['findCaptchaCount'] = Reply(body={'data': {'captchaInvisible': True, 'captchaUrl': 'captcha.png'}})
  signin.answers['captcha.png'] = Reply()
  assert signin.password('1120000001', 'pw') is False
  assert signin.published == [
    {'stage': 'credentials', 'captcha': 'data:image/png;base64,' + base64.b64encode(b'png').decode()}
  ]


def test_the_password_is_sent_sealed_and_signs_in(signin):
  signin.answers['findCaptchaCount'] = Reply(body={'data': {}})

  def accept(request):
    signin.client_.accepts = True
    return Reply(text='<html></html>')

  signin.answers['/cas/login?service=x'] = accept
  assert signin.password('1120000001', 'pw', remember=True) is True
  form = signin.calls[-1][2]['data']
  assert auth.unseal(form['password'], base64.b64decode(KEY)) == 'pw'
  assert form['execution'] == 'flow-1'
  assert signin.published[-1] == {'kept': '1120000001'}


def test_a_wrong_password_is_reported(signin):
  signin.answers['findCaptchaCount'] = Reply(body={'data': {}})
  signin.answers['/cas/login?service=x'] = Reply(text='<html></html>')
  with pytest.raises(auth.SignInError, match='学号或密码错误'):
    signin.password('1120000001', 'wrong')


def test_an_sms_step_looks_up_the_phone_and_sends_a_code(signin, monkeypatch):
  signin.answers['findCaptchaCount'] = Reply(body={'data': {}})
  signin.answers['/cas/login?service=x'] = Reply(text='<p id="user-object-id">u-7</p><form action="/cas/sms"></form>')

  def phone(request):
    sealed_key = base64.b64decode(request['headers']['privateKey'])
    assert sealed_key and json.loads(auth.unseal(request['data'], lookup_key[0]))['userId'] == 'u-7'
    return Reply(
      text=json.dumps(auth.seal(json.dumps({'data': {'tel': '13800001234', 'maskTel': '138****1234'}}), lookup_key[0]))
    )

  lookup_key = [b'fedcba9876543210']
  monkeypatch.setattr(auth.secrets, 'token_bytes', lambda size: lookup_key[0])
  signin.answers['getPhoneNumberByUserId'] = phone
  signin.answers['sendSmsCode'] = Reply(body={'code': 200})
  assert signin.password('1120000001', 'pw') is False
  assert signin.phone == '13800001234'
  assert {'sending': True, 'stage': 'verify', 'captcha': None, 'phone': '138****1234'} in signin.published
  with pytest.raises(auth.SignInError, match='请稍后再发送'):
    signin.send_code()
