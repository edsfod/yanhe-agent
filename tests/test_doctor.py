"""The `doctor` checks."""

import requests
from test_online import FakeClient

import doctor


def offline(monkeypatch, answers=True, campus=False, app=True):
  def get(url, **options):
    if not answers:
      raise requests.ConnectionError()

  monkeypatch.setattr(doctor.requests, 'get', get)
  monkeypatch.setattr(doctor, 'on_campus', lambda: campus)
  monkeypatch.setattr(doctor.autoslides, 'command', lambda: ['C:/Program Files/AutoSlides/AutoSlides.exe'])
  monkeypatch.setattr(doctor.autoslides, 'running', lambda: app)


def test_doctor_without_a_sign_in(monkeypatch):
  offline(monkeypatch)
  lines = list(doctor.checks(lambda: None))
  assert [(level, subject) for level, subject, _ in lines] == [
    ('ok', 'python'),
    ('ok', 'ffmpeg'),
    ('ok', 'network'),
    ('ok', 'network'),
    ('ok', 'network'),
    ('ok', 'campus network'),
    ('ok', 'AutoSlides'),
    ('warn', 'login'),
  ]
  assert lines[-1][2] == 'not signed in; run `login`'


def test_doctor_reads_the_platform_when_signed_in(monkeypatch):
  offline(monkeypatch, campus=True)
  lines = [f'{level} {subject}: {text}' for level, subject, text in doctor.checks(FakeClient)]
  assert lines[5:] == [
    'ok campus network: on it, platform requests go to the campus address',
    'ok AutoSlides: open, C:/Program Files/AutoSlides/AutoSlides.exe',
    'ok login: signed in with the Yanhe 2.0 sign-in AutoSlides keeps',
    'ok platform: course list read, 1 course',
    'ok platform: 示例课程 read, 3 replays, the last with no recordings',
  ]


def test_doctor_reports_failures(monkeypatch, capsys):
  offline(monkeypatch, answers=False)
  assert doctor.run(lambda: None) is False
  out = capsys.readouterr().out
  assert 'fail network: Yanhekt does not answer: Network error: ConnectionError' in out


def test_doctor_asks_for_autoslides_to_be_opened(monkeypatch):
  offline(monkeypatch, app=False)
  lines = [f'{level} {subject}: {text}' for level, subject, text in doctor.checks(lambda: None)]
  assert (
    'warn AutoSlides: closed; open it from its shortcut before `sync` (C:/Program Files/AutoSlides/AutoSlides.exe)'
    in lines
  )
