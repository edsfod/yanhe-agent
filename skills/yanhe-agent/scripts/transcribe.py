"""Turning a lecture's classroom recording into 字幕.srt with BCut, Bilibili's free speech recognition."""

import time
from functools import partial

from bcut import BCut, Rejected
from client import FAILURES, chosen_asr, describe
from transcript import adopt, migrate
from workspace import Ticker, count, ffmpeg, log, read_json, recordings, replacing, transcribed, write_json

POLL = 10  # seconds between asking BCut whether the task is done


def sound_track(video, audio, reencode=False):
  """Write the recording's sound to `audio` as .m4a: the track copied unchanged, which takes seconds, or re-encoded as
  mono 16 kHz AAC, which BCut accepts when it refused the original."""
  options = ['-ac', '1', '-ar', '16000', '-b:a', '48k'] if reencode else ['-c:a', 'copy']
  with replacing(audio) as part:
    ffmpeg('-i', video, '-vn', *options, part)


def recognize(bcut, ticker, audio, timeout=7200):
  """BCut's rows for the sound in `audio`.

  The task's ID is kept in `.cache/bcut-task.json` with the size and time of the sound it was made from, until the
  result is in; a run that stopped meanwhile then waits for that task rather than sending the same sound again.
  """
  note = audio.with_name('bcut-task.json')
  info = audio.stat()
  made_from = {'size': info.st_size, 'mtime_ns': info.st_mtime_ns}

  def send():
    ticker.set(f'uploading {info.st_size / 1e6:.0f} MB of audio to BCut')
    task = bcut.submit(bcut.upload(audio))
    write_json(note, {'task': task, 'audio': made_from})
    ticker.set('waiting for BCut to transcribe')
    return task

  earlier = read_json(note, None)
  resuming = bool(earlier) and earlier['audio'] == made_from
  if resuming:
    task = earlier['task']
    ticker.set('waiting for BCut to transcribe, resuming the earlier task')
  else:
    task = send()
  give_up = time.monotonic() + timeout
  while time.monotonic() < give_up:
    try:
      rows = bcut.result(task)
    except Rejected:
      note.unlink(missing_ok=True)
      raise
    except RuntimeError:
      if not resuming:
        raise
      resuming, task = False, send()  # BCut has forgotten the earlier task
      continue
    if rows is not None:
      note.unlink(missing_ok=True)
      return rows
    time.sleep(POLL)
  raise TimeoutError(f'BCut task {task} still running after {timeout // 60} minutes; run again to keep waiting')


def new_rows(lecture, video, overwrite, say):
  """BCut's rows for the lecture's recording, sending its sound and, if BCut refuses it, the sound re-encoded."""
  audio = lecture / '.cache' / 'audio.m4a'
  with Ticker(say) as ticker:
    if overwrite or not audio.exists():
      ticker.set(f'copying the sound track of {video.name}')
      sound_track(video, audio)
    bcut = BCut(ticker.say)
    try:
      return recognize(bcut, ticker, audio)
    except Rejected:
      say('BCut rejected the sound track')
      ticker.set('encoding the sound track to AAC')
      sound_track(video, audio, reencode=True)
      return recognize(bcut, ticker, audio)


def transcribe_lecture(lecture, overwrite, say):
  """Give the lecture a 字幕.srt from its first recording, telling `say` how it goes; return whether it failed.

  BCut's own answer is kept in `.cache/bcut.json`, so a deleted 字幕.srt is made again without sending the sound.
  """
  if transcribed(lecture) and not overwrite:
    migrate(lecture)
    say('kept existing transcript')
    return False
  answer = lecture / '.cache' / 'bcut.json'
  try:
    chosen_asr()
    if answer.exists() and not overwrite:
      rows = read_json(answer, [])
    else:
      video = next(iter(recordings(lecture).values()), None)
      if video is None:
        say('no recording to transcribe; run `sync` first')
        return True
      rows = new_rows(lecture, video, overwrite, say)
      write_json(answer, rows)
    say(f'{count(adopt(lecture, rows), "segment")} in 字幕.srt')
    return False
  except FAILURES as error:
    say(f'transcription failed: {describe(error)}')
    return True


def transcribe(lectures, overwrite=False):
  """Transcribe the lectures in turn; return how many failed."""
  return sum(transcribe_lecture(lecture, overwrite, partial(log, lecture)) for lecture in lectures)
