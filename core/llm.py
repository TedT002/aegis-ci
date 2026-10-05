"""Sağlayıcıdan bağımsız, bağımlılıksız (stdlib urllib) LLM istemcisi.

LLM çıktısı ASLA doğrudan güvenilir kabul edilmez: her yanıt
  1) JSON olarak ayrıştırılır,
  2) Pydantic şemasıyla doğrulanır,
  3) sandbox'ta deterministik olarak test edilir.
Herhangi bir adım başarısız olursa ajan deterministik moda geri düşer.

Ortam değişkenleri:
  GEMINI_API_KEY / GOOGLE_API_KEY   → Gemini (AEGIS_GEMINI_MODEL, varsayılan gemini-2.5-flash)
  OPENAI_API_KEY                    → OpenAI uyumlu (AEGIS_OPENAI_MODEL, OPENAI_BASE_URL)
"""
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


class LLMError(RuntimeError):
    pass


class LLMClient:
    provider = "base"
    model = ""

    def complete(self, system: str, prompt: str) -> str:  # pragma: no cover - arayüz
        raise NotImplementedError

    def complete_json(self, system: str, prompt: str) -> Dict[str, Any]:
        text = self.complete(system + "\nRespond with a single JSON object only. No prose.", prompt)
        return extract_json(text)

    @staticmethod
    def _post(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: int = 60) -> Dict[str, Any]:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **headers}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise LLMError(f"{type(e).__name__}: {e}") from e


class GeminiClient(LLMClient):
    provider = "gemini"

    def __init__(self, api_key: str, model: Optional[str] = None):
        self.api_key = api_key
        self.model = model or os.getenv("AEGIS_GEMINI_MODEL", "gemini-2.5-flash")

    def complete(self, system: str, prompt: str) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        data = self._post(url, {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        }, {"x-goog-api-key": self.api_key})
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"Unexpected Gemini response: {str(data)[:300]}") from e


class OpenAIClient(LLMClient):
    provider = "openai"

    def __init__(self, api_key: str, model: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key
        self.model = model or os.getenv("AEGIS_OPENAI_MODEL", "gpt-4o-mini")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")

    def complete(self, system: str, prompt: str) -> str:
        data = self._post(f"{self.base_url}/chat/completions", {
            "model": self.model, "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        }, {"Authorization": f"Bearer {self.api_key}"})
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"Unexpected OpenAI response: {str(data)[:300]}") from e


def extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise LLMError("No JSON object in LLM response.")
    try:
        return json.loads(text[start:end + 1])
    except ValueError as e:
        raise LLMError(f"Invalid JSON from LLM: {e}") from e


def build_llm(mode: str = "auto") -> Optional[LLMClient]:
    """mode: auto | none | gemini | openai"""
    mode = (mode or "auto").lower()
    if mode == "none":
        return None
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    if mode in ("auto", "gemini") and gemini_key:
        return GeminiClient(gemini_key)
    if mode in ("auto", "openai") and openai_key:
        return OpenAIClient(openai_key)
    if mode != "auto":
        raise LLMError(f"LLM provider '{mode}' requested but no API key found in environment.")
    return None
