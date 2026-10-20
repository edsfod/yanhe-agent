"""Shared fixtures: the scripts on the import path, course folders and short synthetic recordings."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from imageio_ffmpeg import get_ffmpeg_exe
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parents[1] / 'skills' / 'yanhe-agent' / 'scripts'))

import auth
import autoslides
import client
import server
import transcript
from workspace import write_json

SIZE = (640, 360)


def slide_image(seed, size=SIZE, box=None):
  """A slide-like picture: a white page with dark bars whose layout depends on `seed`, drawn inside `box`."""
  image = Image.new('RGB', size, 'black' if box else 'white')
  draw = ImageDraw.Draw(image)
  left, top, right, bottom = box or (0, 0, *size)
  draw.rectangle((left, top, right - 1, bottom - 1), fill='white')
  height = bottom - top
  for line in range(6):
    y = top + height * (line + 1) // 8
    width = (right - left) * (3 + (seed * 7 + line * 3) % 6) // 10
    draw.rectangle((left + 20, y, left + 20 + width, y + height // 20), fill=(30, 30 + seed * 40 % 200, 90))
  return image


def make_video(path, images, seconds=10, fps=5, audio=True):
  """Write an H.264 MP4 that shows each image for `seconds`, or for its own entry when `seconds` is a list, with a
  keyframe every second as the platform's have."""
  path.parent.mkdir(parents=True, exist_ok=True)
  lengths = seconds if isinstance(seconds, list) else [seconds] * len(images)
  args = []
  for n, (image, length) in enumerate(zip(images, lengths)):
    still = path.parent / f'.still-{n}.png'
    image.save(still)
    args += ['-loop', '1', '-framerate', str(fps), '-t', str(length), '-i', still]
  graph = ''.join(f'[{n}:v]' for n in range(len(images))) + f'concat=n={len(images)}:v=1:a=0[v]'
  sound = ['-f', 'lavfi', '-t', str(sum(lengths)), '-i', 'sine=frequency=440'] if audio else []
  maps = ['-map', '[v]'] + (['-map', f'{len(images)}:a', '-c:a', 'aac'] if audio else [])
  subprocess.run(
    [get_ffmpeg_exe(), '-v', 'error', '-y', *args, *sound, '-filter_complex', graph, *maps, '-c:v', 'libx264',
     '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-g', str(fps), '-keyint_min', str(fps), '-sc_threshold', '0',
     path],
    check=True,
  )  # fmt: skip
  for n in range(len(images)):
    (path.parent / f'.still-{n}.png').unlink()
  return path


def streams(video):
  """The stream lines that ffmpeg prints for a file, such as `Stream #0:2(zho): Subtitle: mov_text`."""
  command = [get_ffmpeg_exe(), '-hide_banner', '-i', video]
  result = subprocess.run(command, capture_output=True, encoding='utf-8', errors='replace', check=False)
  return [line.strip() for line in result.stderr.splitlines() if line.strip().startswith('Stream #')]


@pytest.fixture(autouse=True)
def config(tmp_path_factory, monkeypatch):
  """A settings folder of the test's own, never the user's, where the user has chosen BCut."""
  folder = tmp_path_factory.mktemp('config')
  monkeypatch.setattr(client, 'CONFIG', folder)
  monkeypatch.setattr(client, 'SETTINGS', folder / 'config.json')
  monkeypatch.setattr(auth, 'USERNAME', folder / 'username')
  monkeypatch.setattr(server, 'PAGE', folder / 'signin.json')
  monkeypatch.setattr(server, 'CONFIG', folder)
  monkeypatch.setattr(autoslides, 'user_data', lambda: folder / 'AutoSlides')
  state = folder / 'autoslides-world.json'  # the stand-in app knows no courses until a test gives it some
  state.write_text(json.dumps({'output': str(folder / 'autoslides-output'), 'sessions': {}}), encoding='utf-8')
  monkeypatch.setenv('YANHE_AGENT_AUTOSLIDES', str(Path(__file__).with_name('fake_autoslides.py')))
  monkeypatch.setenv('FAKE_AUTOSLIDES_STATE', str(state))
  client.save_settings({'asr': 'bcut'})
  return folder


@pytest.fixture
def course(tmp_path):
  """A course folder with two lectures and one dropped, as `add` leaves it."""
  folder = tmp_path / '示例课程'
  index = {
    'course_id': '10001',
    'title': '示例课程',
    'teacher': '王老师',
    'term': '2026-2027-1',
    'lectures': [
      {'id': '09月23日第3-5节', 'sub_id': '200001', 'duration_s': 30.0},
      {'id': '09月30日第3-5节', 'sub_id': '200002', 'duration_s': 30.0},
      {'id': '10月01日第3-5节', 'sub_id': '200003', 'dropped': True},
    ],
  }
  write_json(folder / 'course.json', index)
  for lecture in index['lectures'][:2]:
    (folder / lecture['id']).mkdir()
  return folder


def slide_folder(output, name, shown):
  """A slides_<name> folder as AutoSlides leaves it: Slide_<name>_001.png and so on, one per (seconds, seed) in
  `shown`, and timeline.json with each as a canonical slide."""
  folder = output / f'slides_{name}'
  folder.mkdir(parents=True)
  events, resolutions = [], {}
  for n, (time_s, seed) in enumerate(shown, 1):
    file = f'Slide_{name}_{n:03}.png'
    slide_image(seed).save(folder / file)
    events.append({'changeAt': time_s, 'confirmedAt': time_s, 'id': f'evt_{n}', 'initialFile': file})
    resolutions[f'evt_{n}'] = {'file': file, 'state': 'canonical'}
  timeline = {'events': events, 'extractor': 'qt', 'resolutions': resolutions, 'version': 1, 'kind': 'recorded'}
  write_json(folder / 'timeline.json', timeline)
  return folder


def record_files(lecture, **files):
  """Record paths in the lecture's `files` in course.json, as `sync` does for what AutoSlides fetched."""
  path = lecture.parent / 'course.json'
  index = json.loads(path.read_text(encoding='utf-8'))
  entry = next(row for row in index['lectures'] if row['id'] == lecture.name)
  entry.setdefault('files', {}).update({view: str(value) for view, value in files.items()})
  write_json(path, index)


@pytest.fixture
def transcribed(course):
  """The course's first lecture with a BCut transcript and three slides that AutoSlides extracted."""
  lecture = course / '09月23日第3-5节'
  rows = [(1.0, 4.5, '今天讲 流水线'), (12.0, 15.0, '下面点名'), (21.0, 25.0, '作业是第三章'), (26.0, 28.0, '下课')]
  transcript.write(lecture, rows)
  folder = slide_folder(
    course.parent / 'autoslides-output', '示例课程__c70101s900001', [(0.0, 0), (10.0, 1), (20.0, 2)]
  )
  record_files(lecture, slides=folder)
  return lecture


def read_json(path):
  return json.loads(Path(path).read_text(encoding='utf-8'))
