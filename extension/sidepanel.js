const API = "http://127.0.0.1:4711";

const els = {
  attachBtn: document.getElementById("attachBtn"),
  syncAuthBtn: document.getElementById("syncAuthBtn"),
  refreshBtn: document.getElementById("refreshBtn"),
  generateBtn: document.getElementById("generateBtn"),
  exportBtn: document.getElementById("exportBtn"),
  videoInfo: document.getElementById("videoInfo"),
  status: document.getElementById("status"),
  mdSelect: document.getElementById("mdSelect"),
  loadMdBtn: document.getElementById("loadMdBtn"),
  saveMdBtn: document.getElementById("saveMdBtn"),
  mdEditor: document.getElementById("mdEditor"),
  shotBtn: document.getElementById("shotBtn"),
  burstBtn: document.getElementById("burstBtn"),
  burstInterval: document.getElementById("burstInterval"),
  shots: document.getElementById("shots")
};

let current = {
  videoId: null,
  url: null
};

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  return tab;
}

async function getContextFromTab() {
  const tab = await getActiveTab();
  if (!tab?.id) throw new Error("No active tab");
  const resp = await chrome.tabs.sendMessage(tab.id, { type: "TLDR_GET_CONTEXT" });
  return { tabId: tab.id, ...resp };
}

async function extractTranscriptFromPage(tabId) {
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    func: async () => {
      try {
        const pr = window.ytInitialPlayerResponse || window.ytInitialData?.playerResponse || null;
        const tracks = pr?.captions?.playerCaptionsTracklistRenderer?.captionTracks;
        if (!tracks || !tracks.length) return { ok: false, error: "no captions" };

        const pick =
          tracks.find((t) => t?.languageCode === "en") ||
          tracks.find((t) => String(t?.vssId || "").includes(".en")) ||
          tracks[0];
        if (!pick?.baseUrl) return { ok: false, error: "missing baseUrl" };

        const base = new URL(pick.baseUrl);

        function parseSrv1(xmlText) {
          const doc = new DOMParser().parseFromString(xmlText, "text/xml");
          const nodes = Array.from(doc.querySelectorAll("transcript > text"));
          const segments = [];
          for (const n of nodes) {
            const start = Number(n.getAttribute("start"));
            const dur = Number(n.getAttribute("dur") || "0");
            const text = (n.textContent || "").replace(/\s+/g, " ").trim();
            if (!Number.isFinite(start) || !text) continue;
            const end = Number.isFinite(dur) ? start + dur : start;
            segments.push({ startSec: start, endSec: end, text });
          }
          return segments;
        }

        function parseJson3(j) {
          const events = Array.isArray(j?.events) ? j.events : [];
          const segments = [];
          for (const ev of events) {
            const tStartMs = Number(ev?.tStartMs);
            const dDurationMs = Number(ev?.dDurationMs || 0);
            const segs = Array.isArray(ev?.segs) ? ev.segs : [];
            const text = segs.map((s) => s?.utf8 || "").join("").replace(/\s+/g, " ").trim();
            if (!Number.isFinite(tStartMs) || !text) continue;
            const start = tStartMs / 1000;
            const end = start + (Number.isFinite(dDurationMs) ? dDurationMs / 1000 : 0);
            segments.push({ startSec: start, endSec: end, text });
          }
          return segments;
        }

        // Prefer srv1 (simple <transcript><text ...>) but fall back to json3.
        base.searchParams.set("fmt", "srv1");
        const r1 = await fetch(base.toString());
        if (!r1.ok) return { ok: false, error: `timedtext HTTP ${r1.status}` };
        const t1 = await r1.text();
        let segments = parseSrv1(t1);

        if (!segments.length) {
          base.searchParams.set("fmt", "json3");
          const r2 = await fetch(base.toString());
          if (!r2.ok) return { ok: false, error: `timedtext(json3) HTTP ${r2.status}` };
          const j = await r2.json();
          segments = parseJson3(j);
        }

        if (!segments.length) return { ok: false, error: "empty transcript" };
        return { ok: true, segments };
      } catch (e) {
        return { ok: false, error: String(e) };
      }
    }
  });
  return result;
}

