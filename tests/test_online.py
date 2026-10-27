"""The commands that go online, with a stand-in for the platform client and the sign-in page."""

import sys

import pytest
from conftest import make_video, slide_image

import download
import jobs
import server
import transcript
import yanhe_agent
from workspace import read_index, recording, timeline


class FakeClient:
  source = 'AutoSlides'

  def courses(self):
    return [{'id': '10001', 'title': '示例课程', 'teacher': '王老师', 'term': '2026-2027-1'}]

  def course(self, course_id):
    replays = [('200001', '2026-9-23第3-5节'), ('200002', '2026-9-30第3-5节'), ('200003', '2026-10-1第3-5节')]
    return {
      'id': course_id,
      'title': '示例课程',
      'teacher': '王老师',
      'term': '2026-2027-1',
      'code': '20262027110000000101',
      'replays': [{'sub_id': s, 'title': t, 'begin': 1790000000 + 604800 * n} for n, (s, t) in enumerate(replays)],
    }

  def lecture(self, course_id, sub_id):
    return {'duration_s': 5400.0, 'views': []}

  def subtitles(self, sub_id):
    if sub_id == '200001':
      return [(60.0 * minute, 60.0 * minute + 50, '讲课') for minute in range(30)]
    return [(10.0, 12.0, '喂喂')]


@pytest.fixture
def run(monkeypatch, capsys):
  def run(*args):
    monkeypatch.setattr(sys, 'argv', ['yanhe_agent.py', *map(str, args)])
    yanhe_agent.main()
    return capsys.readouterr().out.splitlines()

  return run


@pytest.fixture
def online(monkeypatch):
  monkeypatch.setattr(yanhe_agent, 'session', lambda: FakeClient())


def test_survey_tells_empty_lectures(run, online, course):
  assert run('survey', course) == [
    '示例课程/09月23日第3-5节: 00:00:30, 30 segments, 00:25:00 of speech, short recording',
    '示例课程/09月30日第3-5节: 00:00:30, 1 segment, 00:00:02 of speech, possibly empty, short recording',
  ]
  assert timeline(course / '09月30日第3-5节', 'fast-transcript').exists()


def test_survey_of_a_named_dropped_lecture_brings_it_back(run, online, course):
  assert run('survey', course / '10月01日第3-5节') == [
    '示例课程/10月01日第3-5节: 01:30:00, 1 segment, 00:00:02 of speech, possibly empty'
  ]
  assert 'dropped' not in read_index(course)['lectures'][2]


def test_commands_ask_for_login_when_signed_out(run, monkeypatch, course):
  monkeypatch.setattr(yanhe_agent, 'session', lambda: None)
  with pytest.raises(SystemExit, match='Not signed in; run `login`'):
    run('survey', course)
  with pytest.raises(SystemExit, match='Not signed in; run `login`'):
    run('sync', course)


def test_sync_starts_a_job_with_the_same_command(run, online, course, monkeypatch):
  started = []
  monkeypatch.setattr(jobs, 'start', lambda workspace, argv: started.append((workspace, argv)) or 7)
  monkeypatch.chdir(course.parent)
  assert run('sync', course, '--latest', 1, '--no-slides') == [
    'Job 7 started; `wait` waits for it and prints where each lecture stands'
  ]
  assert started == [(course.parent, ['sync', str(course), '--latest', '1', '--no-slides'])]


def test_sync_downloads_then_embeds_and_reports_what_each_lecture_holds(online, transcribed, monkeypatch, capsys):
  calls = []

  def fake_download(client, course, folder, names, **options):
    calls.append((names, options))
    make_video(recording(transcribed, 'classroom'), [slide_image(0)], seconds=2)
    return 0

  monkeypatch.setattr(download, 'download', fake_download)
  monkeypatch.setattr(sys, 'argv', ['yanhe_agent.py', '--job', '1', 'sync', str(transcribed)])
  yanhe_agent.sync(yanhe_agent.parse())
  assert calls == [
    (
      ['09月23日第3-5节'],
      {'latest': None, 'videos': ['classroom'], 'slides': True, 'transcribe': True, 'overwrite': False},
    )
  ]
  lines = [line.split(' ', 1)[1] for line in capsys.readouterr().out.splitlines()]
  assert lines == [
    '示例课程/09月23日第3-5节: embedded subtitles in 录播-教室.mp4',
    '示例课程/09月23日第3-5节: holds 00:00:30, 3 slides, classroom video, transcript',
  ]
  assert transcript.embedded(transcribed, recording(transcribed, 'classroom'))


