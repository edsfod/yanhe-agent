"""Signing in to 延河 2.0 through BIT's unified sign-in (sso.bit.edu.cn): password, picture captcha and SMS code."""

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qs, quote, urljoin, urlsplit

import keyring
from bs4 import BeautifulSoup
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad, unpad
from keyring.errors import KeyringError

from client import CONFIG, ORIGIN

SSO = 'https://sso.bit.edu.cn'
KEYRING = 'yanhe-agent'
USERNAME = CONFIG / 'username'
TENANT = 21
RESEND_AFTER = 60  # seconds before another SMS code may be asked for
# The sign-in page's key for sealing the AES key of the phone-number lookup, as its own script carries it.
SSO_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAjVr1zKwohU3xA0afprWLSQvIymaSH/V27MedFc+CecXSnORIFMAp4uEIb4taDq/2X4eMeTI66Mu/rB5GKSFDbExF2Gu4NaO/CNDpf1gHMScUrIFCh4CDqzBnx17kclvezLkIK0T8FVa4cRsINvzjbnA6jUSMaf6Fm1n9wTAtW6QYBjssGOEtCj+c38PTBdFMmJbXp3brt1tEBesz6lb3Fjp76FGvDZ08xtYG8fxYPuiMwKU04eS+mcX/BunwgpU3zwekHYB+PWRIvq0lBry9Wms25sJE5T/RAv5fEuMLbBkfcZK3+7ivSZthTmPpr2Ap/ji70ZZ6u2jvR5VJq+LJHQIDAQAB
-----END PUBLIC KEY-----"""


class SignInError(Exception):
  """A failure whose text the sign-in page shows as it is."""


def saved_username():
  return USERNAME.read_text(encoding='utf-8').strip() if USERNAME.exists() else ''


def saved_password(username):
  if not username:
    return None
  try:
    return keyring.get_password(KEYRING, username)
  except KeyringError:
    return None


def seal(text, key):
  """`text` encrypted the way the sign-in page does it: AES-ECB with PKCS#7 padding, in base64."""
  return base64.b64encode(AES.new(key, AES.MODE_ECB).encrypt(pad(text.encode(), 16))).decode()


def unseal(text, key):
  return unpad(AES.new(key, AES.MODE_ECB).decrypt(base64.b64decode(text)), 16).decode()


def csrf_headers():
  """The headers the page sends with calls under /protected/: a random key and an MD5 of it arranged around itself."""
  key = secrets.token_hex(16)
  wrapped = base64.b64encode(key.encode()).decode()
  half = len(wrapped) // 2
  check = hashlib.md5((wrapped[:half] + wrapped + wrapped[half:]).encode()).hexdigest()
  return {'Csrf-Key': key, 'Csrf-Value': check, 'Sid-Language': 'zh_CN'}


def compact(value):
  return json.dumps(value, separators=(',', ':'))


class CasPage:
  """A page of the sign-in service. Its values sit as the text of elements with known IDs, and its form posts to the
  action of the last form on it."""

  def __init__(self, response):
    self.url = response.url
    self.soup = BeautifulSoup(response.text, 'html.parser')
    forms = self.soup.find_all('form', action=True)
    self.action = urljoin(self.url, forms[-1]['action']) if forms else self.url

  def __getitem__(self, element_id):
    element = self.soup.find(id=element_id)
    return element.get_text(strip=True) if element else ''


