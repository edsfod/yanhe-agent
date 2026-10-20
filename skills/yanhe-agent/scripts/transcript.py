"""Timed transcripts: 字幕.srt and its 字幕.txt view, the course's corrections, query and embedding."""

import csv
import hashlib
import math
import re
from bisect import bisect_right
from pathlib import Path

from workspace import (
  clock,
  ffmpeg,
  log,
  read_json,
  read_slides,
  read_tsv,
  recordings,
  replacing,
  shown_slides,
  subtitles,
  tidy,
  timeline,
  write_json,
)

CUE = re.compile(r'(\d+):(\d\d):(\d\d)[,.](\d{1,3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{1,3})')


def seconds(hours, minutes, whole, millis):
  return int(hours) * 3600 + int(minutes) * 60 + int(whole) + int(millis.ljust(3, '0')) / 1000


def parse_srt(text):
  """(start, end, text) rows of an .srt file; cue text keeps its line breaks."""
  rows = []
  for block in re.split(r'\n[ \t]*\n', text.lstrip('﻿').replace('\r\n', '\n').strip()):
    lines = block.split('\n')
    for number, line in enumerate(lines[:2]):
      if match := CUE.search(line):
        start, end = seconds(*match.groups()[:4]), seconds(*match.groups()[4:])
        rows.append((start, end, '\n'.join(lines[number + 1 :]).strip()))
        break
  return rows


def format_srt(rows):
  return ''.join(
    f'{number}\n{clock(start, ",")} --> {clock(end, ",")}\n{text}\n\n'
    for number, (start, end, text) in enumerate(rows, 1)
  )


def write_text(lecture, rows):
  """Write 字幕.txt, a `[00:02:30] text` line per segment for reading and grep, unless it already says that."""
  path = lecture / '字幕.txt'
  text = ''.join(f'[{clock(start)}] {" ".join(words.split())}\n' for start, _, words in rows)
  if not path.exists() or path.read_text(encoding='utf-8') != text:
    with replacing(path) as part:
      part.write_text(text, encoding='utf-8')


def write(lecture, rows):
  """Make `rows` the lecture's transcript, 字幕.srt, and refresh 字幕.txt; return the number of segments."""
  with replacing(subtitles(lecture)) as part:
    part.write_text(format_srt(rows), encoding='utf-8')
  write_text(lecture, rows)
  return len(rows)


def migrate(lecture):
  """Turn a transcript from before 字幕.srt, `.cache/transcript.tsv`, into 字幕.srt, keeping the old file aside."""
  old = timeline(lecture, 'transcript')
  if old.exists() and not subtitles(lecture).exists():
    write(lecture, read_tsv(old))
    old.replace(old.with_name('transcript.old.tsv'))


def read(lecture):
  """The transcript's rows from 字幕.srt, bringing 字幕.txt up to date after an edit."""
  migrate(lecture)
  rows = parse_srt(subtitles(lecture).read_text(encoding='utf-8'))
  write_text(lecture, rows)
  return rows


def corrections(course):
  """The course's (wrong, right) pairs from 纠错.tsv, in the order they were added."""
  path = course / '纠错.tsv'
  if not path.exists():
    return []
  with path.open(encoding='utf-8', newline='') as file:
    return [(row['wrong'], row['right']) for row in csv.DictReader(file, delimiter='\t')]


def add_corrections(course, pairs):
  """Append the pairs not yet in 纠错.tsv and return them, so that each is applied to a transcript only once."""
  known = corrections(course)
  fresh = [pair for pair in dict.fromkeys(map(tuple, pairs)) if pair not in known]
  if fresh:
    with replacing(course / '纠错.tsv') as part, part.open('w', encoding='utf-8', newline='') as file:
      writer = csv.writer(file, delimiter='\t', lineterminator='\n')
      writer.writerow(['wrong', 'right'])
      writer.writerows(known + fresh)
  return fresh


