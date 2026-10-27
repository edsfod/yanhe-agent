import re
from pathlib import Path

import pytest
from conftest import make_video, record_files, slide_image, streams

import transcript
import workspace
from workspace import read_json, subtitles, timeline, write_json, write_tsv

ROWS = [(1.0, 4.5, '今天讲 流水线'), (12.0, 15.0, '下面点名'), (21.0, 25.0, '作业是第三章'), (26.0, 28.0, '下课')]


def test_write_makes_the_srt_and_the_text_view(transcribed):
  assert (
    subtitles(transcribed)
    .read_text(encoding='utf-8')
    .startswith('1\n00:00:01,000 --> 00:00:04,500\n今天讲 流水线\n\n2\n00:00:12,000 --> 00:00:15,000\n下面点名\n\n')
  )
  assert (transcribed / '字幕.txt').read_text(encoding='utf-8') == (
    '[00:00:01] 今天讲 流水线\n[00:00:12] 下面点名\n[00:00:21] 作业是第三章\n[00:00:26] 下课\n'
  )


def test_parse_srt_reads_what_editors_write():
  text = '﻿1\r\n00:00:01,000 --> 00:00:02,500\r\n第一行\r\n第二行\r\n\r\n00:01:02.5 --> 00:01:03.25\r\n无编号\r\n\r\n'
  assert transcript.parse_srt(text) == [(1.0, 2.5, '第一行\n第二行'), (62.5, 63.25, '无编号')]
  assert transcript.parse_srt(transcript.format_srt(ROWS)) == ROWS


def test_read_refreshes_the_text_view_after_an_edit(transcribed):
  srt = subtitles(transcribed)
  srt.write_text(srt.read_text(encoding='utf-8').replace('下面点名', '下面签到\n请举手'), encoding='utf-8')
  assert transcript.read(transcribed)[1] == (12.0, 15.0, '下面签到\n请举手')
  assert '[00:00:12] 下面签到 请举手\n' in (transcribed / '字幕.txt').read_text(encoding='utf-8')


def test_migrate_turns_the_old_tsv_into_the_srt(course):
  lecture = course / '09月30日第3-5节'
  write_tsv(timeline(lecture, 'transcript'), ROWS)
  assert workspace.transcribed(lecture)
  assert transcript.read(lecture) == ROWS
  assert subtitles(lecture).exists()
  assert not timeline(lecture, 'transcript').exists()
  assert (lecture / '.cache' / 'transcript.old.tsv').exists()


def test_corrections_are_kept_once_in_order(course):
  assert transcript.corrections(course) == []
  assert transcript.add_corrections(course, [['流水', '流水线'], ['点名', '签到'], ['流水', '流水线']]) == [
    ('流水', '流水线'),
    ('点名', '签到'),
  ]
  assert transcript.add_corrections(course, [('点名', '签到'), ('乐雪', '乐学')]) == [('乐雪', '乐学')]
  assert transcript.corrections(course) == [('流水', '流水线'), ('点名', '签到'), ('乐雪', '乐学')]
  assert (course / '纠错.tsv').read_text(encoding='utf-8').splitlines()[0] == 'wrong\tright'


def test_fix_applies_pairs_in_order(transcribed):
  assert transcript.fix(transcribed, [('流水', '流水线'), ('流水线线', '流水线'), ('不存在', 'x')]) == ([1, 1, 0], [])
  assert transcript.read(transcribed)[0][2] == '今天讲 流水线'


def test_fix_keeps_hand_edits(transcribed):
  srt = subtitles(transcribed)
  srt.write_text(srt.read_text(encoding='utf-8').replace('下课', '下课了'), encoding='utf-8')
  transcript.fix(transcribed, [('点名', '签到')])
  assert [text for _, _, text in transcript.read(transcribed)][1:] == ['下面签到', '作业是第三章', '下课了']


def test_adopt_tidies_fresh_rows_and_applies_the_course_corrections(course):
  lecture = course / '09月30日第3-5节'
  transcript.add_corrections(course, [('点名', '签到')])
  assert transcript.adopt(lecture, [[12, 15, ' 下面  点名 '], [1, 2, '开始'], [3, 4, '  ']]) == 2
  assert transcript.read(lecture) == [(1.0, 2.0, '开始'), (12.0, 15.0, '下面 签到')]


def slide(lecture, number):
  """The line `query` heads a run of segments with when this slide is on screen."""
  return f'slide {number:03}: {workspace.read_slides(lecture)[number - 1]["file"]}'


