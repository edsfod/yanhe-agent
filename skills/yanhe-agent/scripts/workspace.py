"""The workspace of course folders: course.json, the lectures' files, the tables of timed text, and what the other
modules share for writing files, logging and telling time."""

import csv
import json
import math
import re
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from imageio_ffmpeg import get_ffmpeg_exe

import autoslides

VIEWS = {'classroom': '教室', 'screen': '屏幕'}  # each view and the word in the names of its files
RENAME_TRIES = 6  # Windows refuses to replace a file another process has open, usually for a moment


@contextmanager
def replacing(path):
  """Hand out a neighbour of `path` to write, and move it onto `path` once the block has finished, so that `path`
  never holds half a file. The neighbour ends in `.part` and the real suffix, which ffmpeg reads the format from."""
  path.parent.mkdir(parents=True, exist_ok=True)
  draft = path.with_suffix('.part' + path.suffix)
  try:
    yield draft
    for attempt in range(RENAME_TRIES):
      try:
        draft.replace(path)
        break
      except PermissionError:
        if attempt == RENAME_TRIES - 1:
          raise
        time.sleep(0.05 * 2**attempt)
  finally:
    draft.unlink(missing_ok=True)


def ffmpeg(*args, **kwargs):
  """Run the ffmpeg that imageio-ffmpeg bundles, or `IMAGEIO_FFMPEG_EXE` when set."""
  return subprocess.run([get_ffmpeg_exe(), '-v', 'error', '-y', *args], check=True, **kwargs)


def read_json(path, default):
  return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def write_json(path, value):
  with replacing(path) as draft:
    draft.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def read_index(folder):
  """A course folder's course.json, or {} for any other folder."""
  return read_json(folder / 'course.json', {})


def file_name(text):
  """The text with what file systems refuse replaced by `_`."""
  return re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', text).strip(' .')


def lecture_id(title):
  """A lecture's folder name from the platform's title: 2026-10-8第6-7节 becomes 10月08日第6-7节; another title is
  only made safe as a file name."""
  if parts := re.fullmatch(r'\d{4}-(\d+)-(\d+)(第.+节)', title):
    month, day, periods = parts.groups()
    return f'{int(month):02}月{int(day):02}日{periods}'
  return file_name(title)


def course_folder(workspace, course):
  """Where a course lives in the workspace: a folder named after its title, or after its title and ID when another
  course already has the title."""
  folder = workspace / file_name(course['title'])
  if read_index(folder).get('course_id', course['id']) != course['id']:
    folder = folder.with_name(f'{folder.name} {course["id"]}')
  return folder


def merge_lectures(course, index):
  """course.json's lectures for the course's current replays: a replay seen before keeps all that was recorded for it,
  a new one gets its folder name."""
  recorded = {lecture['sub_id']: lecture for lecture in index.get('lectures', [])}
  merged = []
  for replay in course['replays']:
    lecture = recorded.get(replay['sub_id']) or {'id': lecture_id(replay['title']), 'sub_id': replay['sub_id']}
    merged.append({**lecture, 'begin': replay['begin']})
  return merged


def sync_index(course, folder):
  """Bring the folder's course.json up to date with the course, keeping what was recorded; return it."""
  index = read_index(folder)
  if index.get('course_id', course['id']) != course['id']:
    raise ValueError(f'{folder} belongs to course {index["course_id"]}')
  fresh = {key: course[key] for key in ('title', 'teacher', 'term', 'code')}
  kept = {key: index[key] for key in ('autoslides_course_id',) if key in index}
  index = {'course_id': course['id'], **fresh, **kept, 'lectures': merge_lectures(course, index)}
  write_json(folder / 'course.json', index)
  return index


