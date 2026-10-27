"""A stand-in for the AutoSlides command line, run as `fake_autoslides.py --cli=autoslides -- <command> … --json`.

Its world is the JSON file named by FAKE_AUTOSLIDES_STATE: `courses` maps registrar codes to Yanhekt course IDs,
`sessions` lists each course's sessions, `templates` names the files a download copies, `extract` says whether slides
are extracted after downloads, and `forget` makes the Download panel lose its rows, as a reload of the window does.
A download finishes at once and leaves the files, the slides folder and its row in `rows`.
"""

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

STATE = Path(os.environ['FAKE_AUTOSLIDES_STATE'])


def answer(data=None, error=None):
  print(json.dumps({'ok': False, 'error': error} if error else {'ok': True, 'data': data or {}}))
  sys.exit(1 if error else 0)


def download(state, args):
  kinds = {'both': ['camera', 'screen'], 'camera': ['camera'], 'screen': ['screen']}[args[args.index('--stream') + 1]]
  sessions = [arg for arg in args if arg.isdigit()]
  course = next(course for course, rows in state['sessions'].items() if any(row['id'] == sessions[0] for row in rows))
  output, items = Path(state['output']), []
  for session in sessions:
    for kind in kinds:
      path = output / f'{kind}_lecture{session}__c{course}s{session}.mp4'
      if path.exists() and '--force' not in args:
        items.append({'session_id': session, 'stream': kind, 'status': 'skipped', 'path': str(path)})
        continue
      output.mkdir(parents=True, exist_ok=True)
      shutil.copyfile(state['templates'][kind], path)
      extraction = None
      if kind == 'screen' and state.get('extract', True):
        slides = output / f'slides_lecture{session}__c{course}s{session}'
        slides.mkdir(exist_ok=True)
        file = f'Slide_lecture{session}_001.png'
        shutil.copyfile(state['templates']['slide'], slides / file)
        timeline = {
          'events': [{'changeAt': 0, 'id': 'e1', 'initialFile': file}],
          'resolutions': {'e1': {'file': file, 'state': 'canonical'}},
        }
        (slides / 'timeline.json').write_text(json.dumps(timeline), encoding='utf-8')
        extraction = {'state': state.get('extraction_state', 'completed'), 'progress': 100}
      state['rows'].append({
        'item_id': f'{session}-{kind}',
        'session_id': session,
        'kind': kind,
        'status': 'completed',
        'progress': 100,
        'path': str(path),
        'added_at': datetime.now().isoformat(),
        'extraction': extraction,
      })  # fmt: skip
      items.append({'session_id': session, 'stream': kind, 'status': 'queued', 'path': str(path)})
  return {'output_dir': str(output), 'items': items}


def main():
  args = sys.argv[sys.argv.index('--') + 1 :]
  state = json.loads(STATE.read_text(encoding='utf-8'))
  state.setdefault('rows', [])
  command, rest = args[0], args[1:]
  if command == 'whoami':
    answer({'signed_in': True, 'account': '学生', 'badge': '1120000001'})
  elif command == 'config':
    answer({'settings': {'output_dir': state.get('output'), 'extract_after_download': state.get('extract', True)}})
  elif command == 'search':
    found = state.get('courses', {}).get(rest[0])
    answer({'courses': [{'id': found, 'title': '示例课程'}] if found else []})
  elif command == 'course':
    answer({'course': {'id': rest[0]}, 'sessions': state['sessions'].get(rest[0], [])})
  elif command == 'download':
    data = download(state, rest)
    STATE.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    answer(data)
  elif command == 'downloads':
    answer({'items': [] if state.get('forget') else state['rows']})
  answer(error={'code': 'usage', 'message': f'unknown command {command}'})


main()
