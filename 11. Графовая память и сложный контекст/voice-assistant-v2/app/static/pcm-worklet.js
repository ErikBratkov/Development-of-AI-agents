// worklet копит сэмплы с микрофона и отдает их кусками примерно по 100 мс
// вход float32 [-1, 1], выход int16 - его и шлем в websocket как есть

const CHUNK_SAMPLES = 1600

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor () {
    super()
    this._chunk = new Int16Array(CHUNK_SAMPLES)
    this._filled = 0
  }

  process (inputs) {
    const channel = inputs[0] && inputs[0][0]
    if (!channel) return true
    for (let i = 0; i < channel.length; i++) {
      let sample = channel[i]
      if (sample > 1) sample = 1
      if (sample < -1) sample = -1
      this._chunk[this._filled] = sample < 0
        ? sample * 32768
        : sample * 32767
      this._filled++
      if (this._filled === CHUNK_SAMPLES) {
        // копия нужна, потому что рабочий буфер используем дальше
        const out = this._chunk.slice()
        this.port.postMessage(out.buffer, [out.buffer])
        this._filled = 0
      }
    }
    return true
  }
}

registerProcessor('pcm-capture', PcmCaptureProcessor)