def test_sync_reports_incomplete_lectures(online, course, monkeypatch):
  monkeypatch.setattr(download, 'download', lambda *args, **options: 2)
  monkeypatch.setattr(sys, 'argv', ['yanhe_agent.py', '--job', '1', 'sync', str(course)])
  with pytest.raises(SystemExit, match='2 lectures incomplete'):
    yanhe_agent.sync(yanhe_agent.parse())


def test_login_when_signed_in(run, online):
  assert run('login') == ['Signed in.']


def test_login_opens_the_page_and_returns(run, monkeypatch):
  monkeypatch.setattr(yanhe_agent, 'session', lambda: None)
  monkeypatch.setattr(server, 'open_page', lambda: 'http://127.0.0.1:5000/#token')
  assert run('login') == [
    'Sign-in page: http://127.0.0.1:5000/#token',
    'Give the user this address, then run `login --wait 90` to wait for them to sign in.',
  ]


def test_login_wait_ends_when_the_page_closes(run, monkeypatch):
  signed = iter([None, FakeClient()])
  monkeypatch.setattr(yanhe_agent, 'session', lambda: next(signed))
  monkeypatch.setattr(server, 'open_page', lambda: 'http://127.0.0.1:5000/#token')
  monkeypatch.setattr(server, 'live_page', lambda: None)
  assert run('login', '--wait', 30) == ['Sign-in page: http://127.0.0.1:5000/#token', 'Signed in.']


def test_login_wait_without_a_sign_in(run, monkeypatch):
  monkeypatch.setattr(yanhe_agent, 'session', lambda: None)
  monkeypatch.setattr(server, 'open_page', lambda: 'http://127.0.0.1:5000/#token')
  monkeypatch.setattr(server, 'live_page', lambda: None)
  with pytest.raises(SystemExit, match='closed without a sign-in'):
    run('login', '--wait', 30)


def test_live_page_needs_a_fresh_heartbeat(monkeypatch, tmp_path):
  monkeypatch.setattr(server, 'PAGE', tmp_path / 'signin.json')
  assert server.live_page() is None
  server.write_json(server.PAGE, {'url': 'http://x/#t', 'beat': server.time.time()})
  assert server.live_page() == 'http://x/#t'
  server.write_json(server.PAGE, {'url': 'http://x/#t', 'beat': server.time.time() - 600})
  assert server.live_page() is None


def test_config_records_and_withdraws_the_choice(run, config):
  assert run('config') == [
    "asr: bcut, BCut, Bilibili's speech recognition, which receives the sound track of each lecture"
  ]
  assert run('config', '--asr', 'none') == ['asr: none chosen; transcripts wait']
  assert run('config', '--asr', 'bcut')[0].startswith('asr: bcut')


def test_sync_and_transcribe_wait_for_consent(run, online, course, monkeypatch):
  run('config', '--asr', 'none')
  for command in (['sync', course], ['transcribe', course]):
    with pytest.raises(ValueError, match="needs the user's consent first.*`config --asr bcut`.*--no-transcribe"):
      run(*command)
  monkeypatch.setattr(jobs, 'start', lambda workspace, argv: 3)
  assert run('sync', course, '--no-transcribe')[0].startswith('Job 3 started')


def test_login_when_the_saved_password_signs_in_without_the_page(run, monkeypatch):
  signed = iter([None, FakeClient()])
  monkeypatch.setattr(yanhe_agent, 'session', lambda: next(signed))
  monkeypatch.setattr(server, 'open_page', lambda: None)
  assert run('login') == ['Signed in.']


def test_open_page_returns_none_when_the_page_process_signs_in_at_once(monkeypatch):
  class Ended:
    returncode = 0

    def poll(self):
      return 0

  monkeypatch.setattr(server, 'detached', lambda *args: Ended())
  assert server.open_page(timeout=5) is None
