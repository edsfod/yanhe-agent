# /// script
# requires-python = '>=3.11'
# dependencies = [
#   'beautifulsoup4>=4.12',
#   'flask>=3.1',
#   'imageio-ffmpeg>=0.6',
#   'img2pdf>=0.6',
#   'keyring>=25',
#   'pillow>=11',
#   'pycryptodome>=3.23',
#   'requests>=2.32',
# ]
# ///
"""yanhe-agent: BIT lectures on 延河课堂 with the AutoSlides app - recordings, slides, transcripts and notes."""

import argparse
import math
import re
import sys
import time
from pathlib import Path

import autoslides
import download
import jobs
import transcribe
import transcript
from client import ASR, FAILURES, SORTS, Yanhekt, chosen_asr, describe, save_settings, settings, upgrade_to_campus
from workspace import (
  VIEWS,
  clock,
  count,
  course_folder,
  drop,
  holdings,
  label,
  lecture_entry,
  lectures,
  log,
  parse_time,
  read_index,
  read_slides,
  recordings,
  select,
  sync_index,
  timeline,
  transcribed,
)

# Signing in


def session():
  """A client that is signed in, also through a remembered SSO session, or None. The saved password is left to the
  sign-in page, since using it can call for a captcha or an SMS code."""
  from auth import SignIn

  upgrade_to_campus()
  client = Yanhekt()
  if client.authenticate() or SignIn(client, lambda **values: None).open():
    return client
  return None


def signed_in():
  client = session()
  if client is None:
    raise SystemExit('Not signed in; run `login`, which opens the sign-in page for the user')
  return client


def login(wait=None):
  """Sign in; failing that, open the sign-in page, print its address, and, given `wait`, wait up to that many
  seconds for the user to sign in there."""
  import server

  if session():
    print('Signed in.')
    return
  url = server.open_page()
  if url is None:  # the page's process signed in with the saved password, or gave up
    if session():
      print('Signed in.')
      return
    raise SystemExit('The sign-in ended without a session; run `login` again')
  print(f'Sign-in page: {url}', flush=True)
  if not wait:
    print('Give the user this address, then run `login --wait 90` to wait for them to sign in.')
    return
  stop_at = time.monotonic() + wait
  while server.live_page() and time.monotonic() < stop_at:
    time.sleep(2)
  if session():
    print('Signed in.')
  elif server.live_page():
    raise SystemExit('Not signed in yet; the page stays open, so run `login --wait 90` again')
  else:
    raise SystemExit('The sign-in page closed without a sign-in; run `login` to open another')


# What the targets of a command stand for


def by_course(paths):
  """Each course folder among the targets, with the lecture names given for it, or None for all its lectures."""
  courses = {}
  for path in paths:
    if read_index(path):
      courses[path] = None
    elif read_index(path.parent):
      named = courses.setdefault(path.parent, [])
      if named is not None:
        named.append(path.name)
    else:
      raise ValueError(f'{path.name} is not a course or lecture folder; run `add` first')
  return courses


def having(args, check, missing):
  """The target lectures that pass `check`; if none does, stop with `missing`, which says what to run first."""
  found = [lecture for lecture in lectures(args.paths, args.latest) if check(lecture)]
  if not found:
    raise ValueError(missing)
  return found


def settled(lecture):
  """Stop while AutoSlides is still filtering the lecture's slides, as that renumbers them under the agent."""
  session_id = lecture_entry(lecture).get('autoslides_session_id')
  if session_id and autoslides.filtering(session_id):
    raise ValueError(f'{label(lecture)}: AutoSlides is still filtering the slides; run this again in a few minutes')


def show(folder, index):
  """A course's line, then a line on what each of its lectures holds."""
  print(f'{folder.name}: {index["term"]}, {index["teacher"]}, {count(len(index["lectures"]), "lecture")}')
  for lecture in index['lectures']:
    print(f'{label(folder / lecture["id"])}: {holdings(lecture, folder / lecture["id"])}')


# The commands


def sync(args):
  """The job's work: bring the target lectures up to date - recordings, slides and transcripts, then the subtitles in
  the recordings - and end with a line on what each lecture holds."""
  courses = by_course(args.paths)
  client = signed_in()
  views = [view for view, wanted in (('classroom', not args.no_classroom), ('screen', args.screen)) if wanted]
  failed = 0
  for folder, names in courses.items():
    course = client.course(read_index(folder)['course_id'])
    failed += download.download(
      client,
      course,
      folder,
      names,
      latest=args.latest,
      videos=views,
      slides=not args.no_slides,
      transcribe=not args.no_transcribe,
      overwrite=args.overwrite,
    )
    chosen = [(folder / entry['id'], entry) for entry in select(read_index(folder), names, args.latest)]
    transcript.embed_all(lecture for lecture, _ in chosen if transcribed(lecture) and recordings(lecture))
    for lecture, entry in chosen:
      log(lecture, f'holds {holdings(entry, lecture)}')
  if failed:
    raise SystemExit(f'{count(failed, "lecture")} incomplete')


def do_login(args, workspace):
  if not args.serve:
    login(args.wait)
    return
  import server  # the page's own process

  upgrade_to_campus()
  server.sign_in(Yanhekt())


def do_courses(args, workspace):
  for course in signed_in().courses():
    if args.term in (None, course['term']):
      print(f'{course["id"]}  {course["title"]}  {course["teacher"]}  {course["term"]}')


def do_search(args, workspace):
  total, courses = signed_in().search(
    args.keyword, term=args.term, college=args.college, sort=args.sort, page=args.page, per_page=args.limit
  )
  print(f'{count(total, "course")}, page {args.page} of {math.ceil(total / args.limit)}')
  for course in courses:
    print(f'{course["id"]}  {course["title"]}  {course["teacher"]}  {course["term"]}  {course["college"]}'.rstrip())


def do_add(args, workspace):
  client = signed_in()
  for number in args.courses:
    course = client.course(number)
    folder = course_folder(args.output, course)
    show(folder, sync_index(course, folder))


def do_status(args, workspace):
  folders = args.paths or [path.resolve() for path in sorted(Path('.').iterdir()) if read_index(path)]
  if not folders:
    raise ValueError('No course folders here; run `add` first')
  if stray := [folder.name for folder in folders if not read_index(folder)]:
    raise ValueError(f'Not a course folder: {", ".join(stray)}')
  for folder in folders:
    show(folder, read_index(folder))


def do_survey(args, workspace):
  courses = by_course(args.paths)
  client = signed_in()
  for folder, names in courses.items():
    course = client.course(read_index(folder)['course_id'])
    for line in download.survey(client, course, folder, names, args.latest, args.overwrite):
      print(line, flush=True)


def do_sync(args, workspace):
  if args.job is not None:  # the job's worker
    sync(args)
    return
  by_course(args.paths)  # the targets are folders `add` made
  if not args.no_transcribe:
    chosen_asr()
  signed_in()  # rather than a job that cannot sign in
  autoslides.run('whoami')  # AutoSlides is open and signed in to Yanhekt, where it fetches from
  number = jobs.start(workspace, sys.argv[1:])
  print(f'Job {number} started; `wait` waits for it and prints where each lecture stands')


def do_wait(args, workspace):
  lines, ended = jobs.wait(workspace, args.number, args.timeout)
  print(*lines, sep='\n')
  if ended is False:
    raise SystemExit(1)


def do_jobs(args, workspace):
  found = jobs.listing(workspace)
  for state in found:
    print(jobs.report(workspace, state)[0])
  if not found:
    print('No jobs here')


def do_cancel(args, workspace):
  running = [state['id'] for state in jobs.listing(workspace) if state['state'] == 'running']
  number = args.number or (running[-1] if running else None)
  if number is None:
    raise ValueError('No job is running')
  jobs.cancel(workspace, number)
  print(f'Job {number} cancelled; run its command again to resume')


