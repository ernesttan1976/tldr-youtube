const API = "http://127.0.0.1:4711";

const LS_AUTO_SYNC_AUTH = "tldr.autoSyncAuth";

const els = {
  attachBtn: document.getElementById("attachBtn"),
  syncAuthBtn: document.getElementById("syncAuthBtn"),
  clearAuthBtn: document.getElementById("clearAuthBtn"),
  signInBtn: document.getElementById("signInBtn"),
  autoSyncAuth: document.getElementById("autoSyncAuth"),
  authInfo: document.getElementById("authInfo"),
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

function loadAutoSyncAuth() {
  const raw = localStorage.getItem(LS_AUTO_SYNC_AUTH);
  if (raw == null) return true;
  return raw === "true";
}

function saveAutoSyncAuth(v) {
  localStorage.setItem(LS_AUTO_SYNC_AUTH, v ? "true" : "false");
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  return tab;
}

async function getContextFromTab() {
  const tab = await getActiveTab();
  if (!tab?.id) throw new Error("No active tab");

  // Don't rely on content script wiring (it may not be injected yet). Read context via executeScript.
  // This avoids: "Could not establish connection. Receiving end does not exist."
  const url = tab.url || "";
  if (!/^https?:\/\//.test(url)) throw new Error("Active tab URL is not accessible");
  if (!url.includes("youtube.com/")) throw new Error("Active tab is not a YouTube page");

  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => {
      const href = location.href;
      let videoId = null;
      try {
        const u = new URL(href);
        videoId = u.searchParams.get("v");
        if (!videoId) {
          const m = u.pathname.match(/^\/shorts\/([^/?#]+)/);
          if (m) videoId = m[1];
        }
      } catch {
        // ignore
      }
      const v = document.querySelector("video");
      const currentTime = v ? v.currentTime : null;
      return { url: href, videoId, currentTime };
    }
  });

  return { tabId: tab.id, ...(result || {}) };
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

function fmtBytes(n) {
  if (!Number.isFinite(n)) return "";
  if (n < 1024) return `${n} B`;
  const kb = n / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  return `${mb.toFixed(1)} MB`;
}

async function refreshAuthInfo() {
  if (!els.authInfo) return;
  try {
    const st = await api("/api/auth/status");
    if (!st?.hasCookies) {
      els.authInfo.textContent = "No synced cookies";
      return;
    }
    const when = st.updatedAt ? new Date(st.updatedAt).toLocaleString() : "";
    const size = fmtBytes(Number(st.sizeBytes));
    els.authInfo.textContent = `Synced ${when}${size ? ` (${size})` : ""}`;
  } catch {
    els.authInfo.textContent = "Auth status unavailable";
  }
}

async function openGoogleSignIn() {
  // We do not collect or store Google passwords/2FA codes.
  // User signs in inside the browser, then we sync cookies to the local backend.
  const url =
    "https://accounts.google.com/ServiceLogin?service=youtube&continue=https%3A%2F%2Fwww.youtube.com%2F";
  await chrome.tabs.create({ url });
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
  await refreshAuthInfo();
  return { ok: true, count: r?.count ?? uniq.length };
}

async function clearAuthCookies() {
  await api("/api/auth/cookies", { method: "DELETE" });
  await refreshAuthInfo();
  return { ok: true };
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
  if (els.autoSyncAuth?.checked) {
    await syncAuthCookies().catch(() => {});
  }

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

  const r = await api(`/api/video/${encodeURIComponent(current.videoId)}/generate-draft`, { method: "POST" });
  setStatus(r.status);
  // Poll for a while (ASR can take minutes on long videos).
  const start = Date.now();
  const maxMs = 10 * 60 * 1000;
  while (Date.now() - start < maxMs) {
    await new Promise((res) => setTimeout(res, 2500));
    const st = await api(`/api/video/${encodeURIComponent(current.videoId)}`);
    setStatus({ ...st.status, hasTranscript: st.hasTranscript, hasSections: st.hasSections });
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
  if (els.autoSyncAuth) {
    els.autoSyncAuth.checked = loadAutoSyncAuth();
    els.autoSyncAuth.addEventListener("change", () => saveAutoSyncAuth(els.autoSyncAuth.checked));
  }

  if (els.signInBtn) els.signInBtn.addEventListener("click", () => openGoogleSignIn().catch((e) => setStatus(String(e))));
  els.attachBtn.addEventListener("click", () => attach().catch((e) => setStatus(String(e))));
  els.syncAuthBtn.addEventListener("click", () => syncAuthCookies().then(setStatus).catch((e) => setStatus(String(e))));
  if (els.clearAuthBtn) els.clearAuthBtn.addEventListener("click", () => clearAuthCookies().then(setStatus).catch((e) => setStatus(String(e))));
  els.refreshBtn.addEventListener("click", () => refresh().catch((e) => setStatus(String(e))));
  els.loadMdBtn.addEventListener("click", () => loadMd().catch((e) => setStatus(String(e))));
  els.saveMdBtn.addEventListener("click", () => saveMd().catch((e) => setStatus(String(e))));
  els.generateBtn.addEventListener("click", () => generateDraft().catch((e) => setStatus(String(e))));
  els.exportBtn.addEventListener("click", () => exportPdf().catch((e) => setStatus(String(e))));
  els.shotBtn.addEventListener("click", () => captureNow().catch((e) => setStatus(String(e))));
  els.burstBtn.addEventListener("click", () => captureBurst().catch((e) => setStatus(String(e))));
}

wire();
refreshAuthInfo();
