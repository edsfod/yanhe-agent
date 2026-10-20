# Files in the workspace

## The course index, course.json

```json
{
  "course_id": "10001",
  "title": "示例课程",
  "teacher": "王老师",
  "term": "2026-2027-1",
  "code": "20262027110000000101",
  "autoslides_course_id": "70101",
  "lectures": [
    {
      "id": "09月23日第3-5节", "sub_id": "200001", "begin": 1790140800, "duration_s": 8100.0,
      "autoslides_session_id": "900001",
      "files": {
        "classroom": "D:\\AutoSlides\\camera_示例课程_第3周_星期二_第3大节__c70101s900001.mp4",
        "screen": "D:\\AutoSlides\\screen_示例课程_第3周_星期二_第3大节__c70101s900001.mp4",
        "slides": "D:\\AutoSlides\\slides_示例课程_第3周_星期二_第3大节__c70101s900001"
      }
    },
    {"id": "10月01日第3-5节", "sub_id": "200002", "begin": 1790745600, "dropped": true}
  ]
}
```

`lectures` has one entry per replay on 延河 2.0, in the order the classes took place. `id` names the lecture's folder and comes from the platform's title (`2026-9-23第3-5节` becomes `09月23日第3-5节`); `sub_id` is the replay's ID on the platform and `begin` its start time in seconds since 1970. `duration_s` is filled in once `survey` or `sync` has looked the lecture up, and `dropped` marks an empty lecture whose folder `drop` removed.

`code` is the course's registrar code (`kcwybm`), the same on Yanhekt. `sync` uses it to find the course there through AutoSlides, stores that course's ID as `autoslides_course_id`, and gives each lecture, as `autoslides_session_id`, the Yanhekt session that starts at the same moment. `files` lists where AutoSlides put what it fetched: the `classroom` and `screen` recordings and the `slides` folder, all in its output folder. A lecture without `files` uses a recording saved in its own folder as `录播-教室.mp4` or `录播-屏幕.mp4`.

## Transcripts

`字幕.srt` is the transcript itself, an ordinary SubRip file from BCut. `fix`, a hand edit or a subtitle editor changes it, and the other transcript files are rebuilt from it. `字幕.txt` is rewritten from it, one `[00:02:30] text` line per segment, each time a command reads the transcript.

Inside the lecture's `.cache/`: `bcut.json` keeps BCut's raw result as `[start, end, text]` rows, so a deleted `字幕.srt` can be rebuilt without uploading again; `bcut-task.json` holds the ID of a BCut task until its result comes back; `embedded.json` records which version of `字幕.srt` each recording carries; and `fast-transcript.tsv` holds the platform's fast transcript as tab-separated `start_s`, `end_s`, `text` rows sorted by start. A `transcript.tsv` left by an older version is turned into `字幕.srt` the first time a command reads it and kept as `transcript.old.tsv`.

`纠错.tsv` in the course folder holds the course's corrections as tab-separated `wrong`, `right` rows under that header, applied top to bottom.

## Slides

The `slides` folder belongs to AutoSlides. It holds `Slide_<recording>_001.png` and onwards, and `timeline.json`. In that file, `events` lists every change of the screen with `changeAt`, its time in seconds of the recording, and `resolutions` says what each change shows: a slide of its own (`canonical`, with `file`), a slide seen before (`duplicate`, with `duplicateOf`), or a stretch AutoSlides left out (`gap`), such as a desktop. A slide stays on screen until the next change, and slides are numbered 1, 2, … in the order they first appear.

A file whose name has `.part` before its extension is still being written.

## Settings

`~/.config/yanhe-agent/` holds yanhe-agent's own sign-in (`cookies.txt` and `username`) and `config.json`, whose `asr` names the transcription service the user agreed to, such as `bcut`; the password is kept in the system keychain. Commands first use the Yanhe 2.0 sign-in that AutoSlides keeps in `autoslides-config.json` in its settings folder (`%APPDATA%\AutoSlides` on Windows), reading it without copying it.

## Background jobs

The workspace's `.yanhe-agent/jobs/` holds `<number>.json` and `<number>.log` for each job. The JSON records the command, its state (`running`, `done`, `failed` or `cancelled`), when it started, the worker's process ID, and a heartbeat the worker renews every 5 seconds; a running job whose heartbeat is more than 30 seconds old is taken as interrupted. The log is the command's output.