def do_doctor(args, workspace):
  import doctor

  if not doctor.run(session):
    raise SystemExit(1)


def do_config(args, workspace):
  values = settings()
  if args.asr:
    values.pop('asr', None)
    if args.asr != 'none':
      values['asr'] = args.asr
    save_settings(values)
  print(f'asr: {values["asr"]}, {ASR[values["asr"]]}' if 'asr' in values else 'asr: none chosen; transcripts wait')


def do_drop(args, workspace):
  for lecture in args.lectures:
    drop(lecture)
    print(f'{label(lecture)}: dropped')


def do_transcribe(args, workspace):
  chosen_asr()
  failed = transcribe.transcribe(having(args, recordings, 'No recordings; run `sync` first'), args.overwrite)
  if failed:
    raise SystemExit(f'{count(failed, "recording")} failed')


def do_fix(args, workspace):
  if stray := [path.name for path in args.paths if not read_index(path)]:
    raise ValueError(f'Not a course folder: {", ".join(stray)}; corrections apply to a whole course')
  for course in args.paths:
    fresh = transcript.add_corrections(course, args.pairs)
    if known := [f'{wrong} -> {right}' for wrong, right in args.pairs if (wrong, right) not in fresh]:
      print(f'{course.name}: already in 纠错.tsv: {", ".join(known)}')
    if not fresh:
      continue
    print(f'{course.name}: {count(len(fresh), "correction")} added to 纠错.tsv, which later transcripts follow too')
    stale = 0
    for lecture in filter(transcribed, lectures([course])):
      hits, changes = transcript.fix(lecture, fresh)
      applied = [f'{wrong} -> {right} {hit}x' for (wrong, right), hit in zip(fresh, hits) if hit]
      print(f'{label(lecture)}: {", ".join(applied) or "no hits"}')
      for start, before, after in changes:  # every changed sentence, to catch a pair that also hit a real word
        print(f'{label(lecture)}: [{clock(start)}] {" ".join(before.split())} -> {" ".join(after.split())}')
      if applied:
        stale += len(recordings(lecture))
    if stale:
      print(f'{course.name}: run `embed` to update the subtitles of {count(stale, "recording")}')


def do_embed(args, workspace):
  found = having(
    args,
    lambda lecture: recordings(lecture) and (args.file or transcribed(lecture)),
    'No recordings with a transcript; run `sync` first',
  )
  transcript.embed_all(found, args.file)


def do_query(args, workspace):
  if args.fast_transcript:
    source, missing = 'fast-transcript', 'No fast transcripts; run `survey` first'
    found = having(args, lambda lecture: timeline(lecture, source).exists(), missing)
  else:
    source = 'transcript'
    found = having(args, transcribed, 'No transcripts; run `transcribe` first')
  hits = []
  for lecture in found:
    if lines := list(transcript.query(lecture, source, args.match, args.start, args.end)):
      hits.append((lecture, lines))
  if not hits:
    print(f'No matching segments in {count(len(found), "lecture")}')
  for place, (lecture, lines) in enumerate(hits):
    if place:
      print()
    print(label(lecture), *lines, sep='\n')  # the lecture, then its lines, as ripgrep groups a file's matches


def do_frame(args, workspace):
  import slides  # Pillow takes a moment to load, so only the slide commands import it

  for path in slides.frames(args.lecture, args.times, args.view):
    print(f'{label(args.lecture)}: {path.relative_to(args.lecture)}')


def do_sheet(args, workspace):
  import slides

  for lecture in having(args, read_slides, 'No slides; run `sync` first'):
    settled(lecture)
    for path in slides.sheets(lecture, slides.pick(lecture), args.columns, args.rows):
      print(f'{label(lecture)}: {path.relative_to(lecture)}')


