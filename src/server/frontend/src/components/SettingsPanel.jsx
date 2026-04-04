import { useState, useCallback } from 'react'

const PROVIDER_OPTIONS = [
  { value: '', label: 'Select provider...' },
  { value: 'claude', label: 'Claude (Anthropic)' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'groq', label: 'Groq' },
  { value: 'together', label: 'Together AI' },
  { value: 'openrouter', label: 'OpenRouter' },
  { value: 'ollama', label: 'Ollama (Local)' },
  { value: 'custom', label: 'Custom API...' },
]

export default function SettingsPanel({ isOpen, onClose }) {
  const [providers, setProviders] = useState([])
  const [selectedProvider, setSelectedProvider] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [customUrl, setCustomUrl] = useState('')
  const [customModel, setCustomModel] = useState('')

  const isCustom = selectedProvider === 'custom'

  const addProvider = useCallback(async () => {
    if (!selectedProvider || (!apiKey && selectedProvider !== 'ollama')) return

    const newProvider = {
      name: selectedProvider,
      label: PROVIDER_OPTIONS.find((p) => p.value === selectedProvider)?.label || selectedProvider,
      key: apiKey,
      url: customUrl || undefined,
      model: customModel || undefined,
    }

    // Attempt to save to backend
    try {
      await fetch('/api/providers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: selectedProvider,
          api_key: apiKey,
          base_url: customUrl || undefined,
          model: customModel || undefined,
        }),
      })
    } catch {
      // Continue adding locally even if backend is unavailable
    }

    setProviders((prev) => [...prev, newProvider])
    setSelectedProvider('')
    setApiKey('')
    setCustomUrl('')
    setCustomModel('')
  }, [selectedProvider, apiKey, customUrl, customModel])

  const removeProvider = useCallback((index) => {
    setProviders((prev) => prev.filter((_, i) => i !== index))
  }, [])

  if (!isOpen) return null

  return (
    <div className="fixed top-15 right-4 w-72 max-h-[60vh] bg-[rgba(2,8,16,0.95)] border border-jarvis-border rounded z-200 overflow-y-auto backdrop-blur-lg">
      {/* Header */}
      <div className="flex justify-between items-center px-3.5 py-2.5 border-b border-jarvis-border">
        <span className="font-['Orbitron'] text-[0.7rem] tracking-[2px] text-jarvis-cyan">AI PROVIDERS</span>
        <span
          className="cursor-pointer text-xl text-[rgba(0,184,212,0.35)] transition-colors hover:text-jarvis-red leading-none"
          onClick={onClose}
        >
          &times;
        </span>
      </div>

      {/* Provider list */}
      <div className="px-3.5 py-2">
        {providers.length === 0 ? (
          <div className="text-[rgba(0,184,212,0.35)] text-[0.7rem] py-2 text-center">
            No providers configured
          </div>
        ) : (
          providers.map((p, i) => (
            <div
              key={i}
              className="flex justify-between items-center py-1.5 border-b border-[rgba(0,184,212,0.06)] text-xs"
            >
              <div className="flex flex-col gap-0.5">
                <span className="text-jarvis-bright font-medium">{p.label}</span>
                {p.model && (
                  <span className="text-[rgba(0,184,212,0.35)] text-[0.65rem]">{p.model}</span>
                )}
              </div>
              <span
                className="text-[rgba(0,184,212,0.35)] cursor-pointer text-sm transition-colors hover:text-jarvis-red"
                onClick={() => removeProvider(i)}
              >
                &times;
              </span>
            </div>
          ))
        )}
      </div>

      {/* Add form */}
      <div className="px-3.5 py-2.5 pt-2 border-t border-jarvis-border">
        <select
          value={selectedProvider}
          onChange={(e) => setSelectedProvider(e.target.value)}
          className="w-full mb-2 bg-[rgba(0,20,40,0.8)] border border-jarvis-border text-jarvis-text px-2.5 py-1.5 font-['Share_Tech_Mono',monospace] text-[0.72rem] outline-none focus:border-jarvis-cyan"
        >
          {PROVIDER_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value} className="bg-[#0a1520] text-jarvis-text">
              {opt.label}
            </option>
          ))}
        </select>

        <textarea
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder="Paste your API key or token here..."
          rows={3}
          className="w-full mb-2 bg-[rgba(0,20,40,0.8)] border border-jarvis-border text-jarvis-text px-2.5 py-1.5 font-['Share_Tech_Mono',monospace] text-[0.72rem] outline-none resize-y focus:border-jarvis-cyan"
        />

        {isCustom && (
          <>
            <input
              type="text"
              value={customUrl}
              onChange={(e) => setCustomUrl(e.target.value)}
              placeholder="Base URL (auto-detected)"
              className="w-full mb-2 bg-[rgba(0,20,40,0.8)] border border-jarvis-border text-jarvis-text px-2.5 py-1.5 font-['Share_Tech_Mono',monospace] text-[0.72rem] outline-none focus:border-jarvis-cyan"
            />
            <input
              type="text"
              value={customModel}
              onChange={(e) => setCustomModel(e.target.value)}
              placeholder="Model (auto-detected)"
              className="w-full mb-2 bg-[rgba(0,20,40,0.8)] border border-jarvis-border text-jarvis-text px-2.5 py-1.5 font-['Share_Tech_Mono',monospace] text-[0.72rem] outline-none focus:border-jarvis-cyan"
            />
          </>
        )}

        <button
          onClick={addProvider}
          className="w-full py-2 bg-jarvis-cyan/10 border border-jarvis-cyan text-jarvis-cyan font-['Orbitron'] text-[0.65rem] tracking-[2px] cursor-pointer transition-all hover:bg-jarvis-cyan/25 hover:shadow-[0_0_12px_rgba(0,184,212,0.3)]"
        >
          ADD PROVIDER
        </button>
      </div>
    </div>
  )
}
