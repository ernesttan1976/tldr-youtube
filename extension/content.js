function getVideoId() {
  const url = new URL(location.href);
  return url.searchParams.get("v");
}

function getCurrentTime() {
  const v = document.querySelector("video");
  if (!v) return null;
  return v.currentTime;
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "TLDR_GET_CONTEXT") {
    sendResponse({
      videoId: getVideoId(),
      currentTime: getCurrentTime(),
      url: location.href
    });
    return true;
  }
  return false;
});
