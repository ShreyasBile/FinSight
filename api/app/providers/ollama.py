import logging

from ..utils import parse_json_blob
from .base import OCRProvider

log = logging.getLogger("finsight.ocr")


class OllamaProvider(OCRProvider):
    """Local vision-language model via Ollama (e.g. qwen2.5vl:7b).

    Image-only API, so the gateway rasterizes PDFs to PNG pages first.
    `format=json` nudges the model to emit parseable JSON. Model, host and
    num_ctx come from the Settings page (agent 'extraction')."""

    name = "ollama"

    async def extract(self, file_bytes, mime, doc_type, prompt):
        from ..agents.llm.gateway import achat
        log.info("ollama request: doc_type=%s bytes_kb=%d", doc_type, len(file_bytes) // 1024)
        res = await achat("extraction", user=prompt, documents=[(file_bytes, mime)],
                          timeout=120, mock_hint=doc_type)
        return res.text, parse_json_blob(res.text)
