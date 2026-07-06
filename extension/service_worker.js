chrome.runtime.onInstalled.addListener(() => {
  // Enable side panel for YouTube watch pages.
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});