def correct(rows, pairs):
  """Apply (wrong, right) pairs in turn to the rows' text: (rows, hits per pair)."""
  hits = []
  for wrong, right in pairs:
    hits.append(sum(text.count(wrong) for _, _, text in rows))
    rows = [(start, end, text.replace(wrong, right)) for start, end, text in rows]
  return rows, hits


def fix(lecture, pairs):
  """Apply the pairs to the lecture's transcript, rewriting it after any hit; return each pair's hits and the
  (start, before, after) of every segment that changed."""
  before = read(lecture)
  rows, hits = correct(before, pairs)
  if any(hits):
    write(lecture, rows)
  return hits, [(old[0], old[2], new[2]) for old, new in zip(before, rows) if old[2] != new[2]]


def adopt(lecture, rows):
  """Make the rows of a fresh transcription the lecture's transcript, with the course's corrections applied; return
  the number of segments."""
  return write(lecture, correct(tidy(rows), corrections(lecture.parent))[0])


def query(lecture, source='transcript', pattern=None, start=0, end=math.inf):
  """Segments matching the compiled `pattern` as `[00:02:30] text` lines, after a `slide 008: <path>` line whenever
  another slide is on screen."""
  changes = shown_slides(lecture)
  numbers = {Path(slide['file']): slide['number'] for slide in read_slides(lecture)}
  times = [time_s for time_s, _ in changes]
  shown = -1
  rows = read(lecture) if source == 'transcript' else read_tsv(timeline(lecture, source))
  for begin, finish, text in rows:
    if finish >= start and begin <= end and (not pattern or pattern.search(text)):
      current = bisect_right(times, begin) - 1
      if current > shown:
        shown = current
        if path := changes[current][1]:
          yield f'slide {numbers[path]:03}: {path}'
      yield f'[{clock(begin)}] {" ".join(text.split())}'


def digest(text):
  return hashlib.sha256(text.encode()).hexdigest()


def marks(lecture):
  return lecture / '.cache' / 'embedded.json'


def embedded(lecture, video):
  """Whether the recording carries its lecture's current 字幕.srt, as `embed` recorded it."""
  stat = video.stat()
  current = digest(subtitles(lecture).read_text(encoding='utf-8'))
  return read_json(marks(lecture), {}).get(video.name) == {
    'size': stat.st_size,
    'mtime_ns': stat.st_mtime_ns,
    'subtitles': current,
  }


def embed(lecture, video, text, transcript=False):
  """Replace `video` with a copy whose only subtitle track is `text`, of an .srt or .vtt file, without re-encoding.
  When the text is the lecture's 字幕.srt, record that the copy carries it; otherwise clear the record."""
  with replacing(video) as output:
    ffmpeg('-i', video, '-i', 'pipe:', '-map', '0:v', '-map', '0:a?', '-map', '1:0', '-c', 'copy', '-c:s', 'mov_text',
           '-metadata:s:s:0', 'language=zho', '-movflags', '+faststart', output, input=text.encode())  # fmt: skip
  path = marks(lecture)
  state = read_json(path, {})
  state.pop(video.name, None)
  if transcript:
    stat = video.stat()
    state[video.name] = {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'subtitles': digest(text)}
  if state or path.exists():
    write_json(path, state)


def embed_all(lectures, file=None):
  """Embed each lecture's 字幕.srt, or the subtitles `file`, in its recordings that lack it, logging each."""
  for lecture in lectures:
    if not file:
      read(lecture)  # brings 字幕.txt up to date after an edit of 字幕.srt
    videos = [video for video in recordings(lecture).values() if file or not embedded(lecture, video)]
    if not videos:
      log(lecture, 'recordings already carry the transcript')
      continue
    text = (file or subtitles(lecture)).read_text(encoding='utf-8')
    for video in videos:
      embed(lecture, video, text, transcript=not file)
      log(lecture, f'embedded subtitles in {video.name}')
