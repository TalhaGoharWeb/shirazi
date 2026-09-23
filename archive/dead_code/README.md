# archive/dead_code

Quarantined on 2026-09-23 (Phase 2: stabilize baseline) — NOT deleted.
Git retains full history.

| File | Why quarantined |
|---|---|
| `stt.py` | Whisper/Vosk offline STT classes; MARK XL leftover. Zero importers repo-wide. |
| `tts.py` | EdgeTTS/Kokoro/ElevenLabs engines + TTSPlayer; MARK XL leftover. Zero importers repo-wide. |
| `installer.py` | MARK XL auto-installer (`_CORE`/`_WINDOWS`/`_STT`/`_TTS` package lists). `setup.py` is the real installer. Zero importers repo-wide. |

Note: these three were the ONLY modules importing `miniaudio`, `torch`,
`edge_tts`, `kokoro`, `faster_whisper`, `vosk` — which is why those are not in
requirements.txt. If this code is ever reactivated, those deps must be added.
