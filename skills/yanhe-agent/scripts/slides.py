"""Images of a lecture: frames of a recording at given times, and contact sheets, look-alike groups and 课件.pdf from
the slides AutoSlides extracted."""

import math
import os
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageDraw, ImageFont

from workspace import VIEWS, clock, ffmpeg, label, lecture_entry, read_slides, recording, replacing

SHEET_WIDTH = 1600  # pixels across a contact sheet
CAPTION = 30  # pixels above each thumbnail for its number and time
THREADS = os.cpu_count() or 1


def save_frame(video, second, path):
  with replacing(path) as part:
    ffmpeg('-ss', f'{second:.3f}', '-i', video, '-frames:v', '1', '-q:v', '2', '-update', '1', part)


def frames(lecture, times, view='classroom'):
  """Save a frame of the view's recording at each of `times`, rounded to the second, as frames/教室-00-41-20.jpg and so
  on; return their paths."""
  video = recording(lecture, view)
  if not video.exists():
    raise ValueError(f'{label(lecture)} has no {view} recording; run `sync`')
  length = lecture_entry(lecture).get('duration_s', math.inf)
  past_end = [clock(time) for time in times if time >= length]
  if past_end:
    raise ValueError(f'{label(lecture)} ends at {clock(length)}, before {", ".join(past_end)}')
  seconds = [round(time) for time in times]
  paths = [lecture / 'frames' / f'{VIEWS[view]}-{clock(second).replace(":", "-")}.jpg' for second in seconds]
  with ThreadPoolExecutor(THREADS) as pool:
    for _ in pool.map(save_frame, [video] * len(seconds), seconds, paths):
      pass
  return paths


def pick(lecture, numbers=()):
  """All of the lecture's slides, or those with the given numbers, in that order."""
  slides = read_slides(lecture)
  if not slides:
    raise ValueError(f'{label(lecture)} has no slides; run `sync`')
  unknown = [str(number) for number in numbers if not 1 <= number <= len(slides)]
  if unknown:
    raise ValueError(f'No slide {", ".join(unknown)}; slides run from 1 to {len(slides)}')
  return [slides[number - 1] for number in numbers] if numbers else slides


def tile(sheet, slide, left, top, width, height, font):
  """Draw a slide's thumbnail on the sheet with its number and time above it."""
  with Image.open(slide['file']) as image:
    image.thumbnail((width - 8, height))
    sheet.paste(image.convert('RGB'), (left + 4, top + CAPTION))
  ImageDraw.Draw(sheet).text(
    (left + 6, top + 5), f'{slide["number"]:03}  {clock(slide["time_s"])}', fill='black', font=font
  )


def sheets(lecture, slides, columns=4, rows=4):
  """Contact sheets of the slides, `columns` by `rows` thumbnails each, saved as .cache/sheet-<first>-<last>.jpg in
  place of any earlier ones; return their paths."""
  cache = lecture / '.cache'
  cache.mkdir(exist_ok=True)
  for earlier in cache.glob('sheet-*.jpg'):
    earlier.unlink()
  width = SHEET_WIDTH // columns
  height = width * 9 // 16
  font = ImageFont.load_default(20)
  per_sheet = columns * rows
  paths = []
  for first in range(0, len(slides), per_sheet):
    batch = slides[first : first + per_sheet]
    lines = math.ceil(len(batch) / columns)
    sheet = Image.new('RGB', (SHEET_WIDTH, lines * (height + CAPTION)), 'white')
    for place, slide in enumerate(batch):
      row, column = divmod(place, columns)
      tile(sheet, slide, column * width, row * (height + CAPTION), width, height, font)
    path = cache / f'sheet-{batch[0]["number"]:03}-{batch[-1]["number"]:03}.jpg'
    sheet.save(path, quality=85)
    paths.append(path)
  return paths


def difference_hash(path, side=16):
  """A 256-bit picture of the image's structure: for a side x side greyscale copy, one bit per pixel that says
  whether it is brighter than the pixel on its right."""
  with Image.open(path) as image:
    image.draft('L', (side * 8, side * 8))  # lets a JPEG decode at a fraction of its size
    small = image.convert('L').resize((side + 1, side))
  pixels = small.load()
  bits = 0
  for y in range(side):
    for x in range(side):
      bits = bits << 1 | (pixels[x, y] > pixels[x + 1, y])
  return bits


def similar(lecture, slides, distance=12):
  """The groups, in slide order, of slides that look alike: two slides belong together when their hashes differ in
  at most `distance` of 256 bits, and so do slides linked through others."""
  hashes = [difference_hash(slide['file']) for slide in slides]
  near = [
    [other for other in range(len(slides)) if other != one and (hashes[one] ^ hashes[other]).bit_count() <= distance]
    for one in range(len(slides))
  ]
  seen, groups = set(), []
  for start in range(len(slides)):
    if start in seen:
      continue
    seen.add(start)
    members, stack = [], [start]
    while stack:
      one = stack.pop()
      members.append(one)
      fresh = [other for other in near[one] if other not in seen]
      seen.update(fresh)
      stack.extend(fresh)
    if len(members) > 1:
      groups.append([slides[i] for i in sorted(members)])
  return groups


def write_pdf(lecture, slides):
  """Write 课件.pdf with the slides' images as its pages, in the given order; return its path."""
  import img2pdf  # slow to import, and only this needs it

  path = lecture / '课件.pdf'
  path.write_bytes(img2pdf.convert([slide['file'] for slide in slides]))
  return path
