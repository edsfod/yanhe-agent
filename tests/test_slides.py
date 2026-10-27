import re

import pytest
from conftest import make_video, record_files, slide_folder, slide_image
from PIL import Image, ImageDraw

import slides
import workspace

PILLARBOX = (80, 0, 560, 360)  # a 4:3 slide in a 16:9 frame


@pytest.fixture(scope='module')
def screen(tmp_path_factory):
  """A 30-second screen recording showing three slides, changing at 10 s and 20 s."""
  path = tmp_path_factory.mktemp('screen') / 'screen_示例课程__c70101s900001.mp4'
  return make_video(path, [slide_image(n, box=PILLARBOX) for n in range(3)], audio=False)


def numbers(entries):
  return [entry['number'] for entry in entries]


def test_frames_saves_named_frames_of_the_recording_autoslides_fetched(screen, course):
  lecture = course / '09月23日第3-5节'
  record_files(lecture, screen=screen)
  paths = slides.frames(lecture, [5.4, 12], 'screen')
  assert paths == [lecture / 'frames' / '屏幕-00-00-05.jpg', lecture / 'frames' / '屏幕-00-00-12.jpg']
  assert all(path.stat().st_size for path in paths)
  with pytest.raises(ValueError, match='ends at 00:00:30, before 00:00:31'):
    slides.frames(lecture, [31], 'screen')
  with pytest.raises(ValueError, match='has no classroom recording'):
    slides.frames(lecture, [1])


def test_slides_follow_the_timeline_of_autoslides(transcribed):
  entries = workspace.read_slides(transcribed)
  assert [(entry['number'], entry['time_s']) for entry in entries] == [(1, 0.0), (2, 10.0), (3, 20.0)]
  assert all(entry['file'].endswith(f'_{entry["number"]:03}.png') for entry in entries)


def test_a_slide_shown_again_or_left_out_is_not_numbered_twice(course):
  lecture = course / '09月30日第3-5节'
  folder = slide_folder(course.parent / 'autoslides-output', 'second', [(0.0, 0), (10.0, 1)])
  timeline = workspace.read_json(folder / 'timeline.json', {})
  timeline['events'] += [
    {'changeAt': 20.0, 'confirmedAt': 20.0, 'id': 'evt_3', 'initialFile': 'Slide_second_003.png'},
    {'changeAt': 25.0, 'confirmedAt': 25.0, 'id': 'evt_4', 'initialFile': 'Slide_second_004.png'},
  ]
  timeline['resolutions'] |= {
    'evt_3': {'state': 'duplicate', 'duplicateOf': 'Slide_second_001.png'},
    'evt_4': {'state': 'gap', 'reason': 'trash'},
  }
  workspace.write_json(folder / 'timeline.json', timeline)
  record_files(lecture, slides=folder)
  assert [(time_s, path and path.name) for time_s, path in workspace.shown_slides(lecture)] == [
    (0.0, 'Slide_second_001.png'),
    (10.0, 'Slide_second_002.png'),
    (20.0, 'Slide_second_001.png'),
    (25.0, None),
  ]
  assert numbers(workspace.read_slides(lecture)) == [1, 2]


def test_pick(transcribed):
  assert numbers(slides.pick(transcribed)) == [1, 2, 3]
  assert numbers(slides.pick(transcribed, [3, 1])) == [3, 1]
  with pytest.raises(ValueError, match='No slide 4, 0; slides run from 1 to 3'):
    slides.pick(transcribed, [4, 0])
  with pytest.raises(ValueError, match='has no slides'):
    slides.pick(transcribed.parent / '09月30日第3-5节')


def test_sheets_replace_older_sheets(transcribed):
  (transcribed / '.cache').mkdir(exist_ok=True)
  (transcribed / '.cache' / 'sheet-009-010.jpg').write_bytes(b'')
  paths = slides.sheets(transcribed, slides.pick(transcribed), columns=2, rows=1)
  assert [path.name for path in paths] == ['sheet-001-002.jpg', 'sheet-003-003.jpg']
  assert sorted(path.name for path in (transcribed / '.cache').glob('sheet-*')) == [path.name for path in paths]
  with Image.open(paths[0]) as image:
    assert image.width == slides.SHEET_WIDTH


def test_similar_groups_near_identical_slides(transcribed):
  near = slide_image(0)
  ImageDraw.Draw(near).rectangle((600, 340, 605, 345), fill='red')
  near.save(slides.pick(transcribed, [3])[0]['file'])
  groups = slides.similar(transcribed, slides.pick(transcribed))
  assert [numbers(group) for group in groups] == [[1, 3]]
  assert slides.similar(transcribed, slides.pick(transcribed), distance=0) == []


def test_write_pdf_has_a_page_per_slide(transcribed):
  path = slides.write_pdf(transcribed, slides.pick(transcribed, [2, 3]))
  data = path.read_bytes()
  assert path.name == '课件.pdf'
  assert data.startswith(b'%PDF')
  assert len(re.findall(rb'/Type\s*/Page(?!s)', data)) == 2