def select(index, names=None, latest=None):
  """The lectures of course.json with the given folder names, or else those not dropped, or their last `latest`."""
  if names is None:
    kept = [lecture for lecture in index['lectures'] if not lecture.get('dropped')]
    return kept[-latest:] if latest else kept
  chosen = [lecture for lecture in index['lectures'] if lecture['id'] in names]
  unknown = sorted(set(names) - {lecture['id'] for lecture in chosen})
  if unknown:
    raise ValueError(f'Unknown lecture {", ".join(unknown)}; run `status` for the list')
  return chosen


def lectures(paths, latest=None):
  """The lecture folders the targets stand for: a course folder for its lectures as `select` picks them, any other
  folder for itself."""
  found = []
  for path in paths:
    if index := read_index(path):
      found.extend(path / lecture['id'] for lecture in select(index, latest=latest))
    elif path.is_dir():
      found.append(path)
    else:
      raise ValueError(f'{path.name} is not a course or lecture folder')
  return found


def drop(lecture):
  """Delete an empty lecture's folder and mark it dropped in course.json, so that its course leaves it out."""
  index = read_index(lecture.parent)
  entry = next((row for row in index.get('lectures', []) if row['id'] == lecture.name), None)
  if entry is None:
    raise ValueError(f'{lecture.name} is not a lecture in {lecture.parent.name}/course.json')
  shutil.rmtree(lecture, ignore_errors=True)
  entry['dropped'] = True
  write_json(lecture.parent / 'course.json', index)


def lecture_entry(lecture):
  """The lecture's row in its course.json, or {}."""
  return next((row for row in read_index(lecture.parent).get('lectures', []) if row['id'] == lecture.name), {})


def recording(lecture, view):
  """Where the view's recording is: the file AutoSlides fetched, as `sync` recorded it, or else one saved in the
  lecture's folder."""
  path = lecture_entry(lecture).get('files', {}).get(view)
  return Path(path) if path else lecture / f'录播-{VIEWS[view]}.mp4'


def recordings(lecture):
  """The recordings that exist, by view, the classroom first."""
  found = {view: recording(lecture, view) for view in VIEWS}
  return {view: path for view, path in found.items() if path.exists()}


def timeline(lecture, source):
  """The table of a lecture's timed text: the platform's fast transcript, or a BCut transcript from before 字幕.srt."""
  return lecture / '.cache' / f'{source}.tsv'


def subtitles(lecture):
  """The lecture's transcript itself; edits and corrections change it, and the other transcript files follow it."""
  return lecture / '字幕.srt'


def transcribed(lecture):
  return subtitles(lecture).exists() or timeline(lecture, 'transcript').exists()


def shown_slides(lecture):
  """(seconds, path or None) for each change of the screen, in time order, from the slides AutoSlides extracted."""
  folder = lecture_entry(lecture).get('files', {}).get('slides')
  if not folder:
    return []
  return [(time_s, name and Path(folder) / name) for time_s, name in autoslides.read_timeline(folder)]


def read_slides(lecture):
  """The lecture's slides, numbered in the order they first appear: {'number', 'file': its absolute path, 'time_s'}."""
  slides, seen = [], set()
  for time_s, path in shown_slides(lecture):
    if path is None or path in seen:
      continue
    seen.add(path)
    slides.append({'number': len(slides) + 1, 'file': str(path), 'time_s': time_s})
  return slides


def holdings(lecture, folder):
  """A line on what a lecture has: its length from course.json, then what is saved for it."""
  if lecture.get('dropped'):
    return 'dropped'
  parts = [clock(lecture['duration_s'])] if 'duration_s' in lecture else []
  fast = timeline(folder, 'fast-transcript')
  if fast.exists():
    parts.append(f'fast transcript ({count(len(read_tsv(fast)), "segment")})')
  if slides := read_slides(folder):
    parts.append(count(len(slides), 'slide'))
  parts.extend(f'{view} video' for view in recordings(folder))
  if transcribed(folder):
    parts.append('transcript')
  if (folder / '笔记.md').exists():
    parts.append('notes')
  return ', '.join(parts) or 'nothing saved'


