"""Recordings and slides fetched through AutoSlides, played by the stand-in in fake_autoslides.py."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import make_video, slide_image
from test_online import FakeClient

import download
from workspace import holdings, lecture_entry, read_index, read_slides, recording

BEIJING = timezone(timedelta(hours=8))
CODE = '20262027110000000101'  # FakeClient's registrar code


def beijing(epoch):
  return datetime.fromtimestamp(epoch, BEIJING).strftime('%Y-%m-%d %H:%M:%S')


@pytest.fixture
def world(course, tmp_path, monkeypatch):
  """The stand-in app's world: the course on Yanhekt with a session for each replay but the dropped one, and the
  files a download copies."""
  monkeypatch.setattr(download, 'POLL', 0)
  replays = FakeClient().course('10001')['replays']
  templates = tmp_path / 'templates'
  video = make_video(templates / 'lecture.mp4', [slide_image(0)], seconds=2)
  slide_image(1).save(templates / 'slide.png')
  state = {
    'output': str(tmp_path / 'autoslides-output'),
    'courses': {CODE: '70101'},
    'sessions': {'70101': [{'id': str(900001 + n), 'started_at': beijing(row['begin'])} for n, row in enumerate(replays[:2])]},
    'templates': {'camera': str(video), 'screen': str(video), 'slide': str(templates / 'slide.png')},
  }  # fmt: skip
  path = Path(os.environ['FAKE_AUTOSLIDES_STATE'])

  def change(**values):
    path.write_text(json.dumps({**json.loads(path.read_text(encoding='utf-8')), **values}), encoding='utf-8')

  path.write_text(json.dumps(state), encoding='utf-8')
  return change


def fetch(course, **options):
  values = {'videos': ['classroom'], 'slides': True, 'transcribe': False} | options
  return download.download(FakeClient(), FakeClient().course('10001'), course, **values)


def test_sync_pairs_the_lectures_and_records_what_autoslides_fetched(world, course, capsys):
  assert fetch(course) == 0
  index = read_index(course)
  assert index['autoslides_course_id'] == '70101'
  lecture = course / '09月23日第3-5节'
  files = lecture_entry(lecture)['files']
  assert Path(files['classroom']).name == 'camera_lecture900001__c70101s900001.mp4'
  assert Path(files['screen']).name == 'screen_lecture900001__c70101s900001.mp4'
  assert recording(lecture, 'classroom') == Path(files['classroom'])
  assert [Path(slide['file']).name for slide in read_slides(lecture)] == ['Slide_lecture900001_001.png']
  assert holdings(lecture_entry(lecture), lecture) == '00:00:30, 1 slide, classroom video, screen video'
  out = capsys.readouterr().out
  assert '示例课程/09月23日第3-5节: classroom video queued' in out
  assert '示例课程/09月23日第3-5节: screen video completed, slides completed 100%' in out
  assert fetch(course) == 0
  assert '示例课程/09月23日第3-5节: classroom video skipped' in capsys.readouterr().out


def test_each_classroom_recording_is_transcribed_once_it_is_there(world, course, monkeypatch):
  done = []
  monkeypatch.setattr(download, 'transcribe_lecture', lambda lecture, overwrite, say: done.append(lecture.name))
  assert fetch(course, transcribe=True, slides=False) == 0
  assert sorted(done) == ['09月23日第3-5节', '09月30日第3-5节']


def test_a_lecture_yanhekt_lacks_fails_alone(world, course, capsys):
  world(
    sessions={'70101': [{'id': '900001', 'started_at': beijing(FakeClient().course('10001')['replays'][0]['begin'])}]}
  )
  assert fetch(course) == 1
  assert '示例课程/09月30日第3-5节: not on Yanhekt, where AutoSlides fetches recordings from' in capsys.readouterr().out
  assert 'files' in lecture_entry(course / '09月23日第3-5节')


def test_a_course_yanhekt_does_not_list_stops_the_sync(world, course):
  world(courses={})
  with pytest.raises(download.autoslides.AutoSlidesError, match='Yanhekt lists 0 courses under registrar code'):
    fetch(course)


def test_a_download_the_panel_forgets_fails_with_what_to_do(world, course, capsys):
  world(forget=True)
  assert fetch(course, slides=False) == 2
  assert 'classroom video left the AutoSlides Download panel; run `sync` again' in capsys.readouterr().out


def test_without_auto_extraction_the_sync_says_so_and_does_not_wait(world, course, capsys):
  world(extract=False)
  assert fetch(course) == 0
  assert 'AutoSlides does not extract slides after downloads' in capsys.readouterr().out
  assert read_slides(course / '09月23日第3-5节') == []


def test_the_sync_does_not_wait_for_autoslides_to_finish_filtering_the_slides(world, course, monkeypatch):
  world(extraction_state='post_processing')
  looks = []
  panel = download.autoslides.panel

  def counted():
    looks.append(1)
    if len(looks) > 50:
      raise RuntimeError('the sync kept waiting')
    return panel()

  monkeypatch.setattr(download.autoslides, 'panel', counted)
  assert fetch(course) == 0
  assert [Path(slide['file']).name for slide in read_slides(course / '09月23日第3-5节')] == [
    'Slide_lecture900001_001.png'
  ]


def test_slide_commands_wait_while_autoslides_filters_the_slides(world, course):
  import yanhe_agent

  world(extraction_state='post_processing')
  fetch(course)
  lecture = course / '09月23日第3-5节'
  with pytest.raises(ValueError, match='AutoSlides is still filtering the slides; run this again in a few minutes'):
    yanhe_agent.settled(lecture)
  world(extraction_state='completed', rows=[])
  fetch(course, overwrite=True)
  yanhe_agent.settled(lecture)
