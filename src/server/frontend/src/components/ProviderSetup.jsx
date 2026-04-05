import { useState, useEffect } from 'react'

/**
 * ProviderSetup — wizard that appears when no AI providers are available.
 * Lets the user either:
 * 1. Enter an API key for a cloud provider (Anthropic, Groq, OpenAI, etc.)
 * 2. Connect to a local Ollama instance
 * 3. Pull a model via Ollama
 */

const PROVIDERS = [
  { id: 'groq', name: 'Groq', type: 'openai', base_url: 'https://api.groq.com/openai/v1',
    model: 'llama-3.3-70b-versatile', hint: 'Free at console.groq.com', color: '#f55036' },
  { id: 'anthropic', name: 'Anthropic (Claude)', type: 'anthropic', base_url: 'https://api.anthropic.com',
    model: 'claude-sonnet-4-20250514', hint: 'console.anthropic.com', color: '#d4a574' },
  { id: 'openai', name: 'OpenAI', type: 'openai', base_url: 'https://api.openai.com/v1',
    model: 'gpt-4o', hint: 'platform.openai.com', color: '#10a37f' },
  { id: 'openrouter', name: 'OpenRouter', type: 'openai', base_url: 'https://openrouter.ai/api/v1',
    model: 'anthropic/claude-sonnet-4', hint: 'Many models, one key — openrouter.ai', color: '#6366f1' },
  { id: 'together', name: 'Together AI', type: 'openai', base_url: 'https://api.together.xyz/v1',
    model: 'meta-llama/Llama-3.3-70B-Instruct-Turbo', hint: 'api.together.xyz', color: '#0ea5e9' },
  { id: 'xai', name: 'xAI (Grok)', type: 'openai', base_url: 'https://api.x.ai/v1',
    model: 'grok-3', hint: 'console.x.ai', color: '#fff' },
]

