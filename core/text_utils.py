import re
import unicodedata


def normalize_text(value: str | None, *, collapse_whitespace: bool = False) -> str:
    text = (value or "").strip()
    if not text:
        return ""

    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold().strip()
    if collapse_whitespace:
        text = re.sub(r"\s+", " ", text)
    return text