async function tryUploadTranscript() {
  const ctx = await getContextFromTab();
  if (!ctx?.tabId) return { ok: false, error: "no active tab" };
  const tr = await extractTranscriptFromPage(ctx.tabId);
  if (!tr?.ok) return { ok: false, error: tr?.error || "transcript extraction failed" };
  const resp = await api(`/api/video/${encodeURIComponent(current.videoId)}/transcript`, {
    method: "PUT",
    body: JSON.stringify({ segments: tr.segments })
  });
  return { ok: true, segments: resp?.segments || tr.segments.length };
}

async function api(path, opts = {}) {
  const r = await fetch(API + path, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      ...(opts.headers || {})
    }
  });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(text || `HTTP ${r.status}`);
  }
  const ct = r.headers.get("content-type") || "";
  if (ct.includes("application/json")) return r.json();
  return r.text();
}

function cookiesGetAll(details) {
  return new Promise((resolve, reject) => {
    chrome.cookies.getAll(details, (cookies) => {
      const err = chrome.runtime.lastError;
      if (err) reject(new Error(err.message));
      else resolve(cookies || []);
    });
  });
}

async function syncAuthCookies() {
  // Export a minimal cookie jar for youtube/google so the backend can run yt-dlp as your logged-in session.
  const lists = await Promise.all([
    cookiesGetAll({ domain: "youtube.com" }),
    cookiesGetAll({ domain: "google.com" })
  ]);
  const cookies = lists.flat();

  // Dedupe by (domain,path,name)
  const map = new Map();
  for (const c of cookies) {
    const k = `${c.domain}|${c.path}|${c.name}`;
    map.set(k, c);
  }
  const uniq = Array.from(map.values());
  if (!uniq.length) return { ok: false, error: "no cookies found (are you logged in?)" };

  const r = await api(`/api/auth/cookies`, {
    method: "PUT",
    body: JSON.stringify({ cookies: uniq })
  });
  return { ok: true, count: r?.count ?? uniq.length };
}

function setStatus(obj) {
  els.status.textContent = typeof obj === "string" ? obj : JSON.stringify(obj, null, 2);
}

function setVideoInfo(s) {
  els.videoInfo.textContent = s;
}

function renderMdList(files) {
  els.mdSelect.innerHTML = "";
  for (const f of files) {
    const opt = document.createElement("option");
    opt.value = f;
    opt.textContent = f;
    els.mdSelect.appendChild(opt);
  }
  if (files.includes("index.md")) els.mdSelect.value = "index.md";
}

function renderShots(videoId, files) {
  els.shots.innerHTML = "";
  for (const f of files) {
    const wrap = document.createElement("div");
    wrap.className = "shot";

    const img = document.createElement("img");
    img.src = `${API}/api/video/${encodeURIComponent(videoId)}/screenshot/${encodeURIComponent(f)}`;
    img.loading = "lazy";

    const meta = document.createElement("div");
    meta.className = "meta";

    const name = document.createElement("div");
    name.className = "name";
    name.textContent = f;

    const del = document.createElement("button");
    del.textContent = "Delete";
    del.addEventListener("click", async () => {
      await api(`/api/video/${encodeURIComponent(videoId)}/screenshot/${encodeURIComponent(f)}`, { method: "DELETE" });
      await refresh();
    });

    meta.appendChild(name);
    meta.appendChild(del);

    wrap.appendChild(img);
    wrap.appendChild(meta);
    els.shots.appendChild(wrap);
  }
}

async function attach() {
  const ctx = await getContextFromTab();
  if (!ctx.videoId) throw new Error("Not a YouTube watch page (missing v=...)");
  current.videoId = ctx.videoId;
  current.url = ctx.url;
  setVideoInfo(`${ctx.videoId}`);

  // Best-effort: sync cookies first so backend yt-dlp calls use your session.
  await syncAuthCookies().catch(() => {});

  const st = await api("/api/video/from-url", { method: "POST", body: JSON.stringify({ url: ctx.url }) });
  setStatus({ ...st.status, hasTranscript: st.hasTranscript, hasSections: st.hasSections });
  renderMdList(st.markdown);
  renderShots(st.videoId, st.screenshots);
}

async function refresh() {
  if (!current.videoId) {
    setStatus("Attach to a YouTube tab first.");
    return;
  }
  const st = await api(`/api/video/${encodeURIComponent(current.videoId)}`);
  setStatus({ ...st.status, hasTranscript: st.hasTranscript, hasSections: st.hasSections });
  renderMdList(st.markdown);
  renderShots(st.videoId, st.screenshots);
}

