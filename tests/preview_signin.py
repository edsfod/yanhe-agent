"""Serve the sign-in page against a stand-in for the BIT sign-in, to try the page in a browser.

uv run --no-project --with-requirements skills/yanhe-agent/scripts/yanhe_agent.py python tests/preview_signin.py
prints the page's address. Any student ID and password lead to the image captcha; any captcha leads to the SMS step;
the code 123456 signs in and any other is refused.
"""

import base64
import sys
import tempfile
import time
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'skills' / 'yanhe-agent' / 'scripts'))

import server
from auth import SignInError


def png(width=80, height=28):
  """A grey PNG as a data URL, standing in for the captcha image."""
  rows = b''.join(b'\x00' + bytes([200, 200, 200]) * width for _ in range(height))

  def chunk(kind, data):
    return len(data).to_bytes(4, 'big') + kind + data + zlib.crc32(kind + data).to_bytes(4, 'big')

  header = width.to_bytes(4, 'big') + height.to_bytes(4, 'big') + b'\x08\x02\x00\x00\x00'
  data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b'')
  return 'data:image/png;base64,' + base64.b64encode(data).decode()


class SignIn:
  def __init__(self, client, publish):
    self.publish = publish
    self.resend_at = 0

  def start(self):
    return False

  def password(self, username, password, captcha='', remember=True):
    if not username or not password:
      raise SignInError('请输入学号和密码')
    if not captcha:
      self.publish(stage='credentials', captcha=png())
      return False
    self.send_code(stage='verify', captcha=None, phone='138****1234')
    return False

  def send_code(self, **values):
    if time.time() < self.resend_at:
      raise SignInError('请稍后再发送')
    self.publish(sending=True, **values)
    time.sleep(1)
    self.resend_at = time.time() + 60
    self.publish(sending=False, resend_at=self.resend_at)

  def verify(self, code):
    time.sleep(0.5)
    if code != '123456':
      raise SignInError('验证码错误，请重新输入')
    return True

  def reset(self):
    self.resend_at = 0
    self.publish(stage='credentials', phone='', sending=False, resend_at=0)


if __name__ == '__main__':
  folder = Path(tempfile.mkdtemp())
  server.SignIn, server.PAGE, server.CONFIG = SignIn, folder / 'signin.json', folder
  server.saved_username = lambda: '1120230000'
  server.saved_password = lambda username: None
  server.webbrowser.open = lambda url: None
  server.sign_in(None)
  print('Signed in; the page has closed.', flush=True)