def do_similar(args, workspace):
  import slides

  for lecture in having(args, read_slides, 'No slides; run `sync` first'):
    settled(lecture)
    groups = slides.similar(lecture, slides.pick(lecture), args.distance)
    for group in groups:
      print(f'{label(lecture)}: ' + ' '.join(f'{slide["number"]:03}@{clock(slide["time_s"])}' for slide in group))
    if not groups:
      print(f'{label(lecture)}: no similar slides')


def do_pdf(args, workspace):
  import slides

  settled(args.lecture)
  chosen = slides.pick(args.lecture, args.slides)
  path = slides.write_pdf(args.lecture, chosen)
  pages = ' '.join(f'{slide["number"]:03}' for slide in chosen)
  print(f'{label(args.lecture)}: wrote {path.name}, {count(len(chosen), "page")}: {pages}')


COMMANDS = {name.removeprefix('do_'): handler for name, handler in globals().items() if name.startswith('do_')}


# Reading the command line


def course_id(value):
  """A course ID, given as it is or inside a coursedetail link."""
  if value.isdigit():
    return value
  if found := re.search(r'[?&]course_id=(\d+)', value):
    return found[1]
  raise argparse.ArgumentTypeError(f'{value!r} is not a course ID or coursedetail URL; `courses` lists the IDs')


def slide_number(value):
  return int(Path(value).stem)  # 44, 044.png or a slide image's path


def pattern(value):
  try:
    return re.compile(value, re.IGNORECASE)
  except re.error as error:
    raise argparse.ArgumentTypeError(f'{value!r} is not a regular expression: {error}') from None


def positive(value):
  number = int(value)
  if number < 1:
    raise argparse.ArgumentTypeError(f'{value} is not a positive number')
  return number


def absolute(value):
  return Path(value).resolve()  # so that `.` too is named after the folder


