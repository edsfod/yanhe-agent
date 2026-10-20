"""The offline commands, run through `main` as the agent runs them."""

import argparse
import sys

import pytest
from conftest import make_video, slide_image
from test_transcript import slide

import yanhe_agent


@pytest.fixture
def run(monkeypatch, capsys):
  """Run a command line and return its output lines."""

  def run(*args):
    monkeypatch.setattr(sys, 'argv', ['yanhe_agent.py', *map(str, args)])
    yanhe_agent.main()
    return capsys.readouterr().out.splitlines()

  return run


def test_status_lists_every_course_folder_here(run, transcribed, monkeypatch):
  monkeypatch.chdir(transcribed.parents[1])
  assert run('status') == [
    '示例课程: 2026-2027-1, 王老师, 3 lectures',
    '示例课程/09月23日第3-5节: 00:00:30, 3 slides, transcript',
    '示例课程/09月30日第3-5节: 00:00:30',
    '示例课程/10月01日第3-5节: dropped',
  ]


def test_status_without_course_folders(run, tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)
  with pytest.raises(ValueError, match='No course folders here'):
    run('status')


def test_query_groups_segments_under_their_lecture(run, transcribed):
  assert run('query', transcribed.parent, '-m', '点名|下课') == [
    '示例课程/09月23日第3-5节',
    slide(transcribed, 2),
    '[00:00:12] 下面点名',
    slide(transcribed, 3),
    '[00:00:26] 下课',
  ]
  assert run('query', transcribed, '-m', '考试') == ['No matching segments in 1 lecture']


def test_query_needs_a_transcript(run, course):
  with pytest.raises(ValueError, match='No transcripts; run `transcribe` first'):
    run('query', course)


def test_fix_records_the_corrections_and_applies_them_once(run, transcribed):
  course = transcribed.parent
  assert run('fix', course, '-r', '点名', '签到', '-r', '不存在', 'x') == [
    '示例课程: 2 corrections added to 纠错.tsv, which later transcripts follow too',
    '示例课程/09月23日第3-5节: 点名 -> 签到 1x',
    '示例课程/09月23日第3-5节: [00:00:12] 下面点名 -> 下面签到',
  ]
  assert run('fix', course, '-r', '点名', '签到') == ['示例课程: already in 纠错.tsv: 点名 -> 签到']


def test_fix_points_to_embed_when_recordings_carry_the_old_text(run, transcribed):
  make_video(transcribed / '录播-教室.mp4', [slide_image(0)], seconds=2)
  run('embed', transcribed)
  assert run('fix', transcribed.parent, '-r', '下课', '下课了')[-1] == (
    '示例课程: run `embed` to update the subtitles of 1 recording'
  )
  assert run('embed', transcribed)[0].endswith('embedded subtitles in 录播-教室.mp4')


def test_fix_takes_course_folders_only(run, transcribed):
  with pytest.raises(ValueError, match='corrections apply to a whole course'):
    run('fix', transcribed, '-r', 'a', 'b')


def test_embed_then_skip(run, transcribed):
  make_video(transcribed / '录播-教室.mp4', [slide_image(0)], seconds=2)
  assert run('embed', transcribed)[0].endswith('示例课程/09月23日第3-5节: embedded subtitles in 录播-教室.mp4')
  assert run('embed', transcribed)[0].endswith('示例课程/09月23日第3-5节: recordings already carry the transcript')


def test_drop(run, course):
  assert run('drop', course / '09月30日第3-5节') == ['示例课程/09月30日第3-5节: dropped']
  assert not (course / '09月30日第3-5节').exists()


def test_slide_commands(run, transcribed):
  sheets = [line.replace('\\', '/') for line in run('sheet', transcribed, '--columns', 2, '--rows', 1)]  # Windows
  assert sheets == [
    '示例课程/09月23日第3-5节: .cache/sheet-001-002.jpg',
    '示例课程/09月23日第3-5节: .cache/sheet-003-003.jpg',
  ]
  assert run('similar', transcribed) == ['示例课程/09月23日第3-5节: no similar slides']
  assert run('pdf', transcribed, 'slides/003.jpg', '1') == [
    '示例课程/09月23日第3-5节: wrote 课件.pdf, 2 pages: 003 001'
  ]


@pytest.mark.parametrize(
  ('value', 'expected'),
  [('10001', '10001'), ('https://www.yanhekt.cn/course/coursedetail?course_id=10001&x=1', '10001')],
)
def test_course_id(value, expected):
  assert yanhe_agent.course_id(value) == expected


def test_course_id_rejects_other_text():
  with pytest.raises(argparse.ArgumentTypeError, match='`courses` lists the IDs'):
    yanhe_agent.course_id('编译原理')


def test_by_course_maps_lectures_to_their_course(course):
  first, second = course / '09月23日第3-5节', course / '09月30日第3-5节'
  assert yanhe_agent.by_course([first, second]) == {course: ['09月23日第3-5节', '09月30日第3-5节']}
  assert yanhe_agent.by_course([first, course]) == {course: None}
  assert yanhe_agent.by_course([course / '11月01日第1节']) == {course: ['11月01日第1节']}
  with pytest.raises(ValueError, match='run `add` first'):
    yanhe_agent.by_course([course.parent / 'other' / 'x'])


def test_fix_shows_a_pair_that_also_hits_an_ordinary_word(run, transcribed):
  srt = transcribed / '字幕.srt'
  srt.write_text(srt.read_text(encoding='utf-8').replace('下课', '这种体验很好'), encoding='utf-8')
  assert run('fix', transcribed.parent, '-r', '这种体', '智能体')[1:] == [
    '示例课程/09月23日第3-5节: 这种体 -> 智能体 1x',
    '示例课程/09月23日第3-5节: [00:00:26] 这种体验很好 -> 智能体验很好',
  ]


def test_embed_after_an_edit_of_the_srt_updates_the_text_view(run, transcribed):
  make_video(transcribed / '录播-教室.mp4', [slide_image(0)], seconds=2)
  srt = transcribed / '字幕.srt'
  srt.write_text(srt.read_text(encoding='utf-8').replace('下面点名', '下面签到'), encoding='utf-8')
  run('embed', transcribed)
  assert '[00:00:12] 下面签到\n' in (transcribed / '字幕.txt').read_text(encoding='utf-8')
