"""The lectures' platform data and recordings: fast transcripts from Yanhe 2.0, and recordings and slides through the
AutoSlides app, which keeps them in its output folder."""

import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import autoslides
from client import FAILURES, describe
from transcribe import transcribe_lecture
from workspace import clock, count, label, log, read_tsv, select, sync_index, timeline, write_json, write_tsv


def survey(client, course, folder, names=None, latest=None, overwrite=False):
  """Fetch the fast transcript of each selected lecture, recording its length in course.json, and yield a line on how
  much was said, which tells an empty lecture: a holiday, a cancelled class or an empty room."""
  index = sync_index(course, folder)
  chosen = select(index, names, latest)
  if not chosen:
    raise ValueError(f'{course["title"]} has no replays')
  try:
    for lecture in chosen:
      base = folder / lecture['id']
      base.mkdir(parents=True, exist_ok=True)
      lecture.pop('dropped', None)
      try:
        if 'duration_s' not in lecture:
          lecture['duration_s'] = client.lecture(course['id'], lecture['sub_id'])['duration_s']
        path = timeline(base, 'fast-transcript')
        if overwrite or not path.exists():
          write_tsv(path, client.subtitles(lecture['sub_id']))
      except FAILURES as error:
        yield f'{label(base)}: fast transcript failed: {describe(error)}'
        continue
      rows = read_tsv(path)
      spoken = sum(end - start for start, end, _ in rows)
      line = f'{label(base)}: {clock(lecture["duration_s"])}, {count(len(rows), "segment")}, {clock(spoken)} of speech'
      yield (
        line
        + (', possibly empty' if spoken < 300 else '')
        + (', short recording' if lecture['duration_s'] < 1200 else '')
      )
  finally:
    write_json(folder / 'course.json', index)


POLL = 10  # seconds between looks at the Download panel
EXTRACTION_GRACE = 120  # seconds a finished screen recording may wait for the app to start extracting its slides
FINISHED = {'completed', 'failed', 'cancelled', 'removed'}
KINDS = {kind: view for view, kind in autoslides.STREAMS.items()}


def pair(index):
  """Record the Yanhekt session of each lecture, which AutoSlides fetches recordings by, unless all have one."""
  if all('autoslides_session_id' in lecture for lecture in index['lectures']):
    return
  course_id, starts = autoslides.twin(index.get('code'))
  index['autoslides_course_id'] = course_id
  for lecture in index['lectures']:
    if session := starts.get(lecture.get('begin')):
      lecture['autoslides_session_id'] = session


def describe_row(view, row):
  """A Download panel row as a line: the view's progress, then its slide extraction."""
  status = row['status']
  text = f'{view} video {status}' + (f' {row["progress"]}%' if status in ('downloading', 'processing') else '')
  if row.get('error'):
    text += f': {row["error"]}'
  if extraction := row.get('extraction'):
    text += f', slides {extraction["state"]} {extraction.get("progress", 0)}%'
  return text


