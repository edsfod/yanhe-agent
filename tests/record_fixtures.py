"""Record the platform's answers into tests/fixtures/ with personal data removed, using the saved sign-in.

Run from the repository root:
uv run --no-project --with-requirements skills/yanhe-agent/scripts/yanhe_agent.py python tests/record_fixtures.py <course ID>
with the ID of a course the user does not take. Check the files before committing them: the repository is public.
"""

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).parents[1] / 'skills' / 'yanhe-agent' / 'scripts'))

from client import Yanhekt

FIXTURES = Path(__file__).with_name('fixtures')
PERSONAL = re.compile(
  r'phone|mobile|tel|mail|card|token|password|name|teacher|avatar|student|user|nick|ip|address', re.IGNORECASE
)
COURSE = {
  'title',
  'sub_title',
  'term_name',
  'TermName',
  'Title',
  'kkxy_name',
  'display_name',
}  # course data, not people
TEXT = {'Text', 'TransText', 'trans_content', 'transcript', 'text'}  # what was said in class
CHINESE = re.compile('[一-鿿]')
LONG = 3  # items kept of each list


def scrub(value, key='', keep=COURSE):
  """`value` with personal fields, transcript text, URL paths and queries replaced, and lists cut to LONG items;
  course fields outside `keep` are replaced too."""
  if isinstance(value, dict):
    return {name: scrub(item, name, keep) for name, item in value.items()}
  if isinstance(value, list):
    return [scrub(item, key, keep) for item in value[:LONG]]
  if isinstance(value, str):
    if value.startswith(('http://', 'https://')):
      parts = urlsplit(value)
      play = 'play/' if '/play/' in parts.path else ''
      return f'{parts.scheme}://{parts.netloc}/{play}redacted{Path(parts.path).suffix}'
    if key in TEXT:
      return '片段'
    if key in COURSE - keep:
      return '某课程'
    if PERSONAL.search(key) and key not in COURSE:
      return '某人' if CHINESE.search(value) else 'redacted'
  if isinstance(value, (int, float)) and not isinstance(value, bool) and PERSONAL.search(key) and key not in COURSE:
    return 0
  return value


def hide_course(row, number):
  """Replace the codes that tell which course a row of the user's course list is, as which courses the user takes
  is personal too: the platform's IDs, the college and the timetable's course and class codes."""
  row.update(Id=number, zhiyuan_id=str(number), course_exist=number, KkxyCode='0')
  if row.get('information'):
    codes = json.loads(row['information']) | {'kcdm': '0', 'kcwybm': '0'}
    row['information'] = json.dumps(codes, separators=(',', ':'))


def save(name, body, keep=COURSE):
  FIXTURES.mkdir(exist_ok=True)
  path = FIXTURES / f'{name}.json'
  path.write_text(json.dumps(scrub(body, keep=keep), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
  print(path.relative_to(FIXTURES.parent.parent))


def main(course_id):
  client = Yanhekt()
  if not client.authenticate():
    raise SystemExit('Not signed in; run `login` first')
  mine = client.get_json('/vlabpassportapi/v1/account-profile/course', force_mycourse=1, nowpage=1, **{'per-page': 3})
  for number, row in enumerate(mine['params']['result']['data'], 100001):
    hide_course(row, number)
  save('courses', mine, keep={'TermName'})
  tenant = client.profile['tenant_id']
  search = {'tenant': tenant, 'title': '操作系统', 'order': '', 'page': 1, 'per_page': 3}
  save('search', client.get_json('/courseapi/v3/multi-search/get-course-list', {'tenant-id': str(tenant)}, **search))
  save('course', client.get_json('/courseapi/v3/multi-search/get-course-detail', course_id=course_id))
  sub_id = client.course(course_id)['replays'][0]['sub_id']
  save('lecture', client.get_json('/courseapi/v3/portal-home-setting/get-sub-info', course_id=course_id, sub_id=sub_id))
  save('subtitles', client.get_json('/courseapi/v3/web-socket/search-trans-result', sub_id=sub_id, format='json'))


if __name__ == '__main__':
  main(sys.argv[1])
