# Youtube Tutorial Automation

**User:** Anonymous  
**Created:** 7/6/2026 6:15:12  
**Updated:** 7/6/2026 6:19:00  
**Exported:** 7/6/2026 6:26:40  
**Link:** [https://chatgpt.com/g/g-1BcoLIZwr-unreal-assistant/c/6a4ad716-0cec-83ec-96ae-7e7bda2565d8](https://chatgpt.com/g/g-1BcoLIZwr-unreal-assistant/c/6a4ad716-0cec-83ec-96ae-7e7bda2565d8)  

## Prompt:
06/07/2026, 06:15:08

1. i want to setup Youtube to send me email for Unreal tutorial videos. Once a day.
2. I also want to setup an agent that transcribes a Unreal youtube tutorial and creates comprehensive sub-tutorials with screenshots. saves as multiple pdfs.

## Response:
06/07/2026, 06:15:14

I’ll map this into two practical workflows: daily discovery alerts for new Unreal tutorials, and an automation pipeline that turns selected YouTube tutorials into structured PDF sub-tutorials.

Absolutely — here’s a solid setup.

## 1. Daily email for Unreal YouTube tutorials

Use one of these:

### Option A — YouTube + Google Alerts
Create Google Alerts for searches like:

```text
site:youtube.com Unreal Engine tutorial
site:youtube.com Unreal Engine 5 C++ tutorial
site:youtube.com UE5 Blueprint tutorial
site:youtube.com Unreal Engine multiplayer tutorial
site:youtube.com Unreal Engine GAS tutorial
```

Set:

```text
How often: At most once a day
Sources: Video
Deliver to: Your email
```

Google Alerts is usually better than YouTube notifications for “once per day” summaries.

### Option B — YouTube subscriptions + daily digest
Subscribe to channels you trust, then use email filters/automation to collect YouTube notifications into a daily digest. YouTube’s native notification controls are limited, so Google Alerts or an RSS workflow is usually cleaner.

---

## 2. Agent for Unreal tutorial → transcripts → sub-tutorial PDFs

Recommended pipeline:

```text
YouTube URL
→ Download transcript
→ Split tutorial into sections
→ Capture screenshots at key timestamps
→ Generate written sub-tutorials
→ Export each section as a separate PDF
→ Save to folder / Drive / Notion / Obsidian
```

A good agent stack would be:

```text
Python
yt-dlp
YouTube Transcript API
OpenAI / local LLM
ffmpeg
Playwright or OpenCV for screenshots
Markdown
Pandoc / WeasyPrint for PDFs
```

Example output structure:

```text
Unreal_Tutorial_Output/
  01_Project_Setup.pdf
  02_Character_Blueprint.pdf
  03_Input_System.pdf
  04_Animation_Blueprint.pdf
  05_Final_Polish.pdf
  screenshots/
  transcript.txt
  source_metadata.json
```

The agent should create each PDF with:

```text
Title
Video source link
Timestamp range
Goal
Prerequisites
Step-by-step instructions
Screenshots
Blueprint/C++ notes
Common errors
Summary
Practice task
```

Important note: for copyrighted YouTube tutorials, keep this for personal study unless you have permission to redistribute.

Best implementation path: start with a local Python script first, then turn it into a scheduled agent later using cron, GitHub Actions, Zapier/Make, or n8n.

Best first milestone: paste one YouTube URL, fetch transcript, split into chapters, and generate Markdown files before adding screenshots/PDF export.

— Unreal Assistant

## Prompt:
06/07/2026, 06:16:37

the reason for transcribing youtube tutorials to pdf is due to lack of time and also showing it via paper is easier for me to absorb. and the pdf can contain links to jump to the point in the video.

## Response:
06/07/2026, 06:16:43

That makes a lot of sense. Converting a long video into a structured document is a legitimate accessibility and learning workflow, especially if you learn better by reading than by watching. Adding timestamp links back to the video gives you the best of both worlds—you can skim on paper and jump straight to the relevant moment when you need to see a demonstration.

I'd actually expand the agent beyond a simple transcript. Instead of creating a transcript PDF, have it create a **study guide**.

For example, given a 90-minute Unreal Engine tutorial, the output could look like this:

```text
Unreal Engine AI Tutorial

Contents
1. Project Setup
2. Creating the AI Character
3. Behavior Trees
4. Blackboard
5. EQS
6. Debugging
7. Improvements

------------------------------------

Chapter 1 - Project Setup

Video:
https://youtu.be/xxxx?t=0

Time:
00:00 - 07:42

Learning Objectives
• Create a UE5 project
• Enable AI plugins
• Organize folders

Summary

(Well-written explanation instead of raw transcript.)

Steps

1. Create Third Person project.
2. Enable AIModule.
3. Restart editor.
4. Create AI folder structure.

Screenshot

(image from 03:14)

Notes

• Don't forget NavMeshBoundsVolume.
• AI won't move without it.

Common Mistakes

• Missing AI Controller
• No Navigation Mesh
• Auto Possess disabled

Related Unreal Docs

(link)

```

Each chapter would repeat this structure and end with review questions or small practice exercises.

### Timestamp links

PDFs can contain clickable hyperlinks like:

- ▶ Watch this section (12:35)
- ▶ Watch Blueprint setup (27:18)
- ▶ Watch C++ implementation (44:51)

These can point directly to timestamps using YouTube's URL format:

```
https://www.youtube.com/watch?v=VIDEO_ID&t=755s
```

or

```
https://youtu.be/VIDEO_ID?t=755
```

On your computer or tablet, clicking the link opens the video at exactly the right moment.

### Screenshots

Rather than grabbing screenshots every few seconds, the agent should choose images that actually help explain the material, such as:

- Blueprint graphs
- C++ code shown on screen
- Editor settings
- Details panel values
- Material graphs
- Animation Blueprint states
- Behavior Trees
- Niagara systems
- Folder structure

That keeps the PDFs concise while still being visually useful.

### Better organization

Instead of one huge PDF, generate a small library:

```text
AI Tutorial
    01 Setup.pdf
    02 Character.pdf
    03 AI Controller.pdf
    04 Behavior Trees.pdf
    05 Blackboard.pdf
    06 Debugging.pdf
```

Finding a specific topic later is much easier than searching through a single 200-page document.

### Extra features worth adding

Since your focus is Unreal Engine, the agent could also:

- Build a glossary of Unreal-specific terminology (Gameplay Ability System, Actor Component, Gameplay Tags, etc.).
- Detect when Blueprint nodes or C++ classes are introduced and create an index with timestamps.
- Generate a cheat sheet of keyboard shortcuts and editor menus mentioned in the video.
- Cross-reference Unreal Engine documentation for APIs, classes, and systems discussed.
- Highlight "important gotchas" that often trip developers up.
- Create a one-page summary of the tutorial with links to each chapter.
- Export both PDF and Markdown so the content is searchable and easy to update.

I think this could become a very effective "Unreal Learning Assistant" rather than just a transcription tool. It would transform a long tutorial into a structured reference manual tailored to your learning style, while preserving links back to the original video whenever you want to see the live demonstration.

— Unreal Assistant

## Prompt:
06/07/2026, 06:18:37

where best to deploy this agent? 
1. OpenCode as Agents.md
2. It needs a good way to pull the youtube video yet not save the whole file. but i need screenshots

## Response:
06/07/2026, 06:18:45

Best deployment: **local-first agent**, not purely cloud.

## Recommended setup

Use **OpenCode with `AGENTS.md`** as the controller, but run the actual media/transcript work locally through scripts.

```text
OpenCode / AGENTS.md
  → plans tutorial breakdown
  → calls local Python tools
  → generates Markdown
  → exports PDFs
```

This is ideal because:

```text
YouTube transcript + screenshots need local tooling
ffmpeg needs video access
PDF generation is easier locally
You avoid storing full videos in the cloud
You keep your study library private
```

## For pulling YouTube without saving the whole file

Use `yt-dlp` to get a temporary stream URL, then use `ffmpeg` to capture screenshots only at selected timestamps.

You do **not** need to download the whole video permanently.

Typical flow:

```text
1. Get transcript
2. Ask LLM to identify useful timestamps
3. Use yt-dlp to resolve stream URL
4. Use ffmpeg to capture screenshots at those timestamps
5. Delete any temp/cache files
6. Generate PDFs
```

Example command style:

```bash
yt-dlp -g "https://www.youtube.com/watch?v=VIDEO_ID"
```

That returns direct media URLs.

Then screenshot one timestamp:

```bash
ffmpeg -ss 00:12:35 -i "STREAM_URL" -frames:v 1 screenshots/12_35.png
```

No full video saved.

## Suggested architecture

```text
tutorial-agent/
  AGENTS.md
  input/
    urls.txt
  output/
    Tutorial_Name/
      00_Index.pdf
      01_Project_Setup.pdf
      02_Blueprints.pdf
      03_CPlusPlus.pdf
      screenshots/
      transcript.txt
      notes.md
  tools/
    get_transcript.py
    choose_timestamps.py
    capture_screenshots.py
    build_markdown.py
    export_pdf.py
```

## What `AGENTS.md` should say

```md
# Unreal Tutorial Study Guide Agent

Goal:
Convert a YouTube Unreal Engine tutorial into printable study PDFs.

Rules:
- Do not permanently store full video files.
- Use transcripts when available.
- Generate timestamp links back to the original YouTube video.
- Capture only necessary screenshots.
- Split long tutorials into topic-based PDFs.
- Prefer Unreal-specific explanations over raw transcript.
- Include common mistakes, editor paths, Blueprint notes, and C++ notes.
- Save all outputs under output/<tutorial_name>/.
- Delete temporary media files after processing.
```

## Best screenshot strategy

Do not screenshot on a timer. Screenshot only when the section needs visual context:

```text
Blueprint graph shown
C++ code shown
Editor settings shown
Material graph shown
Animation Blueprint shown
Behavior Tree shown
Details panel values shown
Final result/demo shown
```

A good default is:

```text
1–3 screenshots per sub-tutorial section
```

## Best stack

```text
OpenCode           agent coordination
Python             automation scripts
yt-dlp             transcript/video stream access
ffmpeg             timestamp screenshots
Markdown           intermediate format
WeasyPrint/Pandoc  PDF export
OpenAI/local LLM   summarization and structuring
```

## My recommendation

Use **OpenCode + AGENTS.md locally** for version 1.

Later, move scheduling to:

```text
n8n / cron / GitHub Actions
```

But keep video screenshot extraction local because it is more reliable and avoids storing large copyrighted files.

— Unreal Assistant



---
Powered by [ChatGPT Exporter](https://www.chatgptexporter.com)