import pytest
from conftest import read_json

import workspace
from workspace import (
  clock,
  count,
  course_folder,
  drop,
  file_name,
  holdings,
  label,
  lecture_id,
  lectures,
  merge_lectures,
  parse_time,
  read_index,
  read_tsv,
  replacing,
  select,
  stamp,
  sync_index,
  write_tsv,
)


@pytest.mark.parametrize(
  ('title', 'expected'),
  [
    ('2026-10-8第6-7节', '10月08日第6-7节'),
    ('2026-9-23第3-5节', '09月23日第3-5节'),
    ('习题课: 第1/2章?', '习题课_ 第1_2章_'),
    (' 期末答疑. ', '期末答疑'),
  ],
)
def test_lecture_id(title, expected):
  assert lecture_id(title) == expected


def test_file_name_replaces_reserved_characters():
  assert file_name('a<b>c:"d"/e\\f|g?h*\x01') == 'a_b_c__d__e_f_g_h__'


def test_course_folder_adds_the_id_when_another_course_has_the_title(tmp_path):
  course = {'id': '1', 'title': '数学分析'}
  assert course_folder(tmp_path, course) == tmp_path / '数学分析'
  sync_index({**course, 'teacher': '甲', 'term': 't', 'code': '', 'replays': []}, tmp_path / '数学分析')
  assert course_folder(tmp_path, course) == tmp_path / '数学分析'
  assert course_folder(tmp_path, {'id': '2', 'title': '数学分析'}) == tmp_path / '数学分析 2'


def test_merge_lectures_keeps_recorded_entries():
  index = {'lectures': [{'id': '旧名', 'sub_id': 'a', 'duration_s': 5.0, 'dropped': True}]}
  course = {
    'replays': [
      {'sub_id': 'a', 'title': '2026-9-1第1节', 'begin': 1},
      {'sub_id': 'b', 'title': '2026-9-8第1节', 'begin': 8},
    ]
  }
  assert merge_lectures(course, index) == [
    {'id': '旧名', 'sub_id': 'a', 'duration_s': 5.0, 'dropped': True, 'begin': 1},
    {'id': '09月08日第1节', 'sub_id': 'b', 'begin': 8},
  ]


def test_sync_index_refuses_another_course(course):
  with pytest.raises(ValueError, match='belongs to course 10001'):
    sync_index({'id': '9', 'title': 'x', 'teacher': 'y', 'term': 'z', 'replays': []}, course)


def test_select(course):
  index = read_index(course)
  assert [lecture['id'] for lecture in select(index)] == ['09月23日第3-5节', '09月30日第3-5节']
  assert [lecture['id'] for lecture in select(index, latest=1)] == ['09月30日第3-5节']
  assert [lecture['id'] for lecture in select(index, ['10月01日第3-5节'])] == ['10月01日第3-5节']
  with pytest.raises(ValueError, match='Unknown lecture 11月01日'):
    select(index, ['11月01日'])


def test_lectures_expands_course_folders(course):
  lecture = course / '09月23日第3-5节'
  assert lectures([course]) == [lecture, course / '09月30日第3-5节']
  assert lectures([course], latest=1) == [course / '09月30日第3-5节']
  assert lectures([lecture]) == [lecture]
  with pytest.raises(ValueError, match='not a course or lecture folder'):
    lectures([course / 'missing'])


def test_drop_deletes_the_folder_and_marks_it(course):
  lecture = course / '09月30日第3-5节'
  (lecture / '笔记.md').write_text('x', encoding='utf-8')
  drop(lecture)
  assert not lecture.exists()
  assert read_index(course)['lectures'][1]['dropped'] is True
  with pytest.raises(ValueError, match='not a lecture'):
    drop(course / '12月01日')


def test_holdings(transcribed):
  entry = read_index(transcribed.parent)['lectures'][0]
  assert holdings(entry, transcribed) == '00:00:30, 3 slides, transcript'
  write_tsv(transcribed / '.cache' / 'fast-transcript.tsv', [(0, 1, '一')])
  (transcribed / '录播-教室.mp4').write_bytes(b'')
  (transcribed / '笔记.md').write_text('', encoding='utf-8')
  assert holdings(entry, transcribed) == (
    '00:00:30, fast transcript (1 segment), 3 slides, classroom video, transcript, notes'
  )
  assert holdings({'dropped': True}, transcribed) == 'dropped'
  assert holdings({}, transcribed.parent / '09月30日第3-5节') == 'nothing saved'


def test_tsv_round_trip_sorts_collapses_and_drops_empty_text(tmp_path):
  path = tmp_path / 'a.tsv'
  assert write_tsv(path, [(5, 6, ' b \n c '), (1, 2.5, 'a'), (3, 4, '  ')]) == 2
  assert path.read_text(encoding='utf-8') == 'start_s\tend_s\ttext\n1.000\t2.500\ta\n5.000\t6.000\tb c\n'
  assert read_tsv(path) == [(1.0, 2.5, 'a'), (5.0, 6.0, 'b c')]


def test_replacing_leaves_the_target_alone_when_the_block_fails(tmp_path):
  path = tmp_path / 'a.txt'
  path.write_text('old', encoding='utf-8')
  with pytest.raises(RuntimeError), replacing(path) as part:
    part.write_text('new', encoding='utf-8')
    raise RuntimeError
  assert path.read_text(encoding='utf-8') == 'old'
  assert list(tmp_path.iterdir()) == [path]
  with replacing(path) as part:
    assert part.name == 'a.part.txt'
    part.write_text('new', encoding='utf-8')
  assert path.read_text(encoding='utf-8') == 'new'


def test_write_json_is_utf8_and_indented(tmp_path):
  workspace.write_json(tmp_path / 'a.json', {'名': 1})
  assert (tmp_path / 'a.json').read_text(encoding='utf-8') == '{\n  "名": 1\n}\n'
  assert read_json(tmp_path / 'a.json') == {'名': 1}


@pytest.mark.parametrize(
  ('seconds', 'separator', 'expected'),
  [(0, '', '00:00:00'), (3723.4567, '', '01:02:03'), (3723.4567, ',', '01:02:03,457'), (59.9996, ',', '00:01:00,000')],
)
def test_clock(seconds, separator, expected):
  assert clock(seconds, separator) == expected


def test_parse_time_and_stamp():
  assert parse_time('150') == 150
  assert parse_time('2:30') == 150
  assert parse_time('1:02:30.5') == 3750.5
  assert stamp(150) == '02:30'
  assert stamp(3750) == '01:02:30'


def test_count_and_label(course):
  assert count(1, 'lecture') == '1 lecture'
  assert count(0, 'slide') == '0 slides'
  assert label(course / '09月23日第3-5节') == '示例课程/09月23日第3-5节'


def test_the_ticker_reports_soon_after_a_change_and_then_on_a_timer(monkeypatch):
  monkeypatch.setattr(workspace.Ticker, 'SLOWEST', 0.2)
  monkeypatch.setattr(workspace.Ticker, 'FASTEST', 0.05)
  said = []
  with workspace.Ticker(said.append, 'uploading') as ticker:
    workspace.time.sleep(0.1)
    ticker.set('waiting')
    workspace.time.sleep(0.5)
  assert said[0] == 'uploading'
  assert 'waiting' in said and said.count('waiting') >= 2  # once for the change, again every SLOWEST seconds
