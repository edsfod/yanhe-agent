---
name: yanhe-agent
description: Work with BIT lectures on Yanhekt (延河课堂, aita.yanhekt.cn) together with the AutoSlides app - fetch lecture recordings and slides, read the platform's fast transcripts, transcribe with BCut and embed the subtitles, build slide PDFs, keep lecture and course notes, and answer questions about a course. Use for Yanhekt course links, BIT lecture replays, local lecture recordings, and requests about lecture transcripts, slides, attendance, homework, exams, study notes or review.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/yanhe_agent.py *)
---

# Yanhe Agent

## Running commands

`<yanhe-agent>` stands for `${CLAUDE_SKILL_DIR}/scripts/yanhe_agent.py`; where that variable is shown unexpanded, use the absolute path of `scripts/yanhe_agent.py` next to this file. Run every command from the user's workspace as a single `uv run <yanhe-agent> …`, with the path written out and no quotes, `cd`, variables, `&&` or pipes: a harness can approve only that form in advance. Claude Code approves these commands in the turn that invoked this skill, subagents included, so invoke the skill again at the start of every later turn that runs one; the repeat costs a line. In a sandbox that asks before each command, ask once for the prefix `uv run <yanhe-agent>`. If `uv` is missing or cannot download on its first run, see [references/setup.md](references/setup.md).

When a command fails, tell the user what it said instead of working around it: do not fetch platform addresses yourself, edit the scripts or rewrite `course.json`. Run `doctor` when the message is unclear.

## Before you start

- AutoSlides must be open, started by the user from its shortcut, and signed in; signing in there also signs in to 延河 2.0, which every command uses. Only when a command asks for `login`, run it, hand the user the page address it prints, and run `login --wait 90` until they are done. Never ask for or touch the password.
- Transcripts come from BCut, Bilibili's speech recognition, which receives each lecture's sound track. Run `config` before the first transcription; if no service is recorded, explain this to the user and run `config --asr bcut` only after they agree, passing `--no-transcribe` until then. Never agree for them.

## Workspace

| Path | What it is |
| --- | --- |
| `<course>/course.json` | the lecture list, with the paths of what AutoSlides fetched |
| `<course>/纠错.tsv` | corrections `fix` applies to all of the course's transcripts |
| `<course>/课程总结.md`, `复习提纲.md` | course notes and a requested review outline |
| `<course>/<lecture>/笔记.md` | lecture notes, one folder per lecture, such as `10月08日第6-7节` |
| `<course>/<lecture>/字幕.srt` | the transcript; edit it to correct a single lecture |
| `<course>/<lecture>/字幕.txt` | the same transcript as `[00:02:30]` lines, for reading |
| `<course>/<lecture>/课件.pdf`, `frames/` | a reviewed slide deck, and frames such as `教室-00-41-20.jpg` |

The recordings and slides stay in AutoSlides' output folder. A course folder as a target means its lectures that are not dropped, and `--latest N` keeps the last N; a lecture folder means that lecture. File formats are in [references/files.md](references/files.md), note templates in [references/notes.md](references/notes.md).

## Tasks

The notes are what the user wants; a transcript only has to be good enough to work from, so never polish it line by line.

### Organise a course

With `C` the course folder; running it again handles only new lectures.

