import re


def normalize_title(title: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", title.lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned
