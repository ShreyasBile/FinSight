from ..utils import parse_json_blob
from .base import OCRProvider


class AnthropicProvider(OCRProvider):
    """Server-side Claude vision through the official Anthropic SDK (via the
    agent gateway). The API key stays on the server; PDFs are sent as native
    document blocks. Only reachable after a Head Manager has acknowledged in
    Settings that documents will leave the machine."""

    name = "anthropic"

    async def extract(self, file_bytes, mime, doc_type, prompt):
        from ..agents.llm.gateway import achat
        res = await achat("extraction", user=prompt, documents=[(file_bytes, mime)],
                          timeout=120, mock_hint=doc_type)
        return res.text, parse_json_blob(res.text)
