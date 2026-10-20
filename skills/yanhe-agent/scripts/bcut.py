"""BCut (必剪), Bilibili's free speech recognition: send a sound track, start a recognition task, collect its result."""

import json
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

BASE = 'https://member.bilibili.com/x/bcut/rubick-interface'
MODEL_ID = 7
THROTTLED = (412, 429)  # what Bilibili answers when requests come too fast; it lets them through after minutes
PAUSES = (30, 60, 120, 240)  # seconds to wait before each new try while throttled
DONE, FAILED = 4, 3  # task states


class Rejected(RuntimeError):
  """BCut could not recognise the sound it was sent."""


class BCut:
  """A BCut session; `say` is told when Bilibili throttles it."""

  def __init__(self, say):
    self.say = say
    self.http = requests.Session()
    self.http.headers['User-Agent'] = 'Mozilla/5.0'
    flaky = Retry(3, backoff_factor=2, status_forcelist=[500, 502, 503, 504], allowed_methods=None)
    self.http.mount('https://', HTTPAdapter(max_retries=flaky))

  def ask(self, method, path, **request):
    """The `data` of a call to the interface, waiting out throttling."""
    pauses = iter(PAUSES)
    while True:
      reply = self.http.request(method, BASE + path, timeout=60, **request)
      pause = next(pauses, None) if reply.status_code in THROTTLED else None
      if pause is None:
        break
      self.say(f'BCut is rate limiting; retrying in {pause}s')
      time.sleep(pause)
    reply.raise_for_status()
    answer = reply.json()
    if answer['code'] != 0:
      raise RuntimeError(f'BCut {path}: {answer.get("message") or answer["code"]}')
    return answer['data']

  def upload(self, audio):
    """Send the .m4a file in the pieces BCut asks for; return the address of the stored sound."""
    size = audio.stat().st_size
    slot = self.ask(
      'POST',
      '/resource/create',
      data={'type': 2, 'name': audio.name, 'size': size, 'resource_file_type': 'm4a', 'model_id': MODEL_ID},
    )
    tags = []
    with audio.open('rb') as sound:
      for address in slot['upload_urls']:
        stored = self.http.put(address, data=sound.read(slot['per_size']), timeout=120)
        stored.raise_for_status()
        tags.append(stored.headers['Etag'])
    finished = {
      'in_boss_key': slot['in_boss_key'],
      'resource_id': slot['resource_id'],
      'etags': ','.join(tags),
      'upload_id': slot['upload_id'],
      'model_id': MODEL_ID,
    }
    return self.ask('POST', '/resource/create/complete', data=finished)['download_url']

  def submit(self, resource):
    """Start recognising the stored sound at `resource`; return the task's ID."""
    return self.ask('POST', '/task', json={'resource': resource, 'model_id': str(MODEL_ID)})['task_id']

  def result(self, task):
    """The recognised (start, end, text) rows of the task, in seconds, or None while it is still running."""
    state = self.ask('GET', '/task/result', params={'model_id': MODEL_ID, 'task_id': task})
    if state['state'] == FAILED:
      raise Rejected(f'BCut task failed: {state.get("remark") or "no reason given"}')
    if state['state'] != DONE:
      return None
    return [
      (u['start_time'] / 1000, u['end_time'] / 1000, u['transcript']) for u in json.loads(state['result'])['utterances']
    ]
