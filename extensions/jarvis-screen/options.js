const urlInput = document.getElementById('url')
const msg      = document.getElementById('msg')

chrome.storage.sync.get(['brain_url'], (r) => {
  urlInput.value = r.brain_url || 'https://jarvis.local'
})

document.getElementById('save').addEventListener('click', () => {
  const url = urlInput.value.trim().replace(/\/$/, '')
  if (!url) return
  chrome.storage.sync.set({ brain_url: url }, () => {
    msg.className = 'ok'
    msg.textContent = 'Saved.'
    setTimeout(() => { msg.textContent = '' }, 2000)
  })
})

document.getElementById('test').addEventListener('click', async () => {
  const url = urlInput.value.trim().replace(/\/$/, '')
  msg.className = ''
  msg.textContent = 'Testing...'
  try {
    const res = await fetch(`${url}/api/ready`, { signal: AbortSignal.timeout(5000) })
    const data = await res.json().catch(() => ({}))
    msg.className = 'ok'
    msg.textContent = data.ready ? 'Connected — brain ready.' : 'Connected (initializing).'
  } catch (e) {
    msg.className = 'err'
    msg.textContent = `Unreachable: ${e.message}`
  }
})
