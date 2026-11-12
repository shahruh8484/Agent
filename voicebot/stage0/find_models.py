"""Показывает свежие открытые модели на Hugging Face, чтобы выбрать кандидатов для сравнения.

Запуск (на GPU-сервере): python stage0/find_models.py
Показывает instruct-модели от Qwen, Google, Meta, Mistral, DeepSeek и модели с упоминанием узбекского.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

QUERIES = [
    {"author": "Qwen", "search": "Instruct"},
    {"author": "Qwen", "search": "Qwen3"},
    {"author": "google", "search": "gemma"},
    {"author": "meta-llama", "search": "Instruct"},
    {"author": "mistralai", "search": "Instruct"},
    {"author": "deepseek-ai", "search": ""},
    {"search": "uzbek"},
]


def fetch(params: dict) -> list:
    q = {"sort": "lastModified", "direction": "-1", "limit": "15", **{k: v for k, v in params.items() if v}}
    url = "https://huggingface.co/api/models?" + urllib.parse.urlencode(q)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def main() -> None:
    for params in QUERIES:
        title = " ".join(f"{k}={v}" for k, v in params.items() if v)
        print(f"\n=== {title} ===")
        try:
            for m in fetch(params):
                tags = m.get("tags", [])
                if params.get("author") and "text-generation" not in tags and "image-text-to-text" not in tags:
                    continue
                print(f"  {m['id']:<60} обновлена {m.get('lastModified', '')[:10]}  "
                      f"скачиваний {m.get('downloads', 0):>9}")
        except Exception as e:  # noqa: BLE001
            print(f"  ошибка: {e}")
    print("\nДля 48 ГБ подходят модели ~27–35B в FP8 (в названии -FP8) или MoE вроде 30B-A3B.")
    print("Для 80 ГБ — 27–35B без сжатия или ~70B в FP8.")


if __name__ == "__main__":
    main()
