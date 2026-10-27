"""延河 2.0 (aita.yanhekt.cn): signing in, and reading courses, replays and the platform's fast transcripts."""

import functools
import html
import json
import os
import re
import socket
import ssl
import subprocess
import threading
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from urllib.parse import unquote, urlsplit

import certifi
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

import autoslides

ORIGIN = 'https://aita.yanhekt.cn'
CAMPUS_IP = '10.0.11.240'  # the address of aita.yanhekt.cn inside the campus network
CONFIG = Path.home() / '.config' / 'yanhe-agent'
SETTINGS = CONFIG / 'config.json'
USER_AGENT = (
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
)
ASR = {'bcut': "BCut, Bilibili's speech recognition, which receives the sound track of each lecture"}
SORTS = {'newest': 'c.created_at desc', 'oldest': 'c.created_at asc', 'popular': 'c.click_record_num desc'}
VIEWS = {'3': 'classroom', '2': 'screen'}  # video_list[].type of a replay
READY = '2'  # video_list[].status of a recording that plays
JWT = re.compile(r'eyJ[\w-]+\.[\w-]+\.[\w-]+')


class APIError(Exception):
  """The platform answered, but with an error code."""


# Errors that end a command or a lecture's step with `describe`'s text instead of a traceback; OSError covers requests
FAILURES = (APIError, OSError, RuntimeError, ValueError, subprocess.CalledProcessError)


def settings():
  return json.loads(SETTINGS.read_text(encoding='utf-8')) if SETTINGS.exists() else {}


