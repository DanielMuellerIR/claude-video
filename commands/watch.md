---
description: Inspect a video URL or local file using scene frames and speech, or macOS OCR textframes for displayed slides and code.
argument-hint: <video-url-or-path> [question]
allowed-tools: [Bash, Read, AskUserQuestion]
---

Invoke the `watch` skill (defined in SKILL.md) with the user's arguments: $ARGUMENTS

Choose the scene or OCR textframe workflow from the skill according to the user's goal, then follow that workflow's requirements, invocation, untrusted-media boundary, output reading and marker-checked cleanup. If the user provided no arguments, ask them for a video URL or local path before proceeding.