async function loadMd() {
  if (!current.videoId) throw new Error("Attach first");
  const name = els.mdSelect.value;
  const text = await api(`/api/video/${encodeURIComponent(current.videoId)}/markdown/${encodeURIComponent(name)}`);
  els.mdEditor.value = text;
}

async function saveMd() {
  if (!current.videoId) throw new Error("Attach first");
  const name = els.mdSelect.value;
  await api(`/api/video/${encodeURIComponent(current.videoId)}/markdown/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: JSON.stringify({ content: els.mdEditor.value })
  });
  await refresh();
}

async function generateDraft() {
  if (!current.videoId) throw new Error("Attach first");

  // If the backend doesn't already have a transcript, we must upload one from the page.
  const st0 = await api(`/api/video/${encodeURIComponent(current.videoId)}`);
  if (!st0?.hasTranscript) {
    const up = await tryUploadTranscript();
    if (!up?.ok) {
      setStatus(`Transcript required but upload failed: ${up?.error || "unknown error"}`);
      return;
    }
  }

  const r = await api(`/api/video/${encodeURIComponent(current.videoId)}/generate-draft`, { method: "POST" });
  setStatus(r.status);
  // Poll a bit.
  for (let i = 0; i < 15; i++) {
    await new Promise((res) => setTimeout(res, 1500));
    const st = await api(`/api/video/${encodeURIComponent(current.videoId)}`);
    setStatus(st.status);
    if (st.status?.generation?.state === "done" || st.status?.generation?.state === "error") break;
  }
  await refresh();
}

async function exportPdf() {
  if (!current.videoId) throw new Error("Attach first");
  const r = await api(`/api/video/${encodeURIComponent(current.videoId)}/export-pdf`, {
    method: "POST",
    body: JSON.stringify({ which: "all" })
  });
  setStatus(r.status);
  for (let i = 0; i < 20; i++) {
    await new Promise((res) => setTimeout(res, 1500));
    const st = await api(`/api/video/${encodeURIComponent(current.videoId)}`);
    setStatus(st.status);
    if (st.status?.pdf?.state === "done" || st.status?.pdf?.state === "error") break;
  }
  await refresh();
}

async function captureNow() {
  if (!current.videoId) throw new Error("Attach first");
  const ctx = await getContextFromTab();
  if (ctx.currentTime == null) throw new Error("No <video> element found");
  await api(`/api/video/${encodeURIComponent(current.videoId)}/screenshot`, {
    method: "POST",
    body: JSON.stringify({ t_sec: ctx.currentTime, format: "png" })
  });
  await refresh();
}

async function captureBurst() {
  if (!current.videoId) throw new Error("Attach first");
  const ctx = await getContextFromTab();
  if (ctx.currentTime == null) throw new Error("No <video> element found");
  const interval = Number(els.burstInterval.value);
  await api(`/api/video/${encodeURIComponent(current.videoId)}/screenshot/burst`, {
    method: "POST",
    body: JSON.stringify({ center_sec: ctx.currentTime, range_sec: 10, interval_sec: interval, format: "png" })
  });
  await refresh();
}

function wire() {
  els.attachBtn.addEventListener("click", () => attach().catch((e) => setStatus(String(e))));
  els.syncAuthBtn.addEventListener("click", () => syncAuthCookies().then(setStatus).catch((e) => setStatus(String(e))));
  els.refreshBtn.addEventListener("click", () => refresh().catch((e) => setStatus(String(e))));
  els.loadMdBtn.addEventListener("click", () => loadMd().catch((e) => setStatus(String(e))));
  els.saveMdBtn.addEventListener("click", () => saveMd().catch((e) => setStatus(String(e))));
  els.generateBtn.addEventListener("click", () => generateDraft().catch((e) => setStatus(String(e))));
  els.exportBtn.addEventListener("click", () => exportPdf().catch((e) => setStatus(String(e))));
  els.shotBtn.addEventListener("click", () => captureNow().catch((e) => setStatus(String(e))));
  els.burstBtn.addEventListener("click", () => captureBurst().catch((e) => setStatus(String(e))));
}

wire();
