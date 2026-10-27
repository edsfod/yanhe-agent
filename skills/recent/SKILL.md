---
name: recent
description: 查最近几节课有没有点名、作业、考试和通知
argument-hint: "[课程名，不填则查所有课程]"
disable-model-invocation: true
---

Check the latest lectures, as in "Before you start" and "Recent lectures" in [the yanhe-agent skill](../yanhe-agent/SKILL.md), for the course and wishes given with the command.

When no course is named, look at the newest lecture of every course in the current term. Report attendance checks, assignments and deadlines, exam hints and notices with timestamps, one section per course, and say plainly when a lecture had none.