def tidy(rows):
  """(start, end, text) rows sorted by start, with the text's spaces collapsed and rows without text left out."""
  cleaned = ((start, end, ' '.join(text.split())) for start, end, text in rows)
  return [row for row in sorted(cleaned) if row[2]]


def write_tsv(path, rows):
  """Write the rows, tidied, as a tab-separated table; return how many there are."""
  rows = tidy(rows)
  with replacing(path) as draft, draft.open('w', encoding='utf-8', newline='') as file:
    table = csv.writer(file, delimiter='\t', lineterminator='\n')
    table.writerow(['start_s', 'end_s', 'text'])
    table.writerows((f'{start:.3f}', f'{end:.3f}', text) for start, end, text in rows)
  return len(rows)


def read_tsv(path):
  with path.open(encoding='utf-8', newline='') as file:
    return [(float(row['start_s']), float(row['end_s']), row['text']) for row in csv.DictReader(file, delimiter='\t')]


def count(number, noun):
  """1 lecture, 2 lectures."""
  return f'{number} {noun}' + ('' if number == 1 else 's')


def label(lecture):
  """How output lines name a lecture: course/lecture."""
  return f'{lecture.parent.name}/{lecture.name}'


def clock(seconds, separator=''):
  """01:02:03, or 01:02:03,456 when a separator for the milliseconds is given."""
  millis = round(seconds * 1000)
  hours, millis = divmod(millis, 3_600_000)
  minutes, millis = divmod(millis, 60_000)
  whole, millis = divmod(millis, 1000)
  shown = f'{hours:02}:{minutes:02}:{whole:02}'
  return shown + f'{separator}{millis:03}' if separator else shown


def parse_time(value):
  """Seconds from 150, 2:30 or 1:02:30."""
  seconds = 0.0
  for part in value.split(':'):
    seconds = seconds * 60 + float(part)
  return seconds


def stamp(seconds):
  """02:30, or 01:02:30 once an hour has passed."""
  return clock(seconds).removeprefix('00:')


OUTPUT = threading.Lock()


def log(lecture, message):
  """Print a line led by the time and, when given, the lecture; threads may call it together."""
  lead = f'{label(lecture)}: ' if lecture else ''
  with OUTPUT:
    print(f'{time.strftime("%H:%M:%S")} {lead}{message}', flush=True)


class Ticker:
  """Report a long step's status through `say`: shortly after `set` or `poke`, then every SLOWEST seconds, and never
  twice within FASTEST seconds. The status is read when it is printed: the step's text with the time spent on it."""

  SLOWEST, FASTEST = 10, 0.5

  def __init__(self, say, text=None):
    self.say = say
    self.text, self.start = '', time.monotonic()
    self.printed = -math.inf  # when the last report went out
    self.next = math.inf  # when the next one is due
    self.active = True
    self.changed = threading.Condition()
    self.worker = threading.Thread(target=self.loop, daemon=True)
    if text:
      self.set(text)

  def __enter__(self):
    self.worker.start()
    return self

  def __exit__(self, *failure):
    with self.changed:
      self.active = False
      self.changed.notify()
    self.worker.join()

  def set(self, text):
    """Make `text` the step's status, counting its time from now."""
    self.text, self.start = text, time.monotonic()
    self.poke()

  def poke(self):
    """Ask for a report as soon as FASTEST seconds have passed since the last."""
    with self.changed:
      self.next = min(self.next, self.printed + self.FASTEST)
      self.changed.notify()

  def status(self):
    spent = time.monotonic() - self.start
    return f'{self.text} ({stamp(spent)})' if spent >= 1 else self.text

  def loop(self):
    while True:
      with self.changed:
        while self.active and time.monotonic() < self.next:
          self.changed.wait(min(self.next - time.monotonic(), self.SLOWEST))
        if not self.active:
          return
        self.printed = time.monotonic()
        self.next = self.printed + self.SLOWEST
      self.say(self.status())