def save_settings(values):
  CONFIG.mkdir(parents=True, exist_ok=True, mode=0o700)
  SETTINGS.write_text(json.dumps(values, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def chosen_asr():
  """The transcription service the user agreed to; otherwise stop, saying what to ask them."""
  service = settings().get('asr')
  if service not in ASR:
    raise ValueError(
      f"Transcription needs the user's consent first: it uses {ASR['bcut']}. Ask the user; once they agree, run "
      '`config --asr bcut`. Without it, leave transcripts out with `--no-transcribe`.'
    )
  return service


CAMPUS = threading.Event()  # set once the campus address has answered


def on_campus():
  """Whether the campus address answers and shows the site's certificate."""
  checked = ssl.create_default_context(cafile=certifi.where())
  try:
    with socket.create_connection((CAMPUS_IP, 443), timeout=3) as raw:
      checked.wrap_socket(raw, server_hostname=urlsplit(ORIGIN).hostname).close()
    return True
  except OSError:
    return False


@functools.cache
def upgrade_to_campus():
  """Look for the campus network in the background; once found, new connections to the site go to its campus
  address, while the name in the URL still decides SNI and the certificate check."""

  def look():
    if not on_campus():
      return
    site, resolve = urlsplit(ORIGIN).hostname, socket.getaddrinfo
    socket.getaddrinfo = lambda name, *rest, **options: resolve(CAMPUS_IP if name == site else name, *rest, **options)
    CAMPUS.set()

  threading.Thread(target=look, daemon=True).start()


class PlatformSession(requests.Session):
  """A session that leaves out the environment's proxies once on the campus network, since a proxy would look the
  site up itself and take the public route."""

  def request(self, *args, **kwargs):
    self.trust_env = not CAMPUS.is_set()
    return super().request(*args, **kwargs)


def named(rows, key, name):
  """The ID of the row whose `key` is `name`; otherwise stop, listing the names there are."""
  for row in rows:
    if row[key] == name:
      return row['id']
  raise ValueError(f'Unknown {name!r}; choose from: {", ".join(row[key] for row in rows)}')


def _course_row(row, ident='id', title='title', teacher='realname', term='term_name'):
  return {'id': str(row[ident]), 'title': html.unescape(row[title]), 'teacher': row[teacher] or '', 'term': row[term]}


def _replays(tree):
  """The replays that can be played, from a course's sub_list, which nests years, months and weeks."""
  for node in tree.values() if isinstance(tree, dict) else tree:
    if isinstance(node, dict) and 'sub_title' in node:
      if node['playback_status'] == '1':
        yield node
    elif isinstance(node, (dict, list)):
      yield from _replays(node)


class Yanhekt:
  """A signed-in or signing-in session with 延河 2.0."""

  def __init__(self):
    CONFIG.mkdir(parents=True, exist_ok=True, mode=0o700)
    self.session = PlatformSession()
    self.session.headers.update({'User-Agent': USER_AGENT, 'Referer': ORIGIN + '/'})
    retry = Retry(3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    self.session.mount('https://', HTTPAdapter(max_retries=retry))
    self.session.cookies = MozillaCookieJar(CONFIG / 'cookies.txt')
    if (CONFIG / 'cookies.txt').exists():
      self.session.cookies.load(ignore_discard=True)
    self.profile = self.source = None

  def authenticate(self):
    """Use the Yanhe 2.0 sign-in that AutoSlides keeps, or else the saved `_token` cookie, if Yanhekt still accepts
    it; `source` then names the one in use."""
    token = next((c.value for c in self.session.cookies if c.name == '_token'), '')
    saved = JWT.search(unquote(token))
    candidates = [(jwt, 'AutoSlides') for jwt in autoslides.tokens()] + ([(saved[0], 'yanhe-agent')] if saved else [])
    for jwt, source in candidates:
      self.session.headers['Authorization'] = 'Bearer ' + jwt
      response = self.session.get(ORIGIN + '/userapi/v1/infosimple', timeout=30)
      body = response.json() if response.ok else {}
      if body.get('code') == 200:
        self.profile, self.source = body['params'], source
        if source == 'yanhe-agent':
          self.session.cookies.save(ignore_discard=True, ignore_expires=True)
          os.chmod(CONFIG / 'cookies.txt', 0o600)
        return True
    self.session.headers.pop('Authorization', None)
    return False

  def get_json(self, path, headers=None, **params):
    response = self.session.get(ORIGIN + path, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()

  def get(self, path, headers=None, **params):
    """A call to an interface that reports `code` 0, or 10002 when nothing is recorded."""
    body = self.get_json(path, headers, **params)
    if body['code'] not in (0, 10002):
      raise APIError(f'{path}: {body.get("msg") or body["code"]}')
    return body

  def courses(self):
    """The user's courses, the newest term first."""
    found, page = [], 1
    while True:
      query = {'force_mycourse': 1, 'nowpage': page, 'per-page': 100}
      result = self.get_json('/vlabpassportapi/v1/account-profile/course', **query)['params']['result']
      found += [_course_row(row, 'Id', 'Title', 'Teacher', 'TermName') for row in result['data']]
      if not result['data'] or len(found) >= result['total']:
        return found
      page += 1

  def search(self, keyword='', term=None, college=None, sort=None, page=1, per_page=50):
    """One page of a search through every course of the platform: (how many match, the page's courses)."""
    tenant = self.profile['tenant_id']
    query = {'tenant': tenant, 'title': keyword, 'order': SORTS.get(sort, ''), 'page': page, 'per_page': per_page}
    if term:
      query['term'] = named(self.get_json('/vlabpassportapi/v1/course/term-list')['params']['list'], 'term_name', term)
    if college:
      colleges = self.get_json('/vlabpassportapi/v1/user/structures')['params']['data']
      query['struc_ids[]'] = named(colleges, 'display_name', college)
    data = self.get('/courseapi/v3/multi-search/get-course-list', {'tenant-id': str(tenant)}, **query)['data']
    return int(data['total']), [_course_row(row) | {'college': row['kkxy_name'] or ''} for row in data['list']]

  def course(self, course_id):
    """A course with its playable replays in the order the classes took place."""
    data = self.get('/courseapi/v3/multi-search/get-course-detail', course_id=course_id)['data']
    replays = sorted(_replays(data['sub_list']), key=lambda replay: int(replay['class_begin']))
    return {
      'id': course_id,
      'title': data['title'],
      'teacher': data['realname'],
      'term': data['term_name'],
      'code': data.get('kcwybm') or '',  # the registrar code, under which Yanhekt lists the course too
      'replays': [{'sub_id': r['id'], 'title': r['sub_title'], 'begin': int(r['class_begin'])} for r in replays],
    }

  def lecture(self, course_id, sub_id):
    """A replay's length in seconds and the views it has a playable recording of."""
    data = self.get('/courseapi/v3/portal-home-setting/get-sub-info', course_id=course_id, sub_id=sub_id)['data']
    ready = {str(video['type']) for video in (data.get('video_list') or {}).values() if str(video['status']) == READY}
    return {'duration_s': float(data['duration']), 'views': [view for kind, view in VIEWS.items() if kind in ready]}

  def subtitles(self, sub_id):
    """The platform's fast transcript of a replay as (start, end, text) rows."""
    body = self.get('/courseapi/v3/web-socket/search-trans-result', sub_id=sub_id, format='json')
    latest = {}  # the platform repeats segments as it revises them; the last version of each start wins
    for group in body['list']:
      for segment in group['all_content']:
        start = float(segment['BeginSec'])
        latest[start] = (start, float(segment['EndSec']), segment['Text'])
    return list(latest.values())


def describe(error):
  """An error as one line, without query strings, which can carry signatures."""
  if isinstance(error, requests.HTTPError):
    address = urlsplit(error.response.url)
    return f'HTTP {error.response.status_code} from {address.netloc}{address.path}'
  if isinstance(error, requests.RequestException):
    return f'Network error: {type(error).__name__}'
  if isinstance(error, OSError) and error.filename:
    try:
      where = os.path.relpath(error.filename)
    except ValueError:  # on another drive of Windows
      where = error.filename
    return f'{error.strerror}: {where}'
  if isinstance(error, subprocess.CalledProcessError):
    return f'ffmpeg failed with exit status {error.returncode}'
  return str(error)
