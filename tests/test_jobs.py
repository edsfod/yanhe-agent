"""Background jobs, with real worker processes running offline commands."""

import subprocess
import sys
import time

import pytest

import jobs


@pytest.fixture
def workspace(transcribed):
  return transcribed.parents[1]


def test_a_job_runs_in_a_process_of_its_own_and_reports_its_output(workspace):
  number = jobs.start(workspace, ['status'])
  assert number == 1
  lines, ended = jobs.wait(workspace, timeout=60)
  assert ended is True
  assert lines[0].startswith('Job 1 (status): done in ')
  assert '示例课程/09月23日第3-5节: 00:00:30, 3 slides, transcript' in lines
  state = jobs.load(workspace, 1)
  assert state['pid'] and state['ended'] >= state['started']


def test_a_failing_job_records_the_error(workspace):
  jobs.start(workspace, ['query', 'missing'])
  lines, ended = jobs.wait(workspace, timeout=60)
  assert ended is False
  assert lines[0].startswith('Job 1 (query missing): failed after ')
  assert lines[1] == 'missing is not a course or lecture folder'


def test_numbers_grow_and_a_running_job_blocks_another(workspace):
  jobs.start(workspace, ['status'])
  jobs.wait(workspace, timeout=60)
  now = time.time()
  jobs.save(workspace, {'id': 2, 'command': ['sync'], 'state': 'running', 'started': now, 'beat': now, 'pid': 1})
  with pytest.raises(ValueError, match='Job 2 is still running'):
    jobs.start(workspace, ['status'])
  assert [job['id'] for job in jobs.listing(workspace)] == [1, 2]


def test_of_two_commands_starting_at_once_only_one_gets_the_number(workspace, monkeypatch):
  jobs.start(workspace, ['status'])
  jobs.wait(workspace, timeout=60)
  monkeypatch.setattr(jobs, 'listing', lambda workspace: [])  # as listed before the other command saved its job
  with pytest.raises(ValueError, match='Job 1 started meanwhile'):
    jobs.start(workspace, ['status'])
  assert jobs.load(workspace, 1)['command'] == ['status']


def test_a_silent_worker_counts_as_interrupted(workspace):
  past = time.time() - 600
  state = {'id': 1, 'command': ['sync', 'C'], 'state': 'running', 'started': past - 60, 'beat': past, 'pid': 1}
  jobs.save(workspace, state)
  assert jobs.load(workspace, 1)['state'] == 'interrupted'
  lines, ended = jobs.wait(workspace, timeout=1)
  assert ended is False
  assert 'interrupted after 01:00, when its process stopped; run the same command again to resume' in lines[0]
  jobs.start(workspace, ['status'])  # no longer in the way


def test_wait_gives_up_after_the_timeout(workspace):
  now = time.time()
  jobs.save(workspace, {'id': 1, 'command': ['sync'], 'state': 'running', 'started': now, 'beat': now + 60, 'pid': 1})
  jobs.log_path(workspace, 1).write_text(
    '10:00:00 示例课程/09月23日第3-5节: classroom video 10%\n'
    '10:00:05 示例课程/09月30日第3-5节: queued for slides\n'
    '10:00:09 示例课程/09月23日第3-5节: classroom video 40%, 12 MB/s, 03:10 left\n',
    encoding='utf-8',
  )
  start = time.monotonic()
  lines, ended = jobs.wait(workspace, timeout=1)
  assert time.monotonic() - start < 5
  assert ended is None
  assert lines[1:] == [
    '示例课程/09月23日第3-5节: classroom video 40%, 12 MB/s, 03:10 left',
    '示例课程/09月30日第3-5节: queued for slides',
    'Still running; run `wait` again',
  ]


def test_cancel_stops_the_worker_and_what_it_started(workspace):
  flags = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == 'win32' else {}
  worker = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True, **flags)
  now = time.time()
  jobs.save(
    workspace, {'id': 1, 'command': ['sync'], 'state': 'running', 'started': now, 'beat': now, 'pid': worker.pid}
  )
  jobs.cancel(workspace, 1)
  assert worker.wait(timeout=10) is not None
  assert jobs.load(workspace, 1)['state'] == 'cancelled'
  with pytest.raises(ValueError, match='Job 1 is not running'):
    jobs.cancel(workspace, 1)


def test_wait_without_jobs(tmp_path):
  with pytest.raises(ValueError, match='No jobs here'):
    jobs.wait(tmp_path)
