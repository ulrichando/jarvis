// JARVIS Screen Vision — popup UI
const captureBtn = document.getElementById('capture')
const queryInput = document.getElementById('query')
const responseDiv = document.getElementById('response')

captureBtn.addEventListener('click', analyze)
queryInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') analyze()
})

async function analyze() {
  const query = queryInput.value.trim()
  captureBtn.disabled = true
  captureBtn.textContent = 'Analyzing...'
  responseDiv.className = 'response empty'
  responseDiv.textContent = 'Capturing screen...'

  try {
    const result = await chrome.runtime.sendMessage({
      action: 'capture',
      query: query || undefined,
    })

    if (result.error) {
      responseDiv.className = 'response error'
      responseDiv.textContent = result.error
    } else {
      responseDiv.className = 'response'
      responseDiv.textContent = result.response
    }
  } catch (e) {
    responseDiv.className = 'response error'
    responseDiv.textContent = `Error: ${e.message}`
  }

  captureBtn.disabled = false
  captureBtn.textContent = 'Analyze'
}