export default function ProviderSetup({ isOpen, onClose }) {
  const [tab, setTab] = useState('cloud') // 'cloud' | 'local'
  const [selected, setSelected] = useState(null)
  const [apiKey, setApiKey] = useState('')
  const [status, setStatus] = useState('') // '' | 'testing' | 'success' | 'error'
  const [errorMsg, setErrorMsg] = useState('')
  const [ollamaStatus, setOllamaStatus] = useState('checking') // 'checking' | 'online' | 'offline'
  const [ollamaModels, setOllamaModels] = useState([])
  const [pullModel, setPullModel] = useState('')
  const [pulling, setPulling] = useState(false)
  const [hfModels, setHfModels] = useState([])
  const [hfSearch, setHfSearch] = useState('')
  const [hfSearching, setHfSearching] = useState(false)
  const [hfDownloading, setHfDownloading] = useState('')
  const [downloadProgress, setDownloadProgress] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState('')
  const [currentModel, setCurrentModel] = useState('')
  const [switching, setSwitching] = useState('')

  // Check Ollama status and current model on mount
  useEffect(() => {
    if (!isOpen) return
    fetch('/api/ollama/status')
      .then(r => r.json())
      .then(data => {
        setOllamaStatus(data.online ? 'online' : 'offline')
        setOllamaModels(data.models || [])
      })
      .catch(() => setOllamaStatus('offline'))
    fetch('/api/provider/current')
      .then(r => r.json())
      .then(data => {
        setCurrentModel(data.model || '')
        // Also check Ollama specifically for the local tab
        const ollama = (data.all_providers || []).find(p => p.name === 'ollama')
        if (ollama) setCurrentModel(prev => data.provider === 'ollama' ? data.model : ollama.model)
      })
      .catch(() => {})
  }, [isOpen])

  const testAndSave = async () => {
    if (!selected || !apiKey.trim()) return
    setStatus('testing')
    setErrorMsg('')

    try {
      const resp = await fetch('/api/provider/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: selected.id,
          type: selected.type,
          api_key: apiKey.trim(),
          base_url: selected.base_url,
          model: selected.model,
        }),
      })
      const data = await resp.json()
      if (data.ok) {
        setStatus('success')
        setTimeout(() => onClose(), 1500)
      } else {
        setStatus('error')
        setErrorMsg(data.error || 'Failed to connect')
      }
    } catch (e) {
      setStatus('error')
      setErrorMsg(e.message)
    }
  }

  const connectOllama = async (model) => {
    try {
      const resp = await fetch('/api/provider/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: 'ollama',
          type: 'openai',
          api_key: 'ollama',
          base_url: 'http://localhost:11434/v1',
          model: model,
        }),
      })
      const data = await resp.json()
      if (data.ok) {
        setStatus('success')
        setTimeout(() => onClose(), 1500)
      }
    } catch { /* ignore */ }
  }

  const pullOllamaModel = async () => {
    if (!pullModel.trim()) return
    setPulling(true)
    try {
      await fetch('/api/ollama/pull', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: pullModel.trim() }),
      })
      const resp = await fetch('/api/ollama/status')
      const data = await resp.json()
      setOllamaModels(data.models || [])
      setPullModel('')
    } catch { /* ignore */ }
    setPulling(false)
  }

  const uploadModel = async (file) => {
    if (!file) return
    setUploading(true)
    setUploadProgress(`Uploading ${file.name} (${(file.size / 1024 / 1024 / 1024).toFixed(1)} GB)...`)
    try {
      const form = new FormData()
      form.append('model', file)
      const resp = await fetch('/api/models/upload', { method: 'POST', body: form })
      const data = await resp.json()
      if (data.ok) {
        setUploadProgress(`Imported as "${data.model}". Connecting...`)
        await connectOllama(data.model)
        const resp2 = await fetch('/api/ollama/status')
        const data2 = await resp2.json()
        setOllamaModels(data2.models || [])
        setUploadProgress('')
      } else {
        setUploadProgress(`Failed: ${data.error || 'unknown'}`)
      }
    } catch (e) {
      setUploadProgress(`Error: ${e.message}`)
    }
    setUploading(false)
  }

  const searchModels = async (query) => {
    if (!query || query.length < 2) return
    setHfSearching(true)
    setHfModels([])
    try {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), 15000)
      const resp = await fetch(`/api/models/search?q=${encodeURIComponent(query)}`, { signal: controller.signal })
      clearTimeout(timer)
      const data = await resp.json()
      setHfModels(data.models || [])
    } catch (err) {
      if (err.name === 'AbortError') {
        setHfModels([{ id: 'timeout', name: 'Search timed out — try a shorter name', source: 'error', compat: 'unknown' }])
      } else {
        setHfModels([])
      }
    }
    setHfSearching(false)
  }

  const downloadModel = async (model) => {
    setHfDownloading(model.id || model.name)
    setDownloadProgress('Starting download...')
    try {
      const resp = await fetch('/api/models/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: model.id || model.name, source: model.source }),
      })
      const data = await resp.json()
      if (data.ok) {
        setDownloadProgress('Downloaded! Connecting...')
        if (model.source === 'ollama') {
          await connectOllama(model.id || model.name)
        }
        const resp2 = await fetch('/api/ollama/status')
        const data2 = await resp2.json()
        setOllamaModels(data2.models || [])
        setHfDownloading('')
        setDownloadProgress('')
      } else {
        setDownloadProgress(`Failed: ${data.error || 'unknown error'}`)
        setTimeout(() => { setHfDownloading(''); setDownloadProgress('') }, 3000)
      }
    } catch (e) {
      setDownloadProgress(`Error: ${e.message}`)
      setTimeout(() => { setHfDownloading(''); setDownloadProgress('') }, 3000)
    }
  }

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-9999 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="bg-[#0a1628] border border-[rgba(0,229,255,0.3)] rounded-xl w-130 max-h-[80vh] overflow-y-auto shadow-[0_0_40px_rgba(0,229,255,0.15)]">
        {/* Header */}
        <div className="p-5 border-b border-[rgba(0,229,255,0.1)]">
          <h2 className="text-lg font-bold text-jarvis-bright font-['Share_Tech_Mono',monospace]">
            JARVIS Needs an AI Provider
          </h2>
          <p className="text-sm text-jarvis-bright/50 mt-1">
            Connect a cloud API or use a local model to get started.
          </p>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-[rgba(0,229,255,0.1)]">
          {[
            ['cloud', 'Cloud API'],
            ['local', 'Local Models'],
            ['download', 'Download Model'],
          ].map(([key, label]) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`flex-1 py-3 text-sm font-['Share_Tech_Mono',monospace] transition-all ${
                tab === key
                  ? 'text-jarvis-bright border-b-2 border-jarvis-bright bg-[rgba(0,229,255,0.05)]'
                  : 'text-jarvis-bright/40 hover:text-jarvis-bright/70'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Cloud Tab */}
        {tab === 'cloud' && (
          <div className="p-5 space-y-4">
            <p className="text-xs text-jarvis-bright/40">Select a provider and enter your API key:</p>

            <div className="grid grid-cols-2 gap-2">
              {PROVIDERS.map((p) => (
                <button
                  key={p.id}
                  onClick={() => { setSelected(p); setStatus(''); setErrorMsg('') }}
                  className={`p-3 rounded-lg border text-left transition-all ${
                    selected?.id === p.id
                      ? 'border-jarvis-bright bg-[rgba(0,229,255,0.1)]'
                      : 'border-[rgba(0,229,255,0.1)] hover:border-[rgba(0,229,255,0.3)] bg-transparent'
                  }`}
                >
                  <div className="text-sm font-bold text-jarvis-bright">{p.name}</div>
                  <div className="text-xs text-jarvis-bright/30 mt-1">{p.hint}</div>
                </button>
              ))}
            </div>

            {selected && (
              <div className="space-y-3">
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && testAndSave()}
                  placeholder={`${selected.name} API key...`}
                  className="w-full bg-[rgba(0,20,40,0.6)] border border-[rgba(0,229,255,0.2)] rounded-lg px-3 py-2 text-sm text-jarvis-bright font-['Share_Tech_Mono',monospace] outline-none focus:border-jarvis-bright placeholder:text-jarvis-bright/20"
                />
                <button
                  onClick={testAndSave}
                  disabled={!apiKey.trim() || status === 'testing'}
                  className="w-full py-2 rounded-lg font-['Share_Tech_Mono',monospace] text-sm transition-all bg-jarvis-border text-jarvis-bright border border-[rgba(0,229,255,0.3)] hover:bg-[rgba(0,229,255,0.25)] disabled:opacity-30"
                >
                  {status === 'testing' ? 'Testing connection...' : status === 'success' ? 'Connected!' : 'Connect'}
                </button>
                {status === 'error' && (
                  <p className="text-xs text-red-400">{errorMsg}</p>
                )}
                {status === 'success' && (
                  <p className="text-xs text-green-400">Provider added. JARVIS is online.</p>
                )}
              </div>
            )}
          </div>
        )}

        {/* Local Tab */}
        {tab === 'local' && (
          <div className="p-5 space-y-4">
            <div className="flex items-center gap-2">
              <div className={`w-2 h-2 rounded-full ${ollamaStatus === 'online' ? 'bg-green-400' : 'bg-red-400'}`} />
              <span className="text-sm text-jarvis-bright/60">
                Ollama: {ollamaStatus === 'checking' ? 'checking...' : ollamaStatus}
              </span>
            </div>

            {ollamaStatus === 'offline' && (
              <div className="text-xs text-jarvis-bright/40 space-y-2">
                <p>Ollama is not running. Install and start it:</p>
                <code className="block bg-[rgba(0,20,40,0.8)] p-2 rounded text-jarvis-bright/60">
                  curl -fsSL https://ollama.ai/install.sh | sh{'\n'}
                  ollama serve
                </code>
                <p className="mt-3">No internet? Copy GGUF model files manually:</p>
                <code className="block bg-[rgba(0,20,40,0.8)] p-2 rounded text-jarvis-bright/60">
                  # From USB or another machine:{'\n'}
                  ollama create mymodel -f /path/to/Modelfile{'\n'}
                  # Or import a GGUF directly:{'\n'}
                  ollama create mymodel -f - {'<'} model.gguf
                </code>
              </div>
            )}

            {ollamaStatus === 'online' && ollamaModels.length === 0 && (
              <div className="text-xs text-jarvis-bright/40 space-y-2">
                <p>Ollama is running but no models installed.</p>
                <p>Pull one from the internet:</p>
                <code className="block bg-[rgba(0,20,40,0.8)] p-2 rounded text-jarvis-bright/60">
                  ollama pull qwen2.5:7b
                </code>
                <p className="mt-2">Or import from a local GGUF file (no internet needed):</p>
                <code className="block bg-[rgba(0,20,40,0.8)] p-2 rounded text-jarvis-bright/60">
                  # Copy .gguf file from USB/drive, then:{'\n'}
                  echo {"'"}FROM /path/to/model.gguf{"'"} {'>'} /tmp/Modelfile{'\n'}
                  ollama create mymodel -f /tmp/Modelfile
                </code>
                <p className="mt-2">Model storage: <span className="text-jarvis-bright/60">/usr/share/ollama/.ollama/models/</span></p>
              </div>
            )}

            {ollamaStatus === 'online' && ollamaModels.length > 0 && (
              <div className="space-y-2">
                <p className="text-xs text-jarvis-bright/40">Available models — click to switch:</p>
                {ollamaModels.map((m) => {
                  const isActive = currentModel === m
                  const isSwitching = switching === m
                  // Estimate speed from model name
                  const isBig = /70b|72b|65b/i.test(m)
                  const isMedium = /13b|14b|16b|27b|32b|34b/i.test(m)
                  const speedTag = isBig ? 'slow' : isMedium ? 'medium' : 'fast'
                  return (
                    <button
                      key={m}
                      disabled={isSwitching}
                      onClick={async () => {
                        if (isActive) return
                        setSwitching(m)
                        try {
                          const resp = await fetch('/api/provider/add', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ name: 'ollama', type: 'openai', api_key: 'ollama', base_url: 'http://localhost:11434/v1', model: m, skip_test: true }),
                          })
                          const data = await resp.json()
                          if (data.ok) setCurrentModel(m)
                        } catch { /* ignore */ }
                        setSwitching('')
                      }}
                      className={`w-full text-left p-2 rounded-lg border transition-all flex justify-between items-center ${
                        isActive
                          ? 'border-green-500/50 bg-green-500/10'
                          : 'border-[rgba(0,229,255,0.1)] hover:border-jarvis-bright hover:bg-[rgba(0,229,255,0.05)]'
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <span className={`text-sm ${isActive ? 'text-green-400' : 'text-jarvis-bright'}`}>{m}</span>
                        <span className={`text-xs px-1 rounded ${
                          speedTag === 'fast' ? 'bg-green-500/20 text-green-400'
                          : speedTag === 'medium' ? 'bg-yellow-500/20 text-yellow-400'
                          : 'bg-red-500/20 text-red-400'
                        }`}>{speedTag}</span>
                      </div>
                      {isActive && <span className="text-xs text-green-400 font-bold">ACTIVE</span>}
                      {isSwitching && <span className="text-xs text-yellow-400">Switching...</span>}
                    </button>
                  )
                })}
              </div>
            )}

            {/* Upload local GGUF file */}
            {ollamaStatus === 'online' && (
              <div className="space-y-2">
                <p className="text-xs text-jarvis-bright/40">Upload a local model file (.gguf):</p>
                <label
                  className={`block border-2 border-dashed rounded-lg p-4 text-center cursor-pointer transition-all ${
                    uploading
                      ? 'border-[rgba(0,229,255,0.3)] bg-[rgba(0,229,255,0.05)]'
                      : 'border-jarvis-border hover:border-[rgba(0,229,255,0.4)] hover:bg-[rgba(0,229,255,0.03)]'
                  }`}
                  onDragOver={(e) => { e.preventDefault(); e.currentTarget.classList.add('border-jarvis-bright') }}
                  onDragLeave={(e) => { e.currentTarget.classList.remove('border-jarvis-bright') }}
                  onDrop={(e) => {
                    e.preventDefault()
                    e.currentTarget.classList.remove('border-jarvis-bright')
                    const file = e.dataTransfer.files[0]
                    if (file) uploadModel(file)
                  }}
                >
                  <input
                    type="file"
                    accept=".gguf,.bin,.safetensors,.onnx,.pt,.pth,.ggml"
                    className="hidden"
                    onChange={(e) => { if (e.target.files[0]) uploadModel(e.target.files[0]) }}
                    disabled={uploading}
                  />
                  <div className="text-sm text-jarvis-bright/50">
                    {uploading ? uploadProgress : 'Drop a model file here or click to browse'}
                  </div>
                  <div className="text-xs text-jarvis-bright/25 mt-1">
                    Supports: .gguf, .safetensors, .bin, .onnx, .pt
                  </div>
                </label>
              </div>
            )}

            {ollamaStatus === 'online' && (
              <div className="space-y-2">
                <p className="text-xs text-jarvis-bright/40">Pull a new model:</p>
                <div className="flex gap-2">
                  <input
                    value={pullModel}
                    onChange={(e) => setPullModel(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && pullOllamaModel()}
                    placeholder="e.g. llama3.3, qwen2.5:7b"
                    className="flex-1 bg-[rgba(0,20,40,0.6)] border border-[rgba(0,229,255,0.2)] rounded-lg px-3 py-2 text-sm text-jarvis-bright font-['Share_Tech_Mono',monospace] outline-none focus:border-jarvis-bright placeholder:text-jarvis-bright/20"
                  />
                  <button
                    onClick={pullOllamaModel}
                    disabled={pulling}
                    className="px-4 py-2 rounded-lg text-sm bg-jarvis-border text-jarvis-bright border border-[rgba(0,229,255,0.3)] hover:bg-[rgba(0,229,255,0.25)] disabled:opacity-30"
                  >
                    {pulling ? 'Pulling...' : 'Pull'}
                  </button>
                </div>
              </div>
            )}

            {status === 'success' && (
              <p className="text-xs text-green-400">Local model connected. JARVIS is online.</p>
            )}
          </div>
        )}

        {/* Download Tab */}
        {tab === 'download' && (
          <div className="p-5 space-y-4">
            <p className="text-xs text-jarvis-bright/40">
              Search for a model by name — JARVIS will find and download it for you.
            </p>

            <div className="flex gap-2">
              <input
                value={hfSearch}
                onChange={(e) => setHfSearch(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && searchModels(hfSearch)}
                placeholder="e.g. llama3, qwen, mistral, phi, gemma..."
                className="flex-1 bg-[rgba(0,20,40,0.6)] border border-[rgba(0,229,255,0.2)] rounded-lg px-3 py-2 text-sm text-jarvis-bright font-['Share_Tech_Mono',monospace] outline-none focus:border-jarvis-bright placeholder:text-jarvis-bright/20"
              />
              <button
                onClick={() => searchModels(hfSearch)}
                disabled={hfSearching}
                className="px-4 py-2 rounded-lg text-sm bg-jarvis-border text-jarvis-bright border border-[rgba(0,229,255,0.3)] hover:bg-[rgba(0,229,255,0.25)] disabled:opacity-30"
              >
                {hfSearching ? 'Searching...' : 'Search'}
              </button>
            </div>

            {hfModels.length > 0 && (
              <div className="space-y-2 max-h-75 overflow-y-auto">
                {hfModels.map((m) => (
                  <div
                    key={m.id}
                    className="flex items-center justify-between p-3 rounded-lg border border-[rgba(0,229,255,0.1)] hover:border-[rgba(0,229,255,0.3)] transition-all"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="text-sm text-jarvis-bright truncate">{m.id || m.name}</div>
                      <div className="text-xs text-jarvis-bright/30 flex gap-3">
                        {m.size && <span>{m.size}</span>}
                        {m.pipeline && <span>{m.pipeline}</span>}
                        {m.downloads > 0 && <span>{m.downloads.toLocaleString()} downloads</span>}
                        <span className={
                          m.source === 'ollama' ? 'text-green-400/60'
                          : m.source === 'huggingface-gguf' ? 'text-cyan-400/60'
                          : 'text-purple-400/60'
                        }>
                          {m.source === 'huggingface-gguf' ? 'HF (GGUF)' : m.source === 'huggingface' ? 'HuggingFace' : m.source}
                        </span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 ml-3">
                      {m.compat && (
                        <span className={`text-xs px-1.5 py-0.5 rounded ${
                          m.compat === 'perfect' ? 'bg-green-500/20 text-green-400' :
                          m.compat === 'good' ? 'bg-cyan-500/20 text-cyan-400' :
                          m.compat === 'tight' ? 'bg-yellow-500/20 text-yellow-400' :
                          m.compat === 'too_large' ? 'bg-red-500/20 text-red-400' :
                          'bg-gray-500/20 text-gray-400'
                        }`} title={m.compat_reason}>
                          {m.compat === 'perfect' ? 'GPU' :
                           m.compat === 'good' ? 'OK' :
                           m.compat === 'tight' ? 'Tight' :
                           m.compat === 'too_large' ? 'Too big' : '?'}
                        </span>
                      )}
                      <button
                        onClick={() => downloadModel(m)}
                        disabled={hfDownloading === (m.id || m.name) || m.compat === 'too_large'}
                        className="px-3 py-1 rounded text-xs bg-jarvis-border text-jarvis-bright border border-[rgba(0,229,255,0.2)] hover:bg-[rgba(0,229,255,0.25)] disabled:opacity-30"
                      >
                        {hfDownloading === (m.id || m.name) ? 'Downloading...' : 'Download'}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {downloadProgress && (
              <p className="text-xs text-jarvis-bright/60">{downloadProgress}</p>
            )}

            {hfModels.length === 0 && !hfSearching && hfSearch && (
              <p className="text-xs text-jarvis-bright/30">No models found. Try a different name.</p>
            )}
          </div>
        )}

        {/* Footer */}
        <div className="p-4 border-t border-[rgba(0,229,255,0.1)] flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-1.5 text-sm text-jarvis-bright/40 hover:text-jarvis-bright transition-all"
          >
            Skip
          </button>
        </div>
      </div>
    </div>
  )
}