class SignIn:
  """The steps of a sign-in: password, then a picture captcha or an SMS code when the service asks for one. A step
  returns True once 延河 2.0 accepts the session.

  `publish(**values)` gets what the page should show: the stage (`credentials` or `verify`), a `captcha` image as a
  data URL, the masked `phone`, whether a code is `sending`, and `resend_at`.
  """

  def __init__(self, client, publish):
    self.client, self.publish = client, publish
    self.page = None
    self.username = self.secret = self.phone = ''
    self.remember = True
    self.resend_at = 0

  def call(self, method, url, **kwargs):
    """A request as the sign-in page makes it. Statuses are not raised: the service answers a refused form with an
    error status and a page that says why."""
    headers = kwargs.pop('headers', {})
    if self.page:
      headers['Referer'] = self.page.url
    if method == 'POST':
      headers['Origin'] = SSO
    if '/protected/' in url:
      headers |= csrf_headers()
    return self.client.session.request(method, url, headers=headers, timeout=30, **kwargs)

  def start(self):
    """Sign in without the user if the remembered SSO session or the saved password still does it."""
    if self.open():
      return True
    username = saved_username()
    return bool(saved_password(username)) and self.password(username, '')

  def open(self):
    """Go from 延河 2.0 to the sign-in form; a remembered SSO session signs in on the way, which returns True."""
    self.page = None
    query = {'r': 'auth/login', 'auType': '', 'tenant_code': TENANT, 'forward': ORIGIN + '/course'}
    self.call('GET', ORIGIN + '/casapi/index.php', params=query, allow_redirects=False)  # sets the PHP session
    redirect = self.call('GET', ORIGIN + '/casapi/index.php', params=query | {'auType': 'cas'}, allow_redirects=False)
    service = parse_qs(urlsplit(redirect.headers['Location']).query)['service'][0]  # as CAS compares it byte for byte
    form = self.call('GET', SSO + '/cas/login', params={'service': service})
    if self.client.authenticate():
      return True
    self.page = CasPage(form)
    if not self.page['login-croypto']:
      raise SignInError('无法识别统一身份认证页面，请稍后重试')
    return False

  def captcha_image(self, address):
    """The captcha as a data URL the page can show, fetching the image when the service gave its address."""
    if address.startswith('data:'):
      return address
    image = self.call('GET', urljoin(SSO + '/cas/', address)).content
    return 'data:image/png;base64,' + base64.b64encode(image).decode()

  def password(self, username, password, captcha='', remember=True):
    """Send the user's number and password, with the captcha when one was shown."""
    password = password or saved_password(username)
    if not username or not password:
      raise SignInError('请输入学号和密码')
    on_form = self.page and not self.page['user-object-id']  # not back from the SMS step
    if not on_form and self.open():
      return True
    self.username, self.secret, self.remember = username, password, remember
    key = base64.b64decode(self.page['login-croypto'])
    check = self.call('GET', f'{SSO}/cas/api/protected/user/findCaptchaCount/{quote(username, safe="")}')
    need = check.json()['data']
    if need.get('captchaInvisible') and not captcha:
      self.publish(stage='credentials', captcha=self.captcha_image(need['captchaUrl']))
      return False
    form = {
      'type': 'UsernamePassword',
      '_eventId': 'submit',
      'geolocation': '',
      'execution': self.page['login-page-flowkey'],
      'username': username,
      'croypto': self.page['login-croypto'],
      'password': seal(password, key),
      'captcha_code': captcha,
      'captcha_payload': seal(compact(need.get('captchaPayload', {})), key),
    }
    self.page = CasPage(self.call('POST', self.page.action, data=form))
    if self.page['user-object-id']:  # the service wants an SMS code as well
      self.send_code(stage='verify', captcha=None, phone=self.lookup_phone())
      return False
    if not self.client.authenticate():
      raise SignInError('学号或密码错误')
    self.keep_credentials()
    return True

  def lookup_phone(self):
    """Remember the phone number the code goes to, which the service hands out only encrypted, and return it masked."""
    key = secrets.token_bytes(16)
    sealed_key = PKCS1_v1_5.new(RSA.import_key(SSO_PUBLIC_KEY)).encrypt(base64.b64encode(key))
    reply = self.call(
      'POST',
      SSO + '/cas/api/protected/sms/getPhoneNumberByUserId',
      data=seal(compact({'userId': self.page['user-object-id']}), key),
      headers={
        'Content-Type': 'application/json',
        'hasCrypto': 'true',
        'privateKey': base64.b64encode(sealed_key).decode(),
      },
    )
    body = reply.text
    if body.startswith('"'):  # sometimes the sealed answer comes as a JSON string
      body = json.loads(body)
    number = json.loads(unseal(body, key))['data']
    self.phone = number['tel']
    return number.get('maskTel', '')

  def send_code(self, **values):
    """Have an SMS code sent, at most once a minute, publishing `values` with the sending state."""
    if time.time() < self.resend_at:
      raise SignInError('请稍后再发送')
    self.publish(sending=True, **values)
    try:
      sent = self.call(
        'POST',
        SSO + '/cas/api/protected/sms/publicNoToken/sendSmsCode',
        json={'phone': self.phone, 'businessNo': '0008'},
      ).json()
      if sent['code'] != 200:
        raise SignInError('验证码发送失败，请稍后重试')
      self.resend_at = time.time() + RESEND_AFTER
    finally:
      self.publish(sending=False, resend_at=self.resend_at)

  def verify(self, code):
    """Send the SMS code and finish signing in with it."""
    checked = self.call(
      'POST',
      SSO + '/cas/api/protected/sms/checkToken',
      json={'phone': self.phone, 'token': code, 'delete': False, 'trustDevice': True},
    ).json()
    if checked['code'] != 200:
      raise SignInError('验证码错误，请重新输入')
    form = {
      'username': self.username,
      'password': code,
      'type': 'smsLogin',
      '_eventId': 'submit',
      'geolocation': '',
      'execution': self.page['login-page-flowkey'],
      'captcha_code': '',
      'trustDevice': 'true',
    }
    self.call('POST', self.page.action, data=form)
    if not self.client.authenticate():
      raise SignInError('登录未完成，请重新登录')
    self.keep_credentials()
    return True

  def reset(self):
    """Go back to the password form."""
    self.page, self.resend_at = None, 0
    self.publish(stage='credentials', phone='', sending=False, resend_at=0)

  def keep_credentials(self):
    """Save the number and password in the keychain when the user asked to, and forget them otherwise."""
    try:
      if self.remember:
        keyring.set_password(KEYRING, self.username, self.secret)
        USERNAME.write_text(self.username + '\n', encoding='utf-8')
      elif saved_password(self.username):
        keyring.delete_password(KEYRING, self.username)
        USERNAME.unlink(missing_ok=True)
    except KeyringError as error:
      print(f'Keychain unavailable, credentials not saved: {error}', flush=True)
