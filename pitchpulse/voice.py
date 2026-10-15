"""Spoken commentary with Azure AI Speech (neural text-to-speech on the Foundry resource).

Only lines PitchPulse generated itself are spoken: each comes with an HMAC `voice_token`, so the
public endpoint cannot be used as a free text-to-speech service.
"""
import hashlib
import hmac
import os
from urllib.parse import urlparse
from xml.sax.saxutils import escape

import httpx

# shortcut: per-process secret, so tokens only verify on the replica that issued them; share it via
# an app secret if the app ever runs more than one replica.
SECRET = os.urandom(32)
VOICES = {  # neural voices; British English for that Premier League broadcast sound
    "English": ("en-GB", "en-GB-RyanNeural"), "Albanian": ("sq-AL", "sq-AL-IlirNeural"),
    "Spanish": ("es-ES", "es-ES-AlvaroNeural"), "French": ("fr-FR", "fr-FR-HenriNeural"),
    "German": ("de-DE", "de-DE-ConradNeural"), "Portuguese": ("pt-PT", "pt-PT-DuarteNeural"),
    "Arabic": ("ar-SA", "ar-SA-HamedNeural"),
}
MAX_CHARS = 800


def token(text, language):
    return hmac.new(SECRET, f"{language}\n{text}".encode(), hashlib.sha256).hexdigest()


def verify(text, language, given):
    return hmac.compare_digest(token(text, language), given)


def _endpoint():
    # The Foundry resource's custom domain serves Speech too and accepts Entra ID tokens.
    host = urlparse(os.getenv("AZURE_OPENAI_ENDPOINT", "")).hostname or ""
    resource = host.split(".")[0]
    return f"https://{resource}.cognitiveservices.azure.com/tts/cognitiveservices/v1" if resource else None


async def _auth_header():
    key = os.getenv("AZURE_OPENAI_API_KEY")
    if key:
        return {"Ocp-Apim-Subscription-Key": key}
    from azure.identity.aio import DefaultAzureCredential

    async with DefaultAzureCredential() as credential:  # managed identity in Azure
        access = await credential.get_token("https://cognitiveservices.azure.com/.default")
    return {"Authorization": f"Bearer {access.token}"}


async def speak(text, language, rate=0):
    """MP3 audio of `text` in the viewer's language; `rate` speeds the voice up by that many percent."""
    url = _endpoint()
    if not url:
        raise RuntimeError("Azure AI Speech is not configured")
    lang, voice = VOICES.get(language, VOICES["English"])
    ssml = (f"<speak version='1.0' xml:lang='{lang}'><voice name='{voice}'><prosody rate='+{int(rate)}%'>"
            f"{escape(text[:MAX_CHARS])}</prosody></voice></speak>")
    headers = {**await _auth_header(), "Content-Type": "application/ssml+xml", "User-Agent": "pitchpulse",
               "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, content=ssml.encode("utf-8"), headers=headers)
    r.raise_for_status()
    return r.content
