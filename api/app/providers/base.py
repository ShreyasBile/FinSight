from abc import ABC, abstractmethod


class OCRProvider(ABC):
    """A document-extraction backend.

    Given the raw file bytes + the doc-type prompt, return (raw_text, parsed_json).
    `parsed` may be None if the model produced no valid JSON — guardrails handle that.
    """

    name: str = "base"

    @abstractmethod
    async def extract(self, file_bytes: bytes, mime: str, doc_type: str, prompt: str) -> tuple[str, dict | None]:
        ...
