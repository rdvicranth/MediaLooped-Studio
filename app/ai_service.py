from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from typing import Any

from openai import OpenAI

KEYCHAIN_SERVICE = "MediaLooped Studio OpenAI"
KEYCHAIN_ACCOUNT = "openai-api-key"
DEFAULT_MODEL = "gpt-5.6-luna"


class AIConfigurationError(RuntimeError):
    pass


def save_api_key(api_key: str) -> None:
    key = api_key.strip()
    if not key:
        raise AIConfigurationError("The API key is empty.")

    subprocess.run(
        [
            "security", "add-generic-password",
            "-U",
            "-a", KEYCHAIN_ACCOUNT,
            "-s", KEYCHAIN_SERVICE,
            "-w", key,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def load_api_key() -> str | None:
    result = subprocess.run(
        [
            "security", "find-generic-password",
            "-a", KEYCHAIN_ACCOUNT,
            "-s", KEYCHAIN_SERVICE,
            "-w",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def delete_api_key() -> None:
    subprocess.run(
        [
            "security", "delete-generic-password",
            "-a", KEYCHAIN_ACCOUNT,
            "-s", KEYCHAIN_SERVICE,
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _data_url(image_path: Path) -> str:
    suffix = image_path.suffix.lower()
    mime = "image/png"
    if suffix in {".jpg", ".jpeg"}:
        mime = "image/jpeg"
    elif suffix == ".webp":
        mime = "image/webp"

    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].lstrip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("The AI response did not contain a JSON object.")

    data = json.loads(cleaned[start:end + 1])
    required = {
        "scene", "place", "emotion", "activity",
        "story_role", "importance", "memory_notes",
    }
    missing = required.difference(data)
    if missing:
        raise ValueError(f"AI response is missing: {', '.join(sorted(missing))}")

    data["importance"] = max(1, min(5, int(data["importance"])))
    for key in required - {"importance"}:
        data[key] = str(data[key]).strip()
    return data


def analyze_memory(
    image_path: Path,
    *,
    filename: str,
    media_type: str,
    created_at: str,
    vacation_name: str = "",
    vacation_folder: str = "",
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    api_key = load_api_key()
    if not api_key:
        raise AIConfigurationError(
            "No OpenAI API key is saved. Open Settings and save your key first."
        )

    context_name = vacation_name or "Unknown journey"
    context_folder = vacation_folder or "Unknown folder"

    likely_places = ""
    if "banff" in f"{context_name} {context_folder}".lower():
        likely_places = """
Likely places for this journey include Moraine Lake, Lake Louise, Peyto Lake,
Bow Lake, Crowfoot Glacier, Johnston Canyon, Bow Falls, Surprise Corner,
Vermilion Lakes, Cascade Ponds, Lake Minnewanka, Banff Avenue, Sulphur
Mountain, Banff Gondola, Athabasca Glacier, Icefields Parkway, Canmore,
Custer State Park, and road-trip stops between Illinois and Alberta.
"""

    prompt = f"""
You are the Memory DNA engine for a private family-memory application.

Analyze the supplied image carefully. It may be a photo or a representative
thumbnail from a video.

Journey context:
- Journey name: {context_name}
- Journey folder: {context_folder}
- Filename: {filename}
- Media type: {media_type}
- Capture time: {created_at or "unknown"}
{likely_places}

Return ONLY one valid JSON object with exactly these keys:
{{
  "scene": "short visual scene label",
  "place": "specific place only when visually supported; otherwise a cautious broad place such as mountain park, road, hotel, or unknown",
  "emotion": "short observable mood; do not infer sensitive traits",
  "activity": "what is visibly happening",
  "story_role": "one of Opening, Journey, Establishing, Family Moment, Adventure, Highlight, Transition, Reflection, Ending",
  "importance": 1,
  "memory_notes": "one warm factual sentence useful to a Netflix-style family travel documentary"
}}

Rules:
- Importance must be an integer from 1 to 5.
- Do not identify or name people.
- Prefer the proper landmark name when the image and journey context strongly support it.
- Example: use "Moraine Lake" instead of "turquoise mountain lake" when the distinctive view is strongly consistent with Moraine Lake.
- If the exact place is uncertain, use a cautious broad place rather than inventing certainty.
- Do not infer age, ethnicity, religion, health, or relationships.
- For a video thumbnail, describe only what is visible in this frame.
- Keep every value concise.
"""

    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": _data_url(image_path),
                        "detail": "low",
                    },
                ],
            }
        ],
        max_output_tokens=500,
    )
    return _extract_json(response.output_text)
