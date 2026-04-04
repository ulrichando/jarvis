// WebRTC Voice Activity Detection
class WebRTCVAD {
  constructor(options = {}) {
    this.threshold = options.threshold || 0.5
    this.onSpeech = options.onSpeech || (() => {})
    this.onSilence = options.onSilence || (() => {})
    this.audioContext = null
    this.analyser = null
    this.processor = null
  }

  async start(stream) {
    this.audioContext = new AudioContext()
    const source = this.audioContext.createMediaStreamSource(stream)
    
    this.analyser = this.audioContext.createAnalyser()
    this.analyser.fftSize = 2048
    this.analyser.smoothingTimeConstant = 0.8
    
    // Create script processor for VAD
    this.processor = this.audioContext.createScriptProcessor(2048, 1, 1)
    
    source.connect(this.analyser)
    this.analyser.connect(this.processor)
    this.processor.connect(this.audioContext.destination)
    
    let speaking = false
    let silenceStart = Date.now()
    
    this.processor.onaudioprocess = () => {
      const buffer = new Float32Array(this.analyser.fftSize)
      this.analyser.getFloatTimeDomainData(buffer)
      
      // Calculate RMS (Root Mean Square) for more accurate energy detection
      let sum = 0
      for (let i = 0; i < buffer.length; i++) {
        sum += buffer[i] * buffer[i]
      }
      const rms = Math.sqrt(sum / buffer.length)
      
      // Also check zero-crossing rate for voice vs noise distinction
      let zeroCrossings = 0
      for (let i = 1; i < buffer.length; i++) {
        if ((buffer[i] >= 0) !== (buffer[i - 1] >= 0)) {
          zeroCrossings++
        }
      }
      const zcr = zeroCrossings / buffer.length
      
      // Voice typically has ZCR between 0.02 and 0.5
      const isVoiceLike = zcr > 0.02 && zcr < 0.5
      
      if (rms > this.threshold && isVoiceLike) {
        if (!speaking) {
          speaking = true
          this.onSpeech()
        }
        silenceStart = Date.now()
      } else if (speaking && Date.now() - silenceStart > 300) {
        speaking = false
        this.onSilence()
      }
    }
  }

  stop() {
    if (this.processor) {
      this.processor.disconnect()
      this.processor = null
    }
    if (this.analyser) {
      this.analyser.disconnect()
      this.analyser = null
    }
    if (this.audioContext) {
      this.audioContext.close()
      this.audioContext = null
    }
  }

  setThreshold(threshold) {
    this.threshold = threshold
  }
}

export default WebRTCVAD