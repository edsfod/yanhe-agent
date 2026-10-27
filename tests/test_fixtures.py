"""The client's parsing of answers recorded from the platform by record_fixtures.py, with personal data removed."""

import json
from pathlib import Path

import pytest

from client import Yanhekt

FIXTURES = Path(__file__).with_name('fixtures')
PATHS = {
  '/vlabpassportapi/v1/account-profile/course': 'courses',
  '/courseapi/v3/multi-search/get-course-list': 'search',
  '/courseapi/v3/multi-search/get-course-detail': 'course',
  '/courseapi/v3/portal-home-setting/get-sub-info': 'lecture',
  '/courseapi/v3/web-socket/search-trans-result': 'subtitles',
}


@pytest.fixture
def yanhekt(monkeypatch):
  """A client that answers from the recorded files; later pages of the course list are empty."""
  instance = Yanhekt.__new__(Yanhekt)
  instance.profile = {'id': 1, 'tenant_id': 1, 'phone': '0'}

  def get_json(path, headers=None, **params):
    body = json.loads((FIXTURES / f'{PATHS[path]}.json').read_text(encoding='utf-8'))
    if PATHS[path] == 'courses' and params['nowpage'] > 1:
      body['params']['result']['data'] = []
    return body

  monkeypatch.setattr(instance, 'get_json', get_json)
  return instance


def test_courses(yanhekt):
  courses = yanhekt.courses()
  assert [course['id'] for course in courses] == ['100001', '100002', '100003']
  assert courses[0] == {'id': '100001', 'title': '某课程', 'teacher': '某人', 'term': '2026-2027-1'}


def test_search(yanhekt):
  total, courses = yanhekt.search('操作系统')
  assert total == 38
  assert courses[0] == {
    'id': '102122',
    'title': '操作系统',
    'teacher': '某人',
    'term': '2026-2027-1',
    'college': '计算机学院',
  }


def test_course_lists_the_replays_in_class_order(yanhekt):
  course = yanhekt.course('102122')
  assert {key: course[key] for key in ('id', 'title', 'term')} == {
    'id': '102122',
    'title': '操作系统',
    'term': '2026-2027-1',
  }
  titles = [replay['title'] for replay in course['replays']]
  assert titles[:3] == ['2026-09-25第8-10节', '2026-10-02第8-10节', '2026-10-09第8-10节']
  assert titles == sorted(titles)
  assert course['replays'][0]['sub_id'] == '1544498'


def test_lecture_has_a_classroom_and_a_screen_recording(yanhekt):
  info = yanhekt.lecture('102122', '1544498')
  assert info == {'duration_s': 8929.0, 'views': ['classroom', 'screen']}


def test_subtitles(yanhekt):
  rows = yanhekt.subtitles('1544498')
  assert rows[:2] == [(66.0, 67.0, '片段'), (69.0, 70.0, '片段')]
  assert all(start <= end for start, end, _ in rows)


def test_the_fixtures_carry_no_personal_fields():
  text = ''.join(path.read_text(encoding='utf-8') for path in FIXTURES.glob('*.json'))
  for key in ('"phone": "1', '"mobile": "1', '"Realname": "', '"realname": "'):
    for value in text.split(key)[1:]:
      assert value.startswith(('某人', '0', 'redacted')), key
