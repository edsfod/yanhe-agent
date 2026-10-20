"""The platform client's parsing of API bodies. The bodies are shaped after what the code reads, not recorded responses."""

import subprocess

import pytest
import requests

import client
from client import Yanhekt, describe, named


@pytest.fixture
def yanhekt(monkeypatch):
  """A client that answers `get` from a table of paths and never touches the network or the home folder."""
  bodies = {}
  instance = Yanhekt.__new__(Yanhekt)
  instance.profile = {'id': 42, 'tenant_id': 7, 'phone': '13800001234'}
  monkeypatch.setattr(instance, 'get', lambda path, headers=None, **params: bodies[path])
  instance.bodies = bodies
  return instance


def replay(sub_id, begin, status='1'):
  return {'id': sub_id, 'sub_title': f'2026-9-{begin}第1节', 'playback_status': status, 'class_begin': str(begin)}


def test_course_flattens_the_calendar_and_keeps_played_replays_in_class_order(yanhekt):
  yanhekt.bodies['/courseapi/v3/multi-search/get-course-detail'] = {
    'data': {
      'title': '编译原理',
      'realname': '李老师',
      'term_name': '2026-2027-1',
      'sub_list': {'2026': {'9': {'week 2': [replay('b', 8), replay('c', 9, '0')], 'week 1': [replay('a', 1)]}}},
    }
  }
  assert yanhekt.course('10001') == {
    'id': '10001',
    'title': '编译原理',
    'teacher': '李老师',
    'term': '2026-2027-1',
    'code': '',
    'replays': [
      {'sub_id': 'a', 'title': '2026-9-1第1节', 'begin': 1},
      {'sub_id': 'b', 'title': '2026-9-8第1节', 'begin': 8},
    ],
  }


def test_lecture_maps_ready_videos_to_views(yanhekt):
  yanhekt.bodies['/courseapi/v3/portal-home-setting/get-sub-info'] = {
    'data': {
      'duration': '5400',
      'video_list': {
        'x': {'type': 3, 'status': 2, 'preview_url': 'https://cdn/classroom.m3u8'},
        'y': {'type': '2', 'status': '1', 'preview_url': 'https://cdn/screen.m3u8'},
      },
    }
  }
  assert yanhekt.lecture('10001', 'a') == {'duration_s': 5400.0, 'views': ['classroom']}


def test_lecture_without_videos(yanhekt):
  yanhekt.bodies['/courseapi/v3/portal-home-setting/get-sub-info'] = {'data': {'duration': '0', 'video_list': None}}
  assert yanhekt.lecture('10001', 'a') == {'duration_s': 0.0, 'views': []}


def test_subtitles_keep_the_last_revision_of_each_segment(yanhekt):
  yanhekt.bodies['/courseapi/v3/web-socket/search-trans-result'] = {
    'list': [
      {
        'all_content': [
          {'BeginSec': '1', 'EndSec': '2', 'Text': '初稿'},
          {'BeginSec': '3', 'EndSec': '4', 'Text': '乙'},
        ]
      },
      {'all_content': [{'BeginSec': '1.0', 'EndSec': '2.5', 'Text': '修订'}]},
    ]
  }
  assert yanhekt.subtitles('a') == [(1.0, 2.5, '修订'), (3.0, 4.0, '乙')]


def test_named():
  rows = [{'id': 1, 'name': '计算机学院'}, {'id': 2, 'name': '数学学院'}]
  assert named(rows, 'name', '数学学院') == 2
  with pytest.raises(ValueError, match="Unknown '物理'; choose from: 计算机学院, 数学学院"):
    named(rows, 'name', '物理')


def test_describe_hides_query_strings():
  response = requests.Response()
  response.status_code, response.url = 404, 'https://cvideo.yanhekt.cn/play/a.mp4?t=secret'
  assert describe(requests.HTTPError(response=response)) == 'HTTP 404 from cvideo.yanhekt.cn/play/a.mp4'
  assert describe(requests.ConnectionError('https://x/?t=secret')) == 'Network error: ConnectionError'
  assert describe(subprocess.CalledProcessError(1, ['ffmpeg'])) == 'ffmpeg failed with exit status 1'
  assert describe(ValueError('plain')) == 'plain'


def test_platform_requests_skip_environment_proxies_on_campus(monkeypatch):
  monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:9')  # nothing listens there
  sent = []
  monkeypatch.setattr(requests.Session, 'request', lambda self, *args, **kwargs: sent.append(self.trust_env))
  session = client.PlatformSession()
  session.request('GET', 'https://aita.yanhekt.cn/')
  client.CAMPUS.set()
  try:
    session.request('GET', 'https://aita.yanhekt.cn/')
  finally:
    client.CAMPUS.clear()
  assert sent == [True, False]