1. Find the course with `courses` (the user's own, newest term first) or `search` (all of the platform), asking only when several fit, then `add <course>`; for a folder that exists, read `课程总结.md` and run `status`.
2. `survey C` takes seconds. Drop the empty lectures (holidays, cancelled classes, empty rooms): `survey` flags little speech and short recordings, and `query <lecture> --fast-transcript` decides unclear ones. Record each dropped lecture and why in `课程总结.md`.
3. `sync C`, then `wait` until the job ends.
4. Correct recurring BCut mistakes. BCut hears course terms as homophones and mangles campus names (`延河课堂` the lecture platform, `乐学` the learning platform, `i北理` the campus app on 钉钉). Take the right terms from the slides, search the transcripts for how they were misheard, and give `fix C -r <wrong> <right>...` only forms that are wrong everywhere in this course, such as `react` for `ReAct`, since `fix` also corrects later transcripts. A form that is a real word or part of one, such as `写作`, or `这种体` inside `这种体验`, is corrected in that lecture's `字幕.srt` instead. Leave one-off mistakes alone, and check the sentences `fix` prints, undoing any it got wrong.
5. `embed C`.
6. Make the slide decks and notes as in [Subagents](#subagents), a few lectures to each, or do it yourself one lecture at a time.
7. Fold the reports into `课程总结.md`, then tell the user which lectures were handled and dropped and what attendance checks, assignments and exam hints came up.

### Recent lectures

For questions like "上节课点名了吗" or "最近留了什么作业", read the lecture's `笔记.md`, or else its `字幕.txt`; for lectures with neither, run `survey <course folders> --latest 1` and `query <course folders> --latest 1 --fast-transcript`. Answer with timestamps, and offer to organise the course when the fast transcript is too rough to be sure.

### Slide deck

`sync <targets> --no-classroom --no-transcribe`, `wait`, then build a reviewed `课件.pdf` for each lecture as in [Slides](#slides). Name the lectures that have no screen recording.

### Questions and review

Answer from the notes, then from `query` and the slides it points to, citing timestamps and slide images. For a review outline or self-test, first organise the lectures that have no notes, then write `复习提纲.md`.

## Subagents

Pick the cheapest subagent that can see images and write files. Give it the absolute `<yanhe-agent>` path, its lecture folders and the path of `references/notes.md`, and ask it to:

1. build a reviewed `课件.pdf` as in [Slides](#slides);
2. read `字幕.txt`, `query` it to see which slide each passage belongs to, take a classroom `frame` where the teacher writes on the board or the transcript is unclear, and write `笔记.md` from the lecture template;
3. report for each lecture the page count, a one-line summary, and the attendance, assignment and exam items with timestamps.

## Commands

```sh
uv run <yanhe-agent> login [--wait 90]
uv run <yanhe-agent> config [--asr bcut|none]
uv run <yanhe-agent> doctor
uv run <yanhe-agent> courses [--term 2026-2027-1]
uv run <yanhe-agent> search [<keyword>] [--term 2024-2025-2] [--college 计算机学院] [--sort newest|oldest|popular] [--page 1] [--limit 50]
uv run <yanhe-agent> add <course>... [-o <workspace>]
uv run <yanhe-agent> status [<course folder>...]
uv run <yanhe-agent> survey <target>... [--latest N]
uv run <yanhe-agent> drop <lecture>...
uv run <yanhe-agent> sync <target>... [--latest N] [--no-classroom] [--screen] [--no-slides] [--no-transcribe] [--overwrite]
uv run <yanhe-agent> wait [<job>] [--timeout 90]
uv run <yanhe-agent> jobs
uv run <yanhe-agent> cancel [<job>]
uv run <yanhe-agent> transcribe <target>...
uv run <yanhe-agent> fix <course folder>... -r <wrong> <right>...
uv run <yanhe-agent> embed <target>... [--file <subtitles>]
uv run <yanhe-agent> query <target>... [--latest N] [--fast-transcript] [-m 点名|签到] [--start 2:30] [--end 5:00]
uv run <yanhe-agent> frame <lecture> <time>... [--view classroom|screen]
uv run <yanhe-agent> sheet <target>... [--columns 4] [--rows 4]
uv run <yanhe-agent> similar <target>... [--distance 12]
uv run <yanhe-agent> pdf <lecture> [<slide>...]
```

`<course>` is a course ID or a coursedetail link. `search` gives one page; narrow the keyword, term or college instead of paging. `status` works offline.

`survey` reads the platform's own fast transcript of each lecture in seconds. It is rougher than BCut's, good for surveys and quick checks. `drop` deletes an empty lecture's folder and leaves it out from then on; naming the folder again brings it back.

`sync` checks that AutoSlides is open and signed in, starts a background job and returns. The job looks each lecture up on Yanhekt, the platform AutoSlides downloads from, has AutoSlides fetch the classroom recording and, for slides or with `--screen`, the screen recording and its slides, transcribes each classroom recording with BCut as soon as it arrives, and embeds `字幕.srt`. Lectures Yanhekt does not have are reported and skipped. AutoSlides may go on filtering the slides for a few minutes after the job ends; until it is done, `sheet`, `similar` and `pdf` ask to be run later, because filtering renumbers the slides. Files already there are kept unless `--overwrite`. `wait` says where each lecture stands; run it again while the job runs. If the harness ends the job's process, `wait` reports it interrupted and the same `sync` carries on from there.

`transcribe` runs only the BCut step, in the foreground, for a recording saved as `录播-教室.mp4` in a lecture folder of its own. `embed` puts `字幕.srt`, or a given .srt or .vtt `--file`, into the recordings without re-encoding; run it after `fix` or a hand edit. `query` prints `[00:02:30] text` lines, starting each run with the slide on screen at the time as `slide 008: <path of its image>`; `-m` takes a regular expression and ignores case.

### Slides

The slides are what AutoSlides extracted from the screen recording and kept, numbered 1, 2, … in the order they first appear; a slide that comes back keeps its number. Desktops, editing views and every step of a slide that builds up line by line can still be among them. `sheet` writes contact sheets of numbered, timed thumbnails into `.cache/`, and `similar` groups slides that look almost the same. For a reviewed deck, pick per slide the clearest and most complete version, usually the last step of a build-up, keep lecture order, and pass the numbers to `pdf`. Each page is the slide's image, so judge the slides, not the PDF. `frame` saves frames of a recording at the given times into `frames/`.

## Writing notes

A class is two or three 45-minute periods with 5-minute breaks, so expect chatter or silence around the breaks and after the end.

Put first what students miss most easily: attendance checks, assignments and deadlines, exam hints, the teacher's warnings, platform notices and procedures. By default, map the lecture with timestamps rather than retelling it, and go deeper when asked. Write only what the transcript, slides, frames or audio back up, cite timestamps and images, and mark anything doubtful `需确认`.