def download(client, course, folder, names=None, *, latest=None, videos=(), slides=False, transcribe=False,
             overwrite=False):  # fmt: skip
  """Fetch the selected lectures' recordings through the AutoSlides app, which also extracts the slides of screen
  recordings, recording their paths in course.json, and transcribe each classroom recording once it is there; return
  how many lectures failed.

  `names` are lecture IDs, by default every lecture not dropped or the last `latest` of them; a named dropped lecture
  is unmarked. The recordings stay in the app's output folder, where its Lectures page shows them too.
  """
  views = [view for view in autoslides.STREAMS if view in videos or (view == 'screen' and slides)]
  if not views:
    raise ValueError('Nothing to do; leave out one of --no-classroom and --no-slides')
  index = sync_index(course, folder)
  chosen = select(index, names, latest)
  if not chosen:
    raise ValueError(f'{course["title"]} has no replays to download')
  failed = set()
  try:
    for lecture in chosen:
      (folder / lecture['id']).mkdir(parents=True, exist_ok=True)
      lecture.pop('dropped', None)
      if 'duration_s' not in lecture:
        lecture['duration_s'] = client.lecture(course['id'], lecture['sub_id'])['duration_s']
    pair(index)
    for lecture in chosen:
      if 'autoslides_session_id' not in lecture:
        failed.add(lecture['id'])
        log(folder / lecture['id'], 'not on Yanhekt, where AutoSlides fetches recordings from')
    wanted = [lecture for lecture in chosen if lecture['id'] not in failed]
    queued = (
      autoslides.queue([lecture['autoslides_session_id'] for lecture in wanted], views, overwrite) if wanted else {}
    )
    for lecture in wanted:
      files = lecture.setdefault('files', {})
      for view in views:
        status, path = queued.get((lecture['autoslides_session_id'], view), (None, None))
        if path:
          files[view] = path
        log(folder / lecture['id'], f'{view} video {status or "not on Yanhekt"}')
      if 'screen' in files:
        files['slides'] = str(autoslides.slides_folder(files['screen']))
  finally:
    write_json(folder / 'course.json', index)
  extracting = 'screen' in views and autoslides.run('config').get('settings', {}).get('extract_after_download')
  if 'screen' in views and not extracting:
    log(None, 'AutoSlides does not extract slides after downloads; turn on its auto-extraction to get slides')
  waiting = {
    (lecture['autoslides_session_id'], view): lecture
    for lecture in wanted
    for view in views
    if queued.get((lecture['autoslides_session_id'], view), (None,))[0] in ('queued', 'downloaded')
  }
  queue = ThreadPoolExecutor(1)
  jobs = []

  def start_transcript(lecture):
    if transcribe and 'classroom' in views:
      base = folder / lecture['id']
      log(base, 'queued for transcript')
      jobs.append((lecture['id'], queue.submit(transcribe_lecture, base, overwrite, partial(log, base))))

  try:
    for lecture in wanted:
      if (lecture['autoslides_session_id'], 'classroom') not in waiting:
        start_transcript(lecture)  # kept from an earlier run
    said, finished_at, absent = {}, {}, {}
    while waiting:
      time.sleep(POLL)
      rows = {}
      for row in autoslides.panel():  # the latest row of each session and kind
        key = (row['session_id'], KINDS.get(row['kind']))
        if key in waiting and row['added_at'] >= rows.get(key, {}).get('added_at', ''):
          rows[key] = row
      for key in [key for key in waiting if key not in rows]:  # the panel forgets its rows when its window reloads
        absent[key] = absent.get(key, 0) + 1
        if absent[key] >= 3:
          failed.add(waiting[key]['id'])
          log(folder / waiting[key]['id'], f'{key[1]} video left the AutoSlides Download panel; run `sync` again')
          del waiting[key]
      for key, row in rows.items():
        lecture, view = waiting[key], key[1]
        line = describe_row(view, row)
        if said.get(key) != line:
          said[key] = line
          log(folder / lecture['id'], line)
        if row['status'] not in FINISHED:
          continue
        if row['status'] != 'completed':
          failed.add(lecture['id'])
        elif view == 'classroom':
          start_transcript(lecture)
        elif extracting:
          extraction = row.get('extraction')
          if extraction is None and time.monotonic() - finished_at.setdefault(key, time.monotonic()) < EXTRACTION_GRACE:
            continue  # the app has not taken it for extraction yet
          if extraction and extraction['state'] not in (*autoslides.EXTRACTED, 'error'):  # filtering goes on in the app
            continue
          if not extraction or extraction['state'] == 'error':
            failed.add(lecture['id'])
            log(folder / lecture['id'], 'no slides: AutoSlides did not extract them')
        del waiting[key]
    failed |= {name for name, job in jobs if job.result()}
    return len(failed)
  finally:
    queue.shutdown(cancel_futures=True)  # after an interrupt, start no further transcripts
