// клиентская часть демо - websocket, захват микрофона, проигрывание tts
// стиль push-to-talk: держим кнопку - пишем, отпустили - реплика ушла

const statusEl = document.getElementById('status')
const chatEl = document.getElementById('chat')
const recBtn = document.getElementById('recBtn')
const textForm = document.getElementById('textForm')
const textInput = document.getElementById('textInput')
const hintEl = document.getElementById('hint')

let ws = null
let wsReady = false

// захват микрофона
let micCtx = null
let micNode = null
let micReady = false
let recording = false

// проигрывание ответа
let playCtx = null
let ttsRate = 22050
let playing = false
// сервер закончил слать аудио, но очередь еще может дозвучивать
let ttsDone = true
let playCursor = 0
let activeSources = []

let assistantBubble = null

function setStatus (text) {
  statusEl.textContent = text
}

function addBubble (role, text) {
  const div = document.createElement('div')
  div.className = 'bubble ' + role
  div.textContent = text
  chatEl.appendChild(div)
  chatEl.scrollTop = chatEl.scrollHeight
  return div
}

function updateRecButton () {
  recBtn.disabled = !(wsReady && micReady)
}

// --- websocket ---

function connect () {
  const proto = location.protocol === 'https:' ? 'wss://' : 'ws://'
  ws = new WebSocket(proto + location.host + '/ws')
  ws.binaryType = 'arraybuffer'
  ws.onopen = () => setStatus('соединение установлено')
  ws.onclose = () => {
    wsReady = false
    updateRecButton()
    setStatus('соединение потеряно, обновите страницу')
  }
  ws.onmessage = (event) => {
    if (typeof event.data === 'string') {
      handleEvent(JSON.parse(event.data))
    } else {
      // бинарные кадры вниз - только аудио tts
      playChunk(event.data)
    }
  }
}

function sendJson (payload) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(payload))
  }
}

function handleEvent (msg) {
  switch (msg.type) {
    case 'ready':
      wsReady = true
      updateRecButton()
      setStatus('готов, сессия ' + msg.session_id.slice(0, 8))
      break
    case 'final_transcript':
      addBubble('user', msg.text)
      assistantBubble = null
      setStatus('ассистент думает...')
      break
    case 'llm_token':
      if (!assistantBubble) {
        assistantBubble = addBubble('assistant', '')
      }
      assistantBubble.textContent += msg.text
      chatEl.scrollTop = chatEl.scrollHeight
      break
    case 'tts_start':
      ttsRate = msg.sample_rate
      startPlayback()
      break
    case 'tts_end':
      // не сбрасываем playing сразу - хвост очереди еще дозвучивает,
      // флаг снимет onended последнего источника
      ttsDone = true
      if (activeSources.length === 0) playing = false
      break
    case 'turn_done':
      assistantBubble = null
      setStatus('готов')
      break
    case 'error':
      setStatus('ошибка: ' + msg.message)
      break
  }
}

// --- проигрывание tts ---

function startPlayback () {
  if (!playCtx) {
    playCtx = new AudioContext()
  }
  if (playCtx.state === 'suspended') {
    playCtx.resume()
  }
  playing = true
  ttsDone = false
  playCursor = Math.max(playCursor, playCtx.currentTime + 0.05)
}

function playChunk (arrayBuffer) {
  if (!playCtx) return
  const int16 = new Int16Array(arrayBuffer)
  if (int16.length === 0) return
  const float32 = new Float32Array(int16.length)
  for (let i = 0; i < int16.length; i++) {
    float32[i] = int16[i] / 32768
  }
  const buffer = playCtx.createBuffer(1, float32.length, ttsRate)
  buffer.copyToChannel(float32, 0)
  const source = playCtx.createBufferSource()
  source.buffer = buffer
  source.connect(playCtx.destination)
  // куски встают в очередь друг за другом без пауз
  playCursor = Math.max(playCursor, playCtx.currentTime + 0.05)
  source.start(playCursor)
  playCursor += buffer.duration
  playing = true
  activeSources.push(source)
  source.onended = () => {
    activeSources = activeSources.filter((s) => s !== source)
    if (ttsDone && activeSources.length === 0) playing = false
  }
}

function stopPlayback () {
  for (const source of activeSources) {
    try {
      source.stop()
    } catch (e) {
      // источник мог уже дозвучать, это не страшно
    }
  }
  activeSources = []
  playCursor = 0
  playing = false
  ttsDone = true
}

// --- захват микрофона ---

async function initMic () {
  let stream = null
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true
      }
    })
  } catch (e) {
    hintEl.textContent =
      'микрофон недоступен, работает только текстовый ввод'
    return
  }
  // просим контекст сразу на 16 kHz, браузер сам ресемплирует
  micCtx = new AudioContext({ sampleRate: 16000 })
  await micCtx.audioWorklet.addModule('pcm-worklet.js')
  const source = micCtx.createMediaStreamSource(stream)
  micNode = new AudioWorkletNode(micCtx, 'pcm-capture')
  micNode.port.onmessage = (event) => {
    if (recording && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(event.data)
    }
  }
  // worklet должен быть подключен к выходу, иначе граф его не считает,
  // поэтому вешаем его через нулевой gain чтобы себя не слышать
  const mute = micCtx.createGain()
  mute.gain.value = 0
  source.connect(micNode)
  micNode.connect(mute)
  mute.connect(micCtx.destination)
  micReady = true
  updateRecButton()
}

function startRecording () {
  if (recording || !wsReady || !micReady) return
  if (playing) {
    // нажатие кнопки во время ответа - это перебивание
    sendJson({ type: 'barge_in' })
    stopPlayback()
  }
  if (micCtx.state === 'suspended') {
    micCtx.resume()
  }
  recording = true
  recBtn.classList.add('active')
  recBtn.textContent = 'Говорите...'
  setStatus('запись...')
  sendJson({ type: 'start' })
}

function stopRecording () {
  if (!recording) return
  recording = false
  recBtn.classList.remove('active')
  recBtn.textContent = 'Удерживайте для записи'
  setStatus('распознавание...')
  sendJson({ type: 'stop' })
}

recBtn.addEventListener('pointerdown', (event) => {
  event.preventDefault()
  startRecording()
})
recBtn.addEventListener('pointerup', stopRecording)
recBtn.addEventListener('pointerleave', stopRecording)
// на мобильных жест может оборваться системой (скролл, звонок),
// иначе запись залипнет в активном состоянии
recBtn.addEventListener('pointercancel', stopRecording)

// --- текстовый fallback ---

textForm.addEventListener('submit', (event) => {
  event.preventDefault()
  const text = textInput.value.trim()
  if (!text || !wsReady) return
  if (playing) {
    sendJson({ type: 'barge_in' })
    stopPlayback()
  }
  sendJson({ type: 'text_input', text: text })
  textInput.value = ''
})

connect()
initMic()
