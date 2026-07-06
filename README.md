# tldr-youtube (local)

Local (localhost-only) backend + Chrome side panel extension to generate per-video Markdown study guides, capture curated screenshots, and export A4 PDFs on demand.

## Backend (Docker)

1. Set env (optional for generation): copy `.env.example` to `.env` and set `OPENAI_API_KEY`.
2. Start backend: `docker compose up -d --build`
3. Health check: `http://127.0.0.1:4711/health`

Generated data is stored under `./data/videos/...`.

## Chrome Extension

1. Open `chrome://extensions`
2. Enable Developer mode
3. Load unpacked -> select `./extension`
4. Open a YouTube watch page, click the extension icon to open the side panel

Workflow:
- Click `Attach To Current Tab`
- If you need members-only/private videos: click `Google Sign-In`, sign in in the browser, then click `Sync Cookies`
- Click `Generate Draft` (uploads transcript from the page when available, then generates Markdown drafts)
- Edit Markdown and click `Save`
- Scrub video and use `Capture Now` or `Burst ±10s`
- Click `Export PDFs` to generate PDFs (only on demand)

Note: This project intentionally does not ask for or store your Google username/password/2FA codes. For YouTube, yt-dlp works reliably by using your browser session cookies.
