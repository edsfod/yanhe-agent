"""Transcription with BCut replaced by a stand-in, so that no audio leaves the machine."""

from typing import ClassVar

import pytest
from conftest import make_video, slide_image

import client
import transcribe
import transcript
from workspace import read_json, subtitles

ROWS = [(2.0, 3.0, '下面 点名'), (0.5, 1.0, '上课')]


class FakeBCut:
  """Counts uploads; `results` lists what each `result` call returns or raises, then the rows."""

  uploads = 0
  results: ClassVar[list] = []

  def __init__(self, say):
    pass

  def upload(self, audio):
    assert audio.stat().st_size
    FakeBCut.uploads += 1
    return f'resource-{FakeBCut.uploads}'

  def submit(self, resource):
    return f'task-{resource}'

  def result(self, task):
    outcome = FakeBCut.results.pop(0) if FakeBCut.results else ROWS
    if isinstance(outcome, Exception):
      raise outcome
    return outcome


@pytest.fixture
def lecture(course, monkeypatch):
  monkeypatch.setattr(transcribe, 'BCut', FakeBCut)
  monkeypatch.setattr(transcribe.time, 'sleep', lambda seconds: None)
  FakeBCut.uploads, FakeBCut.results = 0, []
  lecture = course / '09月30日第3-5节'
  make_video(lecture / '录播-教室.mp4', [slide_image(0)], seconds=4)
  return lecture


def test_transcribe_writes_the_srt_keeps_the_raw_result_and_applies_corrections(lecture):
  transcript.add_corrections(lecture.parent, [('点名', '签到')])
  FakeBCut.results = [None, None]  # still running twice
  said = []
  assert transcribe.transcribe_lecture(lecture, False, said.append) is False
  assert said[-1] == '2 segments in 字幕.srt'
  assert transcript.read(lecture) == [(0.5, 1.0, '上课'), (2.0, 3.0, '下面 签到')]
  assert read_json(lecture / '.cache' / 'bcut.json', None) == [[2.0, 3.0, '下面 点名'], [0.5, 1.0, '上课']]
  assert not (lecture / '.cache' / 'bcut-task.json').exists()


def test_a_stopped_run_waits_for_the_same_task_instead_of_uploading_again(lecture):
  FakeBCut.results = [None, ConnectionError('network down')]
  assert transcribe.transcribe_lecture(lecture, False, lambda _: None) is True
  assert read_json(lecture / '.cache' / 'bcut-task.json', {})['task'] == 'task-resource-1'
  assert transcribe.transcribe_lecture(lecture, False, lambda _: None) is False
  assert FakeBCut.uploads == 1
  assert subtitles(lecture).exists()


def test_a_task_bcut_no_longer_knows_is_started_again(lecture):
  FakeBCut.results = [ConnectionError('network down')]
  transcribe.transcribe_lecture(lecture, False, lambda _: None)
  FakeBCut.results = [RuntimeError('BCut /task/result: task not found')]
  assert transcribe.transcribe_lecture(lecture, False, lambda _: None) is False
  assert FakeBCut.uploads == 2


def test_a_saved_task_for_other_audio_is_not_resumed(lecture):
  FakeBCut.results = [ConnectionError('network down')]
  transcribe.transcribe_lecture(lecture, False, lambda _: None)
  (lecture / '.cache' / 'audio.m4a').write_bytes(b'other audio')
  transcribe.transcribe_lecture(lecture, False, lambda _: None)
  assert FakeBCut.uploads == 2


def test_a_rejected_track_is_encoded_and_sent_again(lecture):
  FakeBCut.results = [transcribe.Rejected('BCut task failed: bad audio')]
  said = []
  assert transcribe.transcribe_lecture(lecture, False, said.append) is False
  assert 'BCut rejected the sound track' in said
  assert FakeBCut.uploads == 2


def test_an_existing_transcript_is_kept(lecture):
  transcribe.transcribe_lecture(lecture, False, lambda _: None)
  said = []
  assert transcribe.transcribe_lecture(lecture, False, said.append) is False
  assert said == ['kept existing transcript']
  assert FakeBCut.uploads == 1


def test_a_deleted_srt_is_made_again_from_the_raw_result(lecture):
  transcribe.transcribe_lecture(lecture, False, lambda _: None)
  subtitles(lecture).unlink()
  transcribe.transcribe_lecture(lecture, False, lambda _: None)
  assert FakeBCut.uploads == 1
  assert subtitles(lecture).exists()


def test_overwrite_uploads_again(lecture):
  transcribe.transcribe_lecture(lecture, False, lambda _: None)
  transcribe.transcribe_lecture(lecture, True, lambda _: None)
  assert FakeBCut.uploads == 2


def test_no_recording(course):
  said = []
  assert transcribe.transcribe_lecture(course / '09月23日第3-5节', False, said.append) is True
  assert said == ['no recording to transcribe; run `sync` first']


def test_no_transcription_without_consent(lecture):
  client.save_settings({})
  said = []
  assert transcribe.transcribe_lecture(lecture, False, said.append) is True
  assert said[0].startswith("transcription failed: Transcription needs the user's consent first")
  assert FakeBCut.uploads == 0
