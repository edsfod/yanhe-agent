"""Background jobs: a command run by a worker process of its own, with its state and log in `.yanhe-agent/jobs/`.

A job lives as long as the harness lets its process live. A worker that stops without saying so, because its session
ended or the machine slept, shows as interrupted once its heartbeat is STALE seconds old; running the command again
resumes from what was saved.
"""

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from client import FAILURES, describe
from workspace import read_json, stamp, write_json

BEAT, STALE = 5, 30
SCRIPT = Path(__file__).with_name('yanhe_agent.py')
LINE = re.compile(r'(?:\d\d:\d\d:\d\d )?(?:(?P<label>[^:\n/]+/[^:\n/]+): )?(?P<message>.*)')  # [time] [lecture: ]text


def folder(workspace):
  return workspace / '.yanhe-agent' / 'jobs'


def state_path(workspace, number):
  return folder(workspace) / f'{number}.json'


def log_path(workspace, number):
  return folder(workspace) / f'{number}.log'


def save(workspace, state):
  write_json(state_path(workspace, state['id']), state)


def load(workspace, number):
  """A job's state, with `running` turned into `interrupted` when its heartbeat stopped."""
  state = read_json(state_path(workspace, number), None)
  if state and state['state'] == 'running' and time.time() - state['beat'] > STALE:
    state['state'] = 'interrupted'
  return state


def listing(workspace):
  numbers = sorted(int(path.stem) for path in folder(workspace).glob('*.json') if path.stem.isdigit())
  return [state for number in numbers if (state := load(workspace, number))]


def start(workspace, argv):
  """Start a worker that runs `yanhe_agent.py <argv>` in `workspace`, and return the job's number."""
  jobs = listing(workspace)
  busy = [job for job in jobs if job['state'] == 'running']
  if busy:
    raise ValueError(f'Job {busy[0]["id"]} is still running; `wait` for it or `cancel` it first')
  number = max((job['id'] for job in jobs), default=0) + 1
  now = time.time()
  state = {'id': number, 'command': argv, 'state': 'running', 'started': now, 'beat': now, 'pid': None}
  folder(workspace).mkdir(parents=True, exist_ok=True)
  try:  # created, not replaced, so that of two commands starting at once only one gets the number
    with state_path(workspace, number).open('x', encoding='utf-8') as file:
      file.write(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
  except FileExistsError:
    raise ValueError(f'Job {number} started meanwhile; `wait` for it') from None
  detached(['--job', str(number), *argv], workspace, log_path(workspace, number))
  return number


def detached(argv, cwd, log):
  """Start `yanhe_agent.py <argv>` in a process group of its own that writes to `log`, and return the process at once."""
  if sys.platform == 'win32':  # a hidden console of its own, which ffmpeg then shares instead of opening windows
    detach = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
  else:
    detach = {'start_new_session': True}
  log.parent.mkdir(parents=True, exist_ok=True)
  with log.open('wb') as file:
    return subprocess.Popen(
      [sys.executable, SCRIPT, *argv],
      cwd=cwd,
      stdin=subprocess.DEVNULL,
      stdout=file,
      stderr=subprocess.STDOUT,
      env={**os.environ, 'PYTHONUNBUFFERED': '1'},
      **detach,
    )


def work(workspace, number, run):
  """Run `run` as job `number`, beating while it runs and recording how it ended."""
  state = load(workspace, number)
  state['pid'] = os.getpid()
  lock = threading.Lock()
  stop = threading.Event()

  def beat():
    while not stop.wait(BEAT):
      with lock:
        state['beat'] = time.time()
        save(workspace, state)

  threading.Thread(target=beat, daemon=True).start()
  code, message = 0, ''
  try:
    run()
  except SystemExit as error:
    code = error.code if isinstance(error.code, int) else 1
    message = '' if isinstance(error.code, int) or error.code is None else str(error.code)
  except Exception as error:  # noqa: BLE001 - recorded as the job's end, the traceback goes to its log
    code, message = 1, describe(error)
    if not isinstance(error, FAILURES):
      traceback.print_exc()
  finally:
    stop.set()
    with lock:
      state.update(state='done' if code == 0 else 'failed', beat=time.time(), ended=time.time(), message=message)
      save(workspace, state)
  return code


def cancel(workspace, number):
  """Stop a running job and the programs it started."""
  state = load(workspace, number)
  if not state or state['state'] != 'running':
    raise ValueError(f'Job {number} is not running')
  if not state['pid']:
    raise ValueError(f'Job {number} is starting; cancel it in a moment')
  if sys.platform == 'win32':
    subprocess.run(['taskkill', '/T', '/F', '/PID', str(state['pid'])], capture_output=True, check=False)
  else:
    try:
      os.killpg(state['pid'], signal.SIGTERM)
    except ProcessLookupError:
      pass
  state.update(state='cancelled', ended=time.time())
  save(workspace, state)


def progress(workspace, number):
  """The last log message of each lecture, in the order they first appear, and the job's other last message."""
  path = log_path(workspace, number)
  latest, general = {}, ''
  text = path.read_text(encoding='utf-8', errors='replace') if path.exists() else ''
  for line in filter(str.strip, text.splitlines()):
    match = LINE.fullmatch(line.strip())
    if match['label']:
      latest[match['label']] = match['message']
    else:
      general = match['message']
  return latest, general


def report(workspace, state):
  """Lines on a job: a heading with its state and age, then where each lecture stands."""
  last = state['beat'] if state['state'] == 'interrupted' else time.time()  # an interrupted job ended unrecorded
  spent = stamp(state.get('ended', last) - state['started'])
  heading = {
    'running': f'running for {spent}',
    'done': f'done in {spent}',
    'failed': f'failed after {spent}',
    'cancelled': f'cancelled after {spent}',
    'interrupted': f'interrupted after {spent}, when its process stopped; run the same command again to resume',
  }[state['state']]
  latest, general = progress(workspace, state['id'])
  lines = [f'Job {state["id"]} ({" ".join(state["command"])}): {heading}']
  if state.get('message'):
    lines.append(state['message'])
  lines += [f'{label}: {message}' for label, message in latest.items()]
  if general and not latest:
    lines.append(general)
  return lines


def wait(workspace, number=None, timeout=90):
  """Wait up to `timeout` seconds for a job, the latest by default, to end; return its lines and whether it ended
  well, or None while it still runs."""
  jobs = listing(workspace)
  if not jobs:
    raise ValueError('No jobs here; start one with `sync`')
  number = number or jobs[-1]['id']
  deadline = time.monotonic() + timeout
  while True:
    state = load(workspace, number)
    if state is None:
      raise ValueError(f'No job {number}; `jobs` lists them')
    if state['state'] != 'running' or time.monotonic() >= deadline:
      break
    time.sleep(min(2, max(0, deadline - time.monotonic())))
  ended = None if state['state'] == 'running' else state['state'] == 'done'
  lines = report(workspace, state)
  if ended is None:
    lines.append('Still running; run `wait` again')
  return lines, ended
