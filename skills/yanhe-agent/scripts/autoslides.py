"""The AutoSlides desktop app: the Yanhe 2.0 sign-in it keeps, and its command line for recordings and slides.

Commands run inside the app, which must already be open: started from here it would take this process's proxy
variables, which send its campus requests the wrong way, so it is opened by the user from its shortcut.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# What the app's error codes ask of the user
HINTS = {
  'sign_in_required': 'Sign in to Yanhekt in the AutoSlides window, then run the command again',
  'cli_control_disabled': 'Turn on Allow Command-Line Control in AutoSlides, Settings > Add-ons > Command Line',
  'app_not_running': 'Open AutoSlides from its shortcut, then run the command again',
}
STREAMS = {'classroom': 'camera', 'screen': 'screen'}  # this project's views and the app's names for them
EXTRACTED = ('normalizing', 'post_processing', 'completed')  # slides on disk, still being filtered until completed
BEIJING = timezone(timedelta(hours=8))


class AutoSlidesError(RuntimeError):
  """A command the app refused or failed, with its error code."""

  def __init__(self, code, message):
    super().__init__(message)
    self.code = code


def user_data():
  """The app's settings folder, which Electron names after the app."""
  if sys.platform == 'win32':
    return Path(os.environ['APPDATA']) / 'AutoSlides'
  if sys.platform == 'darwin':
    return Path.home() / 'Library' / 'Application Support' / 'AutoSlides'
  return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'AutoSlides'


def tokens(now=None):
  """The Yanhe 2.0 JWTs the app keeps, one per account, that have not expired."""
  path = user_data() / 'autoslides-config.json'
  try:
    sessions = json.loads(path.read_text(encoding='utf-8')).get('yanhe2Sessions') or {}
  except (OSError, ValueError):
    return []
  now = time.time() * 1000 if now is None else now
  return [s['jwt'] for s in sessions.values() if isinstance(s, dict) and s.get('jwt') and s.get('expiresAt', 0) > now]


def command():
  """The app's executable as a command, or `YANHE_AGENT_AUTOSLIDES` when set (a .py file runs with this Python)."""
  if override := os.environ.get('YANHE_AGENT_AUTOSLIDES'):
    return [sys.executable, override] if override.endswith('.py') else [override]
  if sys.platform == 'win32':
    for root in (
      os.environ.get('ProgramFiles'),
      os.environ.get('LOCALAPPDATA') and Path(os.environ['LOCALAPPDATA']) / 'Programs',
    ):
      if root and (exe := Path(root) / 'AutoSlides' / 'AutoSlides.exe').exists():
        return [str(exe)]
  elif sys.platform == 'darwin':
    if (exe := Path('/Applications/AutoSlides.app/Contents/MacOS/AutoSlides')).exists():
      return [str(exe)]
  elif exe := shutil.which('autoslides'):
    return [exe]
  raise AutoSlidesError(
    'not_installed', 'AutoSlides is not installed; get it from github.com/bit-admin/Yanhekt-AutoSlides'
  )


def running():
  """Whether the app is open."""
  if os.environ.get('YANHE_AGENT_AUTOSLIDES'):
    return True
  if sys.platform == 'win32':
    listing = subprocess.run(
      ['tasklist', '/FI', 'IMAGENAME eq AutoSlides.exe', '/NH'], check=False, capture_output=True, text=True
    )
    return 'AutoSlides.exe' in listing.stdout
  return subprocess.run(['pgrep', '-x', 'AutoSlides'], check=False, capture_output=True).returncode == 0


def run(*args, timeout=120):
  """The `data` of `autoslides <args> --json`, run inside the open app."""
  program = command()
  if not running():
    raise AutoSlidesError('app_not_running', HINTS['app_not_running'])
  result = subprocess.run(
    [*program, '--cli=autoslides', '--', *map(str, args), '--json'], check=False, capture_output=True, timeout=timeout
  )
  try:
    answer = json.loads(result.stdout.decode('utf-8'))
  except ValueError:
    raise AutoSlidesError('failed', f'AutoSlides gave no answer (exit status {result.returncode})') from None
  if not answer.get('ok'):
    error = answer.get('error') or {}
    code = error.get('code', 'failed')
    raise AutoSlidesError(code, HINTS.get(code) or error.get('hint') or error.get('message') or code)
  return answer.get('data') or {}


def twin(code):
  """The Yanhekt course of a Yanhe 2.0 course, found by its registrar code, and its sessions by start time:
  (course ID, {epoch seconds: session ID})."""
  if not code:
    raise AutoSlidesError('not_found', 'The course has no registrar code to find it by on Yanhekt')
  rows = run('search', code).get('courses') or []
  if len(rows) != 1:
    raise AutoSlidesError('not_found', f'Yanhekt lists {len(rows)} courses under registrar code {code}, not one')
  sessions = run('course', rows[0]['id']).get('sessions') or []
  starts = {
    int(datetime.strptime(s['started_at'][:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=BEIJING).timestamp()): s['id']
    for s in sessions
    if s.get('started_at')
  }
  return rows[0]['id'], starts


def queue(sessions, views, overwrite=False):
  """Put the recordings of the views of the sessions in the Download panel and return at once:
  {(session ID, view): (status, path)}, with the statuses of `download`, such as queued or skipped."""
  stream = 'both' if len(views) > 1 else STREAMS[views[0]]
  data = run('download', *sessions, '--stream', stream, '--no-wait', *(['--force'] if overwrite else []), timeout=600)
  views_of = {name: view for view, name in STREAMS.items()}
  return {
    (item['session_id'], views_of[item['stream']]): (item['status'], item['path'])
    for item in data.get('items') or []
    if item.get('stream') in views_of
  }


def panel():
  """The rows of the Download panel, each a download with its status, progress and slide extraction."""
  return run('downloads').get('items') or []


def slides_folder(screen):
  """The folder the app extracts a screen recording's slides into, named after the recording."""
  screen = Path(screen)
  return screen.with_name('slides_' + screen.stem.removeprefix('screen_'))


def read_timeline(folder):
  """(seconds, file name) of each change of the screen in the folder's timeline.json, in time order: a slide shown
  again names the slide it repeats, and a gap, such as a desktop the app left out, names None."""
  try:
    timeline = json.loads((Path(folder) / 'timeline.json').read_text(encoding='utf-8'))
  except (OSError, ValueError):
    return []
  shown = []
  for event in sorted(timeline.get('events') or [], key=lambda event: event['changeAt']):
    resolution = (timeline.get('resolutions') or {}).get(event['id']) or {}
    state = resolution.get('state')
    file = (
      resolution.get('file')
      if state == 'canonical'
      else resolution.get('duplicateOf')
      if state == 'duplicate'
      else None
    )
    shown.append((float(event['changeAt']), file if file and (Path(folder) / file).exists() else None))
  return shown


def filtering(session):
  """Whether the open app is still filtering the slides of the session, which can remove some and so renumber the
  rest; False when the app is closed."""
  if not running():
    return False
  rows = [row for row in panel() if row['session_id'] == session and row['kind'] == 'screen' and row.get('extraction')]
  return bool(rows) and max(rows, key=lambda row: row['added_at'])['extraction']['state'] in (
    'extracting',
    'normalizing',
    'post_processing',
  )
