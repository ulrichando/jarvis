// Enhanced echo cancellation for better TTS interrupt detection
export class EnhancedEchoCancellation {
  constructor() {
    this.ttsAudioContext = null
    this.ttsAnalyser = null
    this.ttsGainNode = null
  }

  // Capture TTS audio for echo reference
  async captureTTSAudio(audioElement) {
    if (!this.ttsAudioContext) {
      this.ttsAudioContext = new AudioContext()
    }
    
    // Create nodes for TTS analysis
    const source = this.ttsAudioContext.createMediaElementSource(audioElement)
    this.ttsAnalyser = this.ttsAudioContext.createAnalyser()
    this.ttsGainNode = this.ttsAudioContext.createGain()
    
    source.connect(this.ttsAnalyser)
    this.ttsAnalyser.connect(this.ttsGainNode)
    this.ttsGainNode.connect(this.ttsAudioContext.destination)
    
    return this.ttsAnalyser
  }

  // Get TTS reference signal for echo subtraction
  getTTSReference() {
    if (!this.ttsAnalyser) return null
    
    const bufferLength = this.ttsAnalyser.frequencyBinCount
    const dataArray = new Uint8Array(bufferLength)
    this.ttsAnalyser.getByteFrequencyData(dataArray)
    
    return dataArray
  }

  // Simple spectral subtraction for echo reduction
  suppressEcho(micData, ttsReference) {
    if (!ttsReference) return micData
    
    const result = new Uint8Array(micData.length)
    for (let i = 0; i < micData.length; i++) {
      // Subtract TTS reference from mic input
      // Scale factor prevents over-subtraction
      const suppressed = Math.max(0, micData[i] - ttsReference[i] * 0.7)
      result[i] = suppressed
    }
    
    return result
  }

  cleanup() {
    if (this.ttsGainNode) {
      this.ttsGainNode.disconnect()
      this.ttsGainNode = null
    }
    if (this.ttsAnalyser) {
      this.ttsAnalyser.disconnect()
      this.ttsAnalyser = null
    }
    if (this.ttsAudioContext && this.ttsAudioContext.state !== 'closed') {
      this.ttsAudioContext.close()
      this.ttsAudioContext = null
    }
  }
}