def parse():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--job', type=int, help=argparse.SUPPRESS)  # given to the worker of a background job
  commands = parser.add_subparsers(dest='command', required=True)
  targets = argparse.ArgumentParser(add_help=False)
  targets.add_argument('paths', nargs='+', type=absolute, metavar='target', help='Course or lecture folders')
  targets.add_argument('--latest', type=positive, metavar='N', help="Only each course folder's last N lectures")

  def command(name, text, *, takes_targets=False):
    return commands.add_parser(name, help=text, parents=[targets] if takes_targets else [])

  part = command('login', 'Sign in; failing that, open the sign-in page and print its address for the user')
  part.add_argument('--wait', type=positive, metavar='SECONDS', help='Wait up to this long for the user to sign in')
  part.add_argument('--serve', action='store_true', help=argparse.SUPPRESS)  # the page's own process
  part = command('courses', "The user's courses by term: ID, title, teacher")
  part.add_argument('--term', help='Only this term, such as 2026-2027-1')
  part = command('search', 'Search all the courses of the platform, one page at a time')
  part.add_argument('keyword', nargs='?', default='', help="Words of a course's title or a teacher's name")
  part.add_argument('--term', help='Such as 2024-2025-2')
  part.add_argument('--college', help='The college offering it, such as 计算机学院')
  part.add_argument('--sort', choices=SORTS, help="Default: the platform's order")
  part.add_argument('--page', type=int, default=1)
  part.add_argument('--limit', type=int, default=50, choices=range(1, 101), metavar='1-100')
  part = command('add', 'Make or update the folders of courses and list their lectures')
  part.add_argument('courses', nargs='+', type=course_id, metavar='course', help='A course ID or coursedetail link')
  part.add_argument('-o', '--output', type=Path, default=Path('.'), help='The workspace (default: here)')
  part = command('status', 'What each lecture holds, read offline')
  part.add_argument('paths', nargs='*', type=absolute, metavar='course', help='Default: all course folders here')
  part = command('survey', "Read the platform's fast transcripts and point out empty lectures", takes_targets=True)
  part.add_argument('--overwrite', action='store_true', help='Read them again')
  part = command('sync', 'Bring lectures up to date in a background job', takes_targets=True)
  part.add_argument('--no-classroom', action='store_true', help='Without the classroom recording')
  part.add_argument('--screen', action='store_true', help='Fetch the screen recording even with --no-slides')
  part.add_argument('--no-slides', action='store_true', help='Without the slides')
  part.add_argument('--no-transcribe', action='store_true', help='Without the transcript')
  part.add_argument('--overwrite', action='store_true', help='Make what is chosen again')
  part = command('wait', 'Wait for a background job to end and say where each lecture stands')
  part.add_argument('number', nargs='?', type=int, metavar='job', help='Default: the newest job')
  part.add_argument('--timeout', type=positive, default=90, metavar='SECONDS', help='Default: 90')
  command('jobs', 'The background jobs started here')
  part = command('cancel', 'Stop a running background job')
  part.add_argument('number', nargs='?', type=int, metavar='job', help='Default: the running job')
  command('doctor', 'Check Python, ffmpeg, AutoSlides, the network, the sign-in and the platform')
  part = command('config', 'Show the settings, or record the transcription service the user agreed to')
  part.add_argument('--asr', choices=[*ASR, 'none'], help='bcut, once the user agrees; none takes the consent back')
  part = command('drop', 'Delete empty lectures and mark them dropped in course.json')
  part.add_argument('lectures', nargs='+', type=absolute, help='Lecture folders')
  part = command('transcribe', 'Transcribe recordings into 字幕.srt with BCut', takes_targets=True)
  part.add_argument('--overwrite', action='store_true', help='Copy the sound and transcribe again')
  part = command('fix', "Add corrections to the course's 纠错.tsv and apply them to its transcripts")
  part.add_argument('paths', nargs='+', type=absolute, metavar='course', help='Course folders')
  part.add_argument('-r', dest='pairs', nargs=2, action='append', required=True, metavar=('WRONG', 'RIGHT'))
  part = command('embed', 'Put the transcript into each recording as subtitles', takes_targets=True)
  part.add_argument('--file', type=absolute, help='An .srt or .vtt file to embed instead')
  part = command('query', 'Print transcript lines, each run after the slide then on screen', takes_targets=True)
  part.add_argument('--fast-transcript', action='store_true', help="The platform's own transcript, from `survey`")
  part.add_argument('-m', '--match', type=pattern, help='A regular expression, such as 点名|签到, in any case')
  part.add_argument('--start', type=parse_time, default=0, help='Seconds or [h:]mm:ss')
  part.add_argument('--end', type=parse_time, default=math.inf, help='Seconds or [h:]mm:ss')
  part = command('frame', 'Save frames of a recording at the given times into frames/')
  part.add_argument('lecture', type=absolute)
  part.add_argument('times', nargs='+', type=parse_time, metavar='time', help='Seconds or [h:]mm:ss')
  part.add_argument('--view', choices=VIEWS, default='classroom')
  part = command('sheet', 'Contact sheets of the numbered, timed slides', takes_targets=True)
  part.add_argument('--columns', type=int, default=4, help='Fewer columns give bigger thumbnails')
  part.add_argument('--rows', type=int, default=4)
  part = command('similar', 'Groups of slides that look almost the same', takes_targets=True)
  part.add_argument('--distance', type=int, default=12, help='At most this many of 256 bits differ (default: 12)')
  part = command('pdf', 'Write 课件.pdf with one slide a page')
  part.add_argument('lecture', type=absolute)
  part.add_argument('slides', nargs='*', type=slide_number, metavar='slide', help="Slide numbers, in the pages' order")
  return parser.parse_args()


def run(args):
  COMMANDS[args.command](args, Path.cwd())


def main():
  for stream in (sys.stdout, sys.stderr):
    stream.reconfigure(encoding='utf-8')
  args = parse()
  if args.job is not None:
    raise SystemExit(jobs.work(Path.cwd(), args.job, lambda: run(args)))
  run(args)


if __name__ == '__main__':
  try:
    main()
  except KeyboardInterrupt:
    raise SystemExit(130) from None
  except FAILURES as error:
    raise SystemExit(describe(error)) from None
