# prism-ai
AI layer for producing localized, audience-aware content variants with explicit provenance through Prism contracts.

## Speech synthesis

Prism owns the model catalog, voice selection, TTS service composition and product limits. The [speech runtime](services/tts/README.md) provides native Ogg/Opus output for authorized clients such as Whisper. The Telegram workflow and its privacy/consent policy remain in prism-hubot; Telegram transport remains in prism-bot.

The [artificial-intelligence foundation](https://github.com/aiaiaiai-org/artificial-intelligence) owns reusable inference primitives and provider adapters, not Prism's service or its voice catalog. A concrete shared speech seam is not published yet; the local engine is injected and can be replaced without changing the service's policy or transport. No deployment is implied by this change.