def test_query_heads_each_run_with_its_slide(transcribed):
  assert list(transcript.query(transcribed)) == [
    slide(transcribed, 1),
    '[00:00:01] 今天讲 流水线',
    slide(transcribed, 2),
    '[00:00:12] 下面点名',
    slide(transcribed, 3),
    '[00:00:21] 作业是第三章',
    '[00:00:26] 下课',
  ]


def test_query_filters_by_pattern_and_time(transcribed):
  assert list(transcript.query(transcribed, pattern=re.compile('点名|作业'))) == [
    slide(transcribed, 2),
    '[00:00:12] 下面点名',
    slide(transcribed, 3),
    '[00:00:21] 作业是第三章',
  ]
  assert list(transcript.query(transcribed, start=14, end=21)) == [
    slide(transcribed, 2),
    '[00:00:12] 下面点名',
    slide(transcribed, 3),
    '[00:00:21] 作业是第三章',
  ]


def test_query_reads_the_fast_transcript(transcribed):
  write_tsv(timeline(transcribed, 'fast-transcript'), [(11, 12, '快速 字幕')])
  assert list(transcript.query(transcribed, 'fast-transcript')) == [slide(transcribed, 2), '[00:00:11] 快速 字幕']


def test_query_without_slides(transcribed):
  folder = workspace.lecture_entry(transcribed)['files']['slides']
  (Path(folder) / 'timeline.json').unlink()
  assert list(transcript.query(transcribed, pattern=re.compile('下课'))) == ['[00:00:26] 下课']


@pytest.fixture
def recorded(transcribed):
  make_video(transcribed / '录播-教室.mp4', [slide_image(0), slide_image(1)], seconds=3)
  return transcribed


def subtitle_tracks(video):
  return [line for line in streams(video) if 'Subtitle' in line]


def test_embed_adds_one_subtitle_track_and_marks_the_recording(recorded):
  video = recorded / '录播-教室.mp4'
  assert not transcript.embedded(recorded, video)
  transcript.embed(recorded, video, subtitles(recorded).read_text(encoding='utf-8'), transcript=True)
  assert [re.search(r'\((\w+)\): Subtitle: (\w+)', line).groups() for line in subtitle_tracks(video)] == [
    ('zho', 'mov_text')
  ]
  assert any('Audio' in line for line in streams(video))
  assert transcript.embedded(recorded, video)
  transcript.embed(recorded, video, subtitles(recorded).read_text(encoding='utf-8'), transcript=True)
  assert len(subtitle_tracks(video)) == 1  # replaced rather than added


def test_an_edited_transcript_or_a_replaced_recording_needs_embedding_again(recorded):
  video = recorded / '录播-教室.mp4'
  transcript.embed(recorded, video, subtitles(recorded).read_text(encoding='utf-8'), transcript=True)
  transcript.fix(recorded, [('点名', '签到')])
  assert not transcript.embedded(recorded, video)
  transcript.embed(recorded, video, subtitles(recorded).read_text(encoding='utf-8'), transcript=True)
  make_video(video, [slide_image(2)], seconds=2)
  assert not transcript.embedded(recorded, video)


def test_embedding_another_file_clears_the_mark(recorded):
  video = recorded / '录播-教室.mp4'
  transcript.embed(recorded, video, subtitles(recorded).read_text(encoding='utf-8'), transcript=True)
  transcript.embed(recorded, video, '1\n00:00:00,000 --> 00:00:01,000\n别的\n\n')
  assert len(subtitle_tracks(video)) == 1
  assert not transcript.embedded(recorded, video)
  assert read_json(transcript.marks(recorded), None) == {}


def test_marks_live_in_the_cache(recorded):
  write_json(transcript.marks(recorded), {'other.mp4': {}})
  transcript.embed(
    recorded, recorded / '录播-教室.mp4', subtitles(recorded).read_text(encoding='utf-8'), transcript=True
  )
  assert set(read_json(transcript.marks(recorded), {})) == {'other.mp4', '录播-教室.mp4'}


def test_a_recording_in_the_autoslides_folder_is_embedded_with_the_lecture_transcript(transcribed, tmp_path):
  video = make_video(tmp_path / 'autoslides-output' / 'camera_示例课程__c70101s900001.mp4', [slide_image(0)], seconds=2)
  record_files(transcribed, classroom=video)
  transcript.embed_all([transcribed])
  assert [('mov_text' in line, '(zho)' in line) for line in subtitle_tracks(video)] == [(True, True)]
  assert transcript.embedded(transcribed, video)
  assert (transcribed / '.cache' / 'embedded.json').exists()
