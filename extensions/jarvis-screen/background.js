// JARVIS Screen Vision — background service worker
// Captures the visible tab and sends to JARVIS for analysis

const JARVIS_URL = 'http://localhost:8765'

// Keyboard shortcut handler
chrome.commands.onCommand.addListener(async (command) => {
  if (command === 'capture-screen') {
    await captureAndAnalyze()
  }
})

// Message handler from popup
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === 'capture') {
    captureAndAnalyze(msg.query).then(sendResponse)
    return true // async response
  }
  if (msg.action === 'capture-only') {
    captureScreen().then(sendResponse)
    return true
  }
})

async function captureScreen() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
    if (!tab) return { error: 'No active tab' }

    const dataUrl = await chrome.tabs.captureVisibleTab(null, {
      format: 'jpeg',
      quality: 85,
    })
    return { image: dataUrl, tabTitle: tab.title, tabUrl: tab.url }
  } catch (e) {
    return { error: e.message }
  }
}

async function captureAndAnalyze(query) {
  const capture = await captureScreen()
  if (capture.error) return capture

  // Send to JARVIS
  try {
    const resp = await fetch(`${JARVIS_URL}/api/analyze-screen`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        image: capture.image,
        query: query || `What do you see on this screen? (Tab: ${capture.tabTitle})`,
      }),
    })
    const data = await resp.json()
    if (data.error) return { error: data.error }
    return { response: data.response, model: data.model }
  } catch (e) {
    return { error: `Can't reach JARVIS: ${e.message}` }
  }
}
