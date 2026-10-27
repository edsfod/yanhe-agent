"""Checks of what the scripts depend on, one line each: `ok`, `warn` or `fail`, the subject, and what to do."""

import platform
import subprocess
import sys

import requests
from imageio_ffmpeg import get_ffmpeg_exe

import autoslides
import bcut
from client import FAILURES, ORIGIN, describe, on_campus
from workspace import count

SITES = {'Yanhekt': ORIGIN, 'BIT sign-in': 'https://sso.bit.edu.cn/cas/login', 'BCut': bcut.BASE}


def version(exe):
  first = subprocess.run([exe, '-version'], capture_output=True, text=True, errors='replace', check=False).stdout.split(
    '\n'
  )[0]
  return first.split()[2] if first.startswith('ffmpeg version') else 'unknown version'


def checks(session=None):
  """Yield (level, subject, text). `session` gives the signed-in client or None, for the checks that need one."""
  yield 'ok', 'python', f'{platform.python_version()} at {sys.executable}'

  yield 'ok', 'ffmpeg', f'{version(get_ffmpeg_exe())}, bundled'

  for name, url in SITES.items():
    try:
      requests.get(url, timeout=10, allow_redirects=False)
      yield 'ok', 'network', f'{name} answers'
    except requests.RequestException as error:
      yield 'fail', 'network', f'{name} does not answer: {describe(error)}'
  yield 'ok', 'campus network', 'on it, platform requests go to the campus address' if on_campus() else 'not on it'
  try:
    program = autoslides.command()[-1]
  except autoslides.AutoSlidesError as error:
    yield 'warn', 'AutoSlides', str(error)
  else:
    if autoslides.running():
      yield 'ok', 'AutoSlides', f'open, {program}'
    else:
      yield 'warn', 'AutoSlides', f'closed; open it from its shortcut before `sync` ({program})'

  client = session() if session else None
  if client is None:
    yield 'warn', 'login', 'not signed in; run `login`'
    return
  source = 'the Yanhe 2.0 sign-in AutoSlides keeps' if client.source == 'AutoSlides' else "yanhe-agent's own sign-in"
  yield 'ok', 'login', f'signed in with {source}'
  try:
    courses = client.courses()
    yield 'ok', 'platform', f'course list read, {count(len(courses), "course")}'
    for row in courses[:3]:  # the latest term's first courses; one with replays checks a lecture too
      course = client.course(row['id'])
      if not course['replays']:
        yield 'ok', 'platform', f'{course["title"]} read, no replays yet'
        continue
      info = client.lecture(course['id'], course['replays'][-1]['sub_id'])
      views = ', '.join(info['views']) or 'no recordings'
      yield (
        'ok',
        'platform',
        f'{course["title"]} read, {count(len(course["replays"]), "replay")}, the last with {views}',
      )
      break
  except (*FAILURES, KeyError, TypeError) as error:
    yield 'fail', 'platform', f'an answer is not as expected, so the platform may have changed: {describe(error)}'


def run(session=None):
  """Print the checks and return whether none failed."""
  failed = False
  for level, subject, text in checks(session):
    print(f'{level} {subject}: {text}', flush=True)
    failed |= level == 'fail'
  return not failed
