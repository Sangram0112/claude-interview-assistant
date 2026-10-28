# Audio permissions and transcription setup

## How meeting audio is captured

The app does not connect to Zoom, Teams or Google Meet. It captures:

* **System audio** – a WASAPI *loopback* of a Windows output device, i.e. exactly what you hear
  in your speakers/headphones. This is the interviewer's voice in any meeting app or browser.
* **Microphone** – your own voice (optional; used for context and practice answers).

Capture starts only after **Start session** + the consent dialog, stops on **Pause**/**Stop**
(devices are closed, not muted), and is shown by a pulsing **● LISTENING** indicator and in the
window title. Raw audio is never written to disk.

## Windows permissions

* **Microphone:** *Settings > Privacy & security > Microphone* → enable *Microphone access* and
  *Let desktop apps access your microphone*. If blocked, starting a session shows an error that
  points here.
* **System audio (loopback):** no permission prompt exists on Windows; any enabled output device
  can be captured. If the system-audio meter never moves, see Troubleshooting.

## Per-platform setup

| Platform | What to do |
|---|---|
| Zoom desktop | Zoom > Settings > Audio > *Speaker*: note the device. In this app choose that device's loopback (or *Default output device* if it is the Windows default). |
| Teams desktop | Teams > Settings > Devices > *Speaker*: same as above. |
| Google Meet (browser) | Meet plays through the browser's output device (normally the Windows default). No extension or tab capture is used, so browser restrictions don't apply. |
| Bluetooth headsets | When a headset switches to "Hands-Free" mode during calls it becomes a different output device; pick the *Hands-Free* loopback in Settings > Audio. |

Use the **Platform setup** tab inside the app for the same tips plus detection of whether the
meeting app is running.

## Transcription providers

| Provider | Audio leaves PC? | Setup | Latency |
|---|---|---|---|
| **Local Whisper** (default) | No | None. First start downloads the model to `%LOCALAPPDATA%\ClaudeInterviewAssistant\models` | ~1–2 s after a sentence ends on a modern CPU with `base.en` |
| **Deepgram** (optional) | Yes, streamed while listening | Create a key at console.deepgram.com, paste in Settings > Transcription | ~0.3–0.8 s |

Whisper model choice (Settings > Transcription): `tiny.en` (fastest, lowest accuracy),
`base.en` (default), `small.en` (better accuracy, needs a fast CPU), `medium.en`/`large-v3`
(GPU recommended; select *NVIDIA GPU (CUDA)* only if CUDA 12 + cuDNN are installed).

## Tuning

* **End-of-sentence silence** (700 ms): lower = faster question detection, but long pauses
  may split a question; higher = fewer splits.
* **Speech threshold**: raise it if background noise produces junk transcripts. The
  segmenter also learns the steady noise floor automatically.
* **Detect questions from**: by default only system audio (the interviewer) is scanned, so
  your own spoken questions don't trigger suggestions.

## Authorised meeting-platform integrations (not implemented)

| Platform | Official route | Why not used |
|---|---|---|
| Zoom | Realtime Media Streams (RTMS) / Meeting SDK | Requires a Zoom developer app, account-admin approval and a server component. Claude's Zoom connector does not provide live audio. |
| Teams | Graph transcripts (post-meeting), real-time media bots | Needs Azure app registration and tenant admin consent; transcripts are post-meeting only. |
| Google Meet | Meet REST API (post-meeting transcripts), Meet Media API (developer preview) | Needs Workspace + Google Cloud project; real-time access is preview-only. |

The `TranscriptionProvider` and `AudioSource` interfaces are the extension points for adding
one of these later.
