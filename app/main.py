#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image

from ai_service import (
    AIConfigurationError,
    DEFAULT_MODEL,
    analyze_memory,
    delete_api_key,
    load_api_key,
    save_api_key,
)

APP_VERSION = "0.7.1-alpha"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "medialooped.db"
THUMB_DIR = DATA_DIR / "thumbnails"
DATA_DIR.mkdir(exist_ok=True)
THUMB_DIR.mkdir(exist_ok=True)


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{num_bytes} B"


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_columns() -> None:
    conn = db()
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(media)").fetchall()}
    columns = {
        "favorite": "INTEGER NOT NULL DEFAULT 0",
        "scene": "TEXT NOT NULL DEFAULT ''",
        "place": "TEXT NOT NULL DEFAULT ''",
        "emotion": "TEXT NOT NULL DEFAULT ''",
        "activity": "TEXT NOT NULL DEFAULT ''",
        "story_role": "TEXT NOT NULL DEFAULT ''",
        "importance": "INTEGER NOT NULL DEFAULT 3",
        "memory_notes": "TEXT NOT NULL DEFAULT ''",
        "ai_analyzed": "INTEGER NOT NULL DEFAULT 0",
        "story_date": "TEXT NOT NULL DEFAULT ''",
        "story_date_source": "TEXT NOT NULL DEFAULT ''",
        "story_date_confidence": "INTEGER NOT NULL DEFAULT 0",
    }
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE media ADD COLUMN {name} {ddl}")
    conn.commit()
    conn.close()


def list_vacations() -> list[dict]:
    conn = db()
    rows = conn.execute("""
        SELECT id, name, folder_path, total_files, photos, videos, total_bytes
        FROM vacations ORDER BY analyzed_at DESC
    """).fetchall()
    conn.close()
    return [{**dict(r), "total_size": human_size(r["total_bytes"])} for r in rows]


def get_vacation(vacation_id: int) -> dict | None:
    conn = db()
    row = conn.execute("SELECT * FROM vacations WHERE id=?", (vacation_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_media(vacation_id: int, media_filter: str, search: str) -> list[dict]:
    conn = db()
    query = "SELECT * FROM media WHERE vacation_id=?"
    params: list[object] = [vacation_id]

    if media_filter == "Favorites":
        query += " AND favorite=1"
    elif media_filter == "AI Analyzed":
        query += " AND ai_analyzed=1"
    elif media_filter in {"Photo", "Video"}:
        query += " AND media_type=?"
        params.append(media_filter)

    if search.strip():
        query += """ AND (
            lower(filename) LIKE ? OR lower(scene) LIKE ? OR lower(place) LIKE ?
            OR lower(emotion) LIKE ? OR lower(activity) LIKE ?
            OR lower(story_role) LIKE ? OR lower(memory_notes) LIKE ?
        )"""
        token = f"%{search.strip().lower()}%"
        params.extend([token] * 7)

    query += " ORDER BY favorite DESC, importance DESC, quality_score DESC, filename"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]
def build_vacation_dna(vacation_id: int) -> dict:
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM media
        WHERE vacation_id=?
        ORDER BY filename
        """,
        (vacation_id,)
    ).fetchall()

    conn.close()

    media = [dict(r) for r in rows]

    total = len(media)
    photos = sum(1 for item in media if item["media_type"] == "Photo")
    videos = sum(1 for item in media if item["media_type"] == "Video")
    analyzed = sum(1 for item in media if item["ai_analyzed"])
    favorites = sum(1 for item in media if item["favorite"])

    def count_values(field_name: str) -> list[tuple[str, int]]:
        counts = {}

        for item in media:
            value = item.get(field_name)

            if value:
                value = str(value).strip()

                if value:
                    counts[value] = counts.get(value, 0) + 1

        return sorted(
            counts.items(),
            key=lambda pair: (-pair[1], pair[0].lower())
        )

    return {
        "vacation_id": vacation_id,
        "total_memories": total,
        "photos": photos,
        "videos": videos,
        "ai_analyzed": analyzed,
        "favorites": favorites,
        "places": count_values("place"),
        "emotions": count_values("emotion"),
        "activities": count_values("activity"),
        "story_roles": count_values("story_role"),
    }
def build_story_blueprint(vacation_id: int) -> dict:
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM media
        WHERE vacation_id=?
          AND ai_analyzed=1
        ORDER BY
            favorite DESC,
            importance DESC,
            quality_score DESC,
            filename
        """,
        (vacation_id,)
    ).fetchall()

    conn.close()

    memories = [dict(r) for r in rows]

    chapters = [
        ("opening", {"Opening", "Establishing"}, 4),
        ("journey", {"Journey", "Transition"}, 8),
        ("family", {"Family Moment"}, 12),
        ("adventure", {"Adventure"}, 10),
        ("highlights", {"Highlight"}, 10),
    ]

    used_ids = set()
    blueprint = {}

    def normalized(value):
        return (value or "").strip().lower()

    def choose_diverse(candidates, limit):
        selected = []
        place_counts = {}
        scene_keys = set()

        for memory in candidates:
            if memory["id"] in used_ids:
                continue

            place = normalized(memory.get("place"))
            scene = normalized(memory.get("scene"))

            # Avoid selecting too many memories from one location.
            if place and place_counts.get(place, 0) >= 2:
                continue

            # Avoid obvious duplicate/similar scene descriptions.
            scene_key = scene[:45]
            if scene_key and scene_key in scene_keys:
                continue

            selected.append(memory)
            used_ids.add(memory["id"])

            if place:
                place_counts[place] = place_counts.get(place, 0) + 1

            if scene_key:
                scene_keys.add(scene_key)

            if len(selected) >= limit:
                break

        selected.sort(
            key=lambda memory: (
                memory.get("content_created") or "",
                memory.get("filename") or ""
            )
        )

        return selected
        

    for chapter_name, roles, limit in chapters:
        candidates = [
            memory
            for memory in memories
            if memory.get("story_role") in roles
        ]

        blueprint[chapter_name] = choose_diverse(candidates, limit)

    return blueprint
def build_trip_chapters(vacation_id: int) -> list[dict]:
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM media
        WHERE vacation_id=?
          AND ai_analyzed=1
          AND content_created IS NOT NULL
          AND content_created != ''
        """,
        (vacation_id,)
    ).fetchall()

    conn.close()

    memories = [dict(r) for r in rows]

    for memory in memories:
        memory["_effective_story_date"] = (
            memory.get("story_date")
            or (memory.get("content_created") or "")[:10]
        )

    memories.sort(
        key=lambda memory: (
            memory.get("_effective_story_date") or "",
            memory.get("content_created") or "",
            memory.get("filename") or ""
        )
    )

    chapters = []
    current = None

    for memory in memories:
        date_key = memory.get("_effective_story_date") or ""
        place = (memory.get("place") or "").strip()

        if not date_key:
            continue

        if current is None or current["date"] != date_key:
            current = {
                "date": date_key,
                "places": [],
                "memories": [],
            }
            chapters.append(current)

        if place and place not in current["places"]:
            current["places"].append(place)

        current["memories"].append(memory)

    return chapters
def build_film_storyboard(vacation_id: int) -> list[dict]:
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM media
        WHERE vacation_id=?
          AND ai_analyzed=1
          AND duplicate_of=''
        """,
        (vacation_id,)
    ).fetchall()

    conn.close()

    memories = [dict(r) for r in rows]

    def effective_date(memory):
        return (
            memory.get("story_date")
            or (memory.get("content_created") or "")[:10]
        )

    def text(memory, key):
        return (memory.get(key) or "").strip()

    # Ignore unresolved outlier dates for the first film cut.
    memories = [
        memory
        for memory in memories
        if "2026-07-09" <= effective_date(memory) <= "2026-07-19"
    ]

    events = [
        {
            "name": "Cold Open",
            "dates": {"2026-07-15", "2026-07-16", "2026-07-18"},
            "keywords": [
                "moraine",
                "lake louise",
                "peyto",
                "athabasca",
                "gondola",
                "canadian rockies",
            ],
            "target_seconds": 12,
            "max_shots": 3,
        },
        {
            "name": "Departure",
            "dates": {"2026-07-09"},
            "keywords": ["home driveway"],
            "target_seconds": 7,
            "max_shots": 2,
        },
        {
            "name": "The Road West",
            "dates": {"2026-07-10"},
            "keywords": [
                "wall drug",
                "road-trip",
                "roadside",
                "dinosaur",
            ],
            "target_seconds": 12,
            "max_shots": 4,
        },
        {
            "name": "Badlands",
            "dates": {"2026-07-11"},
            "keywords": ["badlands"],
            "target_seconds": 16,
            "max_shots": 4,
        },
        {
            "name": "Black Hills & Wildlife",
            "dates": {"2026-07-11", "2026-07-12"},
            "keywords": [
                "custer",
                "mount rushmore",
                "wildlife",
                "bison",
                "bear",
            ],
            "target_seconds": 18,
            "max_shots": 5,
        },
        {
            "name": "Crossing North",
            "dates": {"2026-07-13"},
            "keywords": ["border", "road-trip"],
            "target_seconds": 8,
            "max_shots": 2,
        },
        {
            "name": "Arrival in Banff",
            "dates": {"2026-07-14"},
            "keywords": [
                "banff",
                "canmore",
                "bow river",
                "bow falls",
                "banff avenue",
            ],
            "target_seconds": 22,
            "max_shots": 6,
        },
        {
            "name": "Lake Louise",
            "dates": {"2026-07-15"},
            "keywords": ["lake louise"],
            "target_seconds": 18,
            "max_shots": 5,
        },
        {
            "name": "Moraine Lake",
            "dates": {"2026-07-15"},
            "keywords": ["moraine"],
            "target_seconds": 20,
            "max_shots": 5,
        },
        {
            "name": "Icefields Parkway",
            "dates": {"2026-07-16"},
            "keywords": [
                "peyto",
                "bow lake",
                "icefields parkway",
                "mountain overlook",
            ],
            "target_seconds": 24,
            "max_shots": 6,
        },
        {
            "name": "Athabasca Glacier",
            "dates": {"2026-07-16"},
            "keywords": ["athabasca"],
            "target_seconds": 26,
            "max_shots": 6,
        },
        {
            "name": "Johnston Canyon",
            "dates": {"2026-07-17"},
            "keywords": ["johnston canyon"],
            "target_seconds": 20,
            "max_shots": 5,
        },
        {
            "name": "Sulphur Mountain",
            "dates": {"2026-07-18"},
            "keywords": [
                "gondola",
                "sulphur",
                "mountain overlook",
                "canadian rockies",
            ],
            "target_seconds": 22,
            "max_shots": 5,
        },
        {
            "name": "Farewell",
            "dates": {"2026-07-18", "2026-07-19"},
            "keywords": [
                "lake minnewanka",
                "banff",
                "family",
                "lodging",
            ],
            "target_seconds": 18,
            "max_shots": 5,
        },
    ]

    def event_match_score(memory, event):
        score = 0

        if effective_date(memory) not in event["dates"]:
            return -9999

        haystack = " ".join([
            text(memory, "place"),
            text(memory, "scene"),
            text(memory, "activity"),
        ]).lower()

        keyword_hits = sum(
            1
            for keyword in event["keywords"]
            if keyword in haystack
        )

        if keyword_hits == 0:
            return -9999

        score += keyword_hits * 80
        score += int(memory.get("importance") or 0) * 25
        score += int(memory.get("quality_score") or 0)

        role = text(memory, "story_role").lower()

        if "highlight" in role:
            score += 35
        elif "family" in role:
            score += 25
        elif "adventure" in role:
            score += 20
        elif "establish" in role:
            score += 15
        elif "journey" in role or "transition" in role:
            score += 12

        if memory.get("favorite"):
            score += 30

        if memory.get("media_type") == "Video":
            score += 20

        # Hard rejection / penalty rules.
        scene = text(memory, "scene").lower()
        place = text(memory, "place").lower()

        if int(memory.get("importance") or 0) <= 1:
            score -= 200

        if "abstract" in scene or "motion-blurred" in scene:
            score -= 250

        if place in {"unknown", ""}:
            score -= 60

        return score

    def shot_duration(memory):
        if memory.get("media_type") == "Video":
            available = float(memory.get("duration_seconds") or 0)

            if available > 0:
                return max(3.0, min(5.5, available))

            return 4.5

        if int(memory.get("importance") or 0) == 5:
            return 4.0

        return 3.2

    storyboard = []
    used_ids = set()

    for event in events:
        candidates = []

        for memory in memories:
            if memory["id"] in used_ids:
                continue

            score = event_match_score(memory, event)

            if score > 0:
                candidates.append((score, memory))

        candidates.sort(
            key=lambda pair: pair[0],
            reverse=True
        )

        selected = []
        total_seconds = 0.0
        seen_scene_keys = set()
        media_counts = {"Photo": 0, "Video": 0}

        for score, memory in candidates:
            if len(selected) >= event["max_shots"]:
                break

            if total_seconds >= event["target_seconds"]:
                break

            scene_key = text(memory, "scene").lower()[:50]

            if scene_key and scene_key in seen_scene_keys:
                continue

            # Avoid photo-only PPT feel when video exists.
            if (
                memory.get("media_type") == "Photo"
                and media_counts["Photo"] >= 3
                and any(
                    candidate_memory.get("media_type") == "Video"
                    for _, candidate_memory in candidates
                )
            ):
                continue

            duration = shot_duration(memory)

            selected.append({
                "memory": memory,
                "score": score,
                "duration": duration,
            })

            used_ids.add(memory["id"])

            if scene_key:
                seen_scene_keys.add(scene_key)

            media_counts[memory.get("media_type")] += 1
            total_seconds += duration

        selected.sort(
            key=lambda item: (
                effective_date(item["memory"]),
                item["memory"].get("content_created") or "",
                item["memory"].get("filename") or "",
            )
        )

        storyboard.append({
            "name": event["name"],
            "target_seconds": event["target_seconds"],
            "shots": selected,
            "actual_seconds": sum(
                shot["duration"]
                for shot in selected
            ),
        })

    return storyboard
def save_memory_dna(media_id: int, values: dict, ai_analyzed: int | None = None) -> None:
    conn = db()
    if ai_analyzed is None:
        conn.execute("""
            UPDATE media SET favorite=?, scene=?, place=?, emotion=?, activity=?,
                story_role=?, importance=?, memory_notes=? WHERE id=?
        """, (
            values["favorite"], values["scene"], values["place"],
            values["emotion"], values["activity"], values["story_role"],
            values["importance"], values["memory_notes"], media_id
        ))
    else:
        conn.execute("""
            UPDATE media SET favorite=?, scene=?, place=?, emotion=?, activity=?,
                story_role=?, importance=?, memory_notes=?, ai_analyzed=? WHERE id=?
        """, (
            values["favorite"], values["scene"], values["place"],
            values["emotion"], values["activity"], values["story_role"],
            values["importance"], values["memory_notes"], ai_analyzed, media_id
        ))
    conn.commit()
    conn.close()



def list_pending_media(vacation_id: int) -> list[dict]:
    """Return items not yet saved as AI analyzed."""
    conn = db()
    rows = conn.execute("""
        SELECT * FROM media
        WHERE vacation_id=? AND ai_analyzed=0
        ORDER BY content_created, filename
    """, (vacation_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def full_path(vacation: dict, media: dict) -> Path:
    return Path(vacation["folder_path"]) / media["relative_path"]


def thumbnail(source: Path, key: str, size: int) -> Path | None:
    target = THUMB_DIR / f"{key}.png"
    if target.exists():
        return target
    try:
        subprocess.run(
            ["qlmanage", "-t", "-s", str(size), "-o", str(THUMB_DIR), str(source)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False, timeout=25
        )
        generated = THUMB_DIR / f"{source.name}.png"
        if generated.exists():
            generated.replace(target)
            return target
    except Exception:
        pass
    return None


def export_memory_dna(vacation_id: int, vacation_name: str) -> Path:
    conn = db()
    rows = conn.execute("""
        SELECT filename, media_type, relative_path, favorite, scene, place,
               emotion, activity, story_role, importance, memory_notes,
               quality_score, content_created, ai_analyzed
        FROM media WHERE vacation_id=?
        ORDER BY importance DESC, filename
    """, (vacation_id,)).fetchall()
    conn.close()
    output = DATA_DIR / f"{vacation_name.replace(' ', '_')}_Memory_DNA.json"
    output.write_text(
        json.dumps([dict(r) for r in rows], indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    return output


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ensure_columns()

        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")
        self.title(f"MediaLooped Studio — {APP_VERSION}")
        self.geometry("1260x800")
        self.minsize(1100, 710)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.current_vacation_id = None
        self.current_media = []
        self.images = []
        self.selected_media = None
        self.current_ai_button = None
        self.batch_cancel_requested = False
        self.batch_window = None

        self.build_sidebar()
        self.main = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(0, weight=1)
        self.show_home()

    def build_sidebar(self):
        side = ctk.CTkFrame(self, width=225, corner_radius=0)
        side.grid(row=0, column=0, sticky="nsew")
        side.grid_rowconfigure(8, weight=1)

        ctk.CTkLabel(side, text="MediaLooped", font=ctk.CTkFont(size=25, weight="bold")).grid(
            row=0, column=0, padx=24, pady=(28, 4), sticky="w"
        )
        ctk.CTkLabel(side, text="Studio", text_color=("gray45", "gray70")).grid(
            row=1, column=0, padx=24, pady=(0, 24), sticky="w"
        )

        items = [
            ("My Memories", self.show_home),
            ("Memory Explorer", self.open_first),
            ("Story Builder", self.coming_soon),
            ("Create Documentary", self.coming_soon),
            ("Settings", self.show_settings),
        ]
        for i, (label, command) in enumerate(items, 2):
            ctk.CTkButton(
                side, text=label, command=command, height=42, anchor="w",
                fg_color="transparent", text_color=("gray10", "gray90"),
                hover_color=("gray80", "gray25")
            ).grid(row=i, column=0, padx=14, pady=4, sticky="ew")

        ctk.CTkLabel(side, text=f"Alpha {APP_VERSION}", text_color=("gray50", "gray60")).grid(
            row=9, column=0, padx=24, pady=20, sticky="sw"
        )

    def clear(self):
        for w in self.main.winfo_children():
            w.destroy()
        self.images.clear()

    def show_home(self):
        self.clear()
        page = ctk.CTkScrollableFrame(self.main, fg_color="transparent")
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(page, text="My Memories", font=ctk.CTkFont(size=34, weight="bold")).grid(
            row=0, column=0, padx=40, pady=(34, 8), sticky="w"
        )
        ctk.CTkLabel(
            page, text="AI-assisted Memory DNA turns files into meaningful moments.",
            text_color=("gray40", "gray70"), font=ctk.CTkFont(size=16)
        ).grid(row=1, column=0, padx=40, pady=(0, 24), sticky="w")

        for row_index, vacation in enumerate(list_vacations(), 2):
            card = ctk.CTkFrame(page, corner_radius=18)
            card.grid(row=row_index, column=0, padx=40, pady=10, sticky="ew")
            card.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(card, text=vacation["name"].upper(),
                         font=ctk.CTkFont(size=22, weight="bold")).grid(
                row=0, column=0, padx=24, pady=(22, 4), sticky="w"
            )
            ctk.CTkLabel(
                card,
                text=f'{vacation["photos"]} photos • {vacation["videos"]} videos • {vacation["total_size"]}',
                text_color=("gray40", "gray70")
            ).grid(row=1, column=0, padx=24, pady=(0, 22), sticky="w")

            ctk.CTkButton(
                card, text="Open Memory Explorer",
                command=lambda vid=vacation["id"]: self.show_explorer(vid),
                width=210, height=42
            ).grid(row=0, column=1, rowspan=2, padx=24, pady=24)

    def open_first(self):
        vacations = list_vacations()
        if not vacations:
            messagebox.showinfo("MediaLooped", "No analyzed vacation found.")
            return
        self.show_explorer(vacations[0]["id"])

    def show_settings(self):
        self.clear()
        page = ctk.CTkFrame(self.main, fg_color="transparent")
        page.grid(row=0, column=0, sticky="nsew")

        ctk.CTkLabel(page, text="Settings", font=ctk.CTkFont(size=34, weight="bold")).pack(
            padx=48, pady=(42, 10), anchor="w"
        )
        ctk.CTkLabel(
            page,
            text="Your API key is stored in macOS Keychain—not in the project or database.",
            text_color=("gray40", "gray70"),
            font=ctk.CTkFont(size=16),
        ).pack(padx=48, pady=(0, 28), anchor="w")

        card = ctk.CTkFrame(page, corner_radius=18)
        card.pack(padx=48, pady=10, fill="x")

        current = load_api_key()
        status = "API key saved in Keychain" if current else "No API key saved"
        self.key_status = ctk.CTkLabel(
            card, text=status,
            font=ctk.CTkFont(size=18, weight="bold")
        )
        self.key_status.pack(padx=24, pady=(24, 10), anchor="w")

        self.key_entry = ctk.CTkEntry(
            card, placeholder_text="Paste OpenAI API key",
            show="•", height=44
        )
        self.key_entry.pack(padx=24, pady=10, fill="x")

        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(padx=24, pady=(8, 24), fill="x")
        ctk.CTkButton(buttons, text="Save to Keychain",
                      command=self.save_key_from_settings).pack(side="left", padx=(0, 10))
        ctk.CTkButton(buttons, text="Remove Key",
                      fg_color=("gray60", "gray35"),
                      command=self.remove_key_from_settings).pack(side="left")

        ctk.CTkLabel(
            page,
            text=f"AI model: {DEFAULT_MODEL}\n"
                 "Only the selected image or generated video thumbnail is sent when you click Analyze with AI.",
            justify="left", text_color=("gray40", "gray70")
        ).pack(padx=48, pady=20, anchor="w")

    def save_key_from_settings(self):
        try:
            save_api_key(self.key_entry.get())
            self.key_entry.delete(0, "end")
            self.key_status.configure(text="API key saved in Keychain")
            messagebox.showinfo("MediaLooped", "API key saved securely in macOS Keychain.")
        except Exception as exc:
            messagebox.showerror("MediaLooped", f"Could not save API key:\n{exc}")

    def remove_key_from_settings(self):
        delete_api_key()
        self.key_status.configure(text="No API key saved")
        messagebox.showinfo("MediaLooped", "API key removed from macOS Keychain.")

    def show_explorer(self, vacation_id: int):
        self.current_vacation_id = vacation_id
        self.clear()
        vacation = get_vacation(vacation_id)
        if not vacation:
            return

        root = ctk.CTkFrame(self.main, fg_color="transparent")
        root.grid(row=0, column=0, sticky="nsew")
        root.grid_columnconfigure(0, weight=3)
        root.grid_columnconfigure(1, weight=2)
        root.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            root, text=f'{vacation["name"]} — Memory Explorer',
            font=ctk.CTkFont(size=30, weight="bold")
        ).grid(row=0, column=0, columnspan=2, padx=28, pady=(24, 12), sticky="w")

        self.build_explorer_toolbar(root, vacation)

        self.build_gallery_panel(root)

        self.build_detail_panel(root)

        self.refresh()
    def build_explorer_toolbar(self, root, vacation):
        bar = ctk.CTkFrame(root, corner_radius=14)
        bar.grid(row=1, column=0, columnspan=2, padx=28, pady=(0, 14), sticky="ew")
        bar.grid_columnconfigure(0, weight=1)

        self.search = ctk.CTkEntry(
            bar,
            placeholder_text="Search filename, place, scene, emotion..."
        )
        self.search.grid(row=0, column=0, padx=(16, 8), pady=14, sticky="ew")

        self.filter_menu = ctk.CTkOptionMenu(
            bar,
            values=["All", "Favorites", "AI Analyzed", "Photo", "Video"],
            command=lambda _: self.refresh()
        )
        self.filter_menu.grid(row=0, column=1, padx=8, pady=14)

        ctk.CTkButton(
            bar,
            text="Search",
            width=90,
            command=self.refresh
        ).grid(row=0, column=2, padx=8, pady=14)

        ctk.CTkButton(
            bar,
            text="Export DNA",
            width=100,
            command=lambda: self.export_current(vacation)
        ).grid(row=0, column=3, padx=8, pady=14)

        ctk.CTkButton(
            bar,
            text="✨ Analyze Entire Vacation",
            width=185,
            command=lambda: self.show_batch_analysis(vacation)
        ).grid(row=0, column=4, padx=8, pady=14)

        ctk.CTkButton(
            bar,
            text="Back",
            width=70,
            command=self.show_home
        ).grid(row=0, column=5, padx=(8, 16), pady=14)
    def build_gallery_panel(self, root):
        self.gallery = ctk.CTkScrollableFrame(root, corner_radius=16)
        self.gallery.grid(
            row=2,
            column=0,
            padx=(28, 10),
            pady=(0, 24),
            sticky="nsew"
        )
    def build_detail_panel(self, root):
        self.detail = ctk.CTkScrollableFrame(root, corner_radius=16)
        self.detail.grid(
        row=2,
        column=1,
        padx=(10, 28),
        pady=(0, 24),
        sticky="nsew"
        )
    def build_memory_card(self, vacation, media, index):
        row, col = index // 3 + 1, index % 3
        source = full_path(vacation, media)
        thumb = thumbnail(source, f'{media["vacation_id"]}_{media["id"]}', 260)

        card = ctk.CTkFrame(self.gallery, corner_radius=12)
        card.grid(row=row, column=col, padx=8, pady=8, sticky="nsew")

        button = None

        if thumb and thumb.exists():
            try:
                pil = Image.open(thumb)
                pil.thumbnail((190, 135))
                img = ctk.CTkImage(
                    light_image=pil,
                    dark_image=pil,
                    size=pil.size
                )
                self.images.append(img)

                button = ctk.CTkButton(
                    card,
                    text="",
                    image=img,
                    fg_color="transparent",
                    hover_color=("gray85", "gray25"),
                    command=lambda m=media: self.show_detail(m)
                )
            except Exception:
                pass

        if button is None:
            button = ctk.CTkButton(
                card,
                text=media["media_type"],
                command=lambda m=media: self.show_detail(m)
            )

        button.pack(
            padx=8,
            pady=(8, 4),
            fill="both",
            expand=True
        )

        title = media["scene"] or media["filename"]

        if len(title) > 24:
            title = title[:21] + "..."

        prefix = (
            "✨ "
            if media["ai_analyzed"]
            else ("♥ " if media["favorite"] else "")
        )

        ctk.CTkLabel(
            card,
            text=prefix + title,
            font=ctk.CTkFont(size=11)
        ).pack(
            padx=8,
            pady=(2, 8)
        )   
    def refresh(self):
        if self.current_vacation_id is None:
            return
        for w in self.gallery.winfo_children():
            w.destroy()
        self.images.clear()

        vacation = get_vacation(self.current_vacation_id)
        self.current_media = list_media(
            self.current_vacation_id,
            self.filter_menu.get(),
            self.search.get()
        )

        ctk.CTkLabel(
            self.gallery, text=f"{len(self.current_media)} memories",
            font=ctk.CTkFont(size=16, weight="bold")
        ).grid(row=0, column=0, columnspan=3, padx=12, pady=(12, 16), sticky="w")

        for col in range(3):
            self.gallery.grid_columnconfigure(col, weight=1)

        for index, media in enumerate(self.current_media):
            self.build_memory_card(vacation, media, index)
            

        if self.current_media:
            self.show_detail(self.current_media[0])
    def build_detail_preview(self, image_path):
        if image_path and image_path.exists():
            try:
                pil = Image.open(image_path)
                pil.thumbnail((380, 280))
                img = ctk.CTkImage(
                    light_image=pil,
                    dark_image=pil,
                    size=pil.size
                )
                self.images.append(img)

                ctk.CTkLabel(
                    self.detail,
                    text="",
                    image=img
                ).pack(
                    padx=18,
                    pady=(18, 12)
                )
            except Exception:
                pass
    
    def build_ai_action_block(self, media, image_path):
            ctk.CTkLabel(
                self.detail,
                text="AI Memory Card",
                font=ctk.CTkFont(size=22, weight="bold")
            ).pack(
                padx=18,
                pady=(8, 12),
                anchor="w"
            )

            self.current_ai_button = ctk.CTkButton(
                self.detail,
                text="✨ Analyze with AI",
                command=lambda: self.start_ai_analysis(media, image_path),
                height=44
            )
            self.current_ai_button.pack(
                padx=18,
                pady=(0, 10),
                fill="x"
            )

            ctk.CTkLabel(
                self.detail,
                text="A selected image—or one representative frame for a video—will be sent to OpenAI.",
                wraplength=380,
                justify="left",
                text_color=("gray40", "gray70")
            ).pack(
                padx=18,
                pady=(0, 10),
                anchor="w"
            )
    
    def build_memory_fields(self, media):
        self.favorite_var = ctk.IntVar(value=media["favorite"])

        ctk.CTkCheckBox(
                self.detail,
                text="Favorite Memory",
                variable=self.favorite_var
            ).pack(
                padx=18,
                pady=6,
                anchor="w"
            )

        self.entries = {}

        fields = [
            ("scene", "Scene", media["scene"]),
            ("place", "Place", media["place"]),
            ("emotion", "Emotion", media["emotion"]),
            ("activity", "Activity", media["activity"]),
            ("story_role", "Story role", media["story_role"]),
        ]

        for key, label, value in fields:
            ctk.CTkLabel(
                self.detail,
                text=label
            ).pack(
                padx=18,
                pady=(8, 2),
                anchor="w"
            )

            entry = ctk.CTkEntry(self.detail)
            entry.insert(0, value or "")
            entry.pack(
                padx=18,
                pady=(0, 4),
                fill="x"
            )

        self.entries[key] = entry

    def show_detail(self, media: dict):
        self.selected_media = media
        for w in self.detail.winfo_children():
            w.destroy()

        vacation = get_vacation(media["vacation_id"])
        source = full_path(vacation, media)
        large = thumbnail(source, f'{media["vacation_id"]}_{media["id"]}_large', 700)

        self.build_detail_preview(large)

        self.build_ai_action_block(media, large)

        self.build_memory_fields(media)

        ctk.CTkLabel(self.detail, text="Importance (1–5)").pack(
            padx=18, pady=(10, 2), anchor="w"
        )
        self.importance_var = ctk.IntVar(value=media["importance"] or 3)
        self.importance = ctk.CTkSlider(
            self.detail, from_=1, to=5, number_of_steps=4,
            variable=self.importance_var
        )
        self.importance.pack(padx=18, pady=(0, 8), fill="x")

        ctk.CTkLabel(self.detail, text="Memory notes").pack(
            padx=18, pady=(8, 2), anchor="w"
        )
        self.notes = ctk.CTkTextbox(self.detail, height=100)
        self.notes.insert("1.0", media["memory_notes"] or "")
        self.notes.pack(padx=18, pady=(0, 10), fill="x")

        ctk.CTkButton(
            self.detail, text="Save Memory DNA",
            command=self.save_current, height=42
        ).pack(padx=18, pady=(8, 6), fill="x")

        ctk.CTkButton(
            self.detail, text="Open Original",
            command=lambda: subprocess.run(["open", str(source)], check=False),
            height=42
        ).pack(padx=18, pady=6, fill="x")

    def start_ai_analysis(self, media: dict, image_path: Path | None):
        if image_path is None or not image_path.exists():
            messagebox.showerror(
                "MediaLooped",
                "A preview image could not be created for this memory."
            )
            return
        if not load_api_key():
            messagebox.showinfo(
                "MediaLooped",
                "Open Settings and save your OpenAI API key first."
            )
            return

        self.current_ai_button.configure(text="Analyzing…", state="disabled")
        threading.Thread(
            target=self.run_ai_analysis,
            args=(media, image_path),
            daemon=True,
        ).start()

    def run_ai_analysis(self, media: dict, image_path: Path):
        try:
            vacation = get_vacation(media["vacation_id"])
            result = analyze_memory(
                image_path,
                filename=media["filename"],
                media_type=media["media_type"],
                created_at=media["content_created"] or "",
                vacation_name=vacation["name"] if vacation else "",
                vacation_folder=vacation["folder_path"] if vacation else "",
            )
            self.after(0, self.apply_ai_result, media, result)
        except Exception as exc:
            self.after(0, self.ai_error, str(exc))

    def apply_ai_result(self, media: dict, result: dict):
        for key in ("scene", "place", "emotion", "activity", "story_role"):
            self.entries[key].delete(0, "end")
            self.entries[key].insert(0, result[key])

        self.importance_var.set(result["importance"])
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", result["memory_notes"])
        self.current_ai_button.configure(text="✨ Analyze Again", state="normal")

        messagebox.showinfo(
            "MediaLooped",
            "AI suggestions are ready. Review them, then click Save Memory DNA."
        )

    def ai_error(self, error: str):
        if self.current_ai_button:
            self.current_ai_button.configure(text="✨ Analyze with AI", state="normal")
        messagebox.showerror("MediaLooped AI", error)

    def save_current(self):
        if not self.selected_media:
            return
        values = {
            "favorite": int(self.favorite_var.get()),
            "scene": self.entries["scene"].get().strip(),
            "place": self.entries["place"].get().strip(),
            "emotion": self.entries["emotion"].get().strip(),
            "activity": self.entries["activity"].get().strip(),
            "story_role": self.entries["story_role"].get().strip(),
            "importance": int(round(self.importance_var.get())),
            "memory_notes": self.notes.get("1.0", "end").strip(),
        }
        save_memory_dna(self.selected_media["id"], values, ai_analyzed=1)
        messagebox.showinfo("MediaLooped", "Memory DNA saved locally.")
        self.refresh()


    def show_batch_analysis(self, vacation: dict):
        if not load_api_key():
            messagebox.showinfo(
                "MediaLooped",
                "Open Settings and save your OpenAI API key first."
            )
            return

        pending = list_pending_media(vacation["id"])
        completed = vacation["total_files"] - len(pending)

        window = ctk.CTkToplevel(self)
        window.title("Analyze Entire Vacation")
        window.geometry("620x500")
        window.resizable(False, False)
        window.transient(self)
        window.grab_set()
        self.batch_window = window
        self.batch_cancel_requested = False

        ctk.CTkLabel(
            window,
            text="✨ Analyze Entire Vacation",
            font=ctk.CTkFont(size=27, weight="bold")
        ).pack(padx=32, pady=(30, 8), anchor="w")

        ctk.CTkLabel(
            window,
            text=(
                f'{vacation["name"]}\n\n'
                f'Total memories: {vacation["total_files"]}\n'
                f'Already analyzed: {completed}\n'
                f'Remaining: {len(pending)}'
            ),
            justify="left",
            font=ctk.CTkFont(size=16)
        ).pack(padx=32, pady=(8, 20), anchor="w")

        ctk.CTkLabel(
            window,
            text=(
                "MediaLooped will process one memory at a time and save after "
                "every successful result. You may stop and resume later without "
                "paying again for completed memories.\n\n"
                "API usage charges apply. Monitor exact spending in the OpenAI "
                "Platform Usage page."
            ),
            wraplength=550,
            justify="left",
            text_color=("gray35", "gray70")
        ).pack(padx=32, pady=(0, 20), anchor="w")

        self.batch_progress = ctk.CTkProgressBar(window, width=550)
        self.batch_progress.set(0)
        self.batch_progress.pack(padx=32, pady=(8, 10))

        self.batch_status = ctk.CTkLabel(
            window,
            text="Ready",
            wraplength=550,
            justify="left"
        )
        self.batch_status.pack(padx=32, pady=8, anchor="w")

        controls = ctk.CTkFrame(window, fg_color="transparent")
        controls.pack(padx=32, pady=(18, 28), fill="x")

        self.batch_start_button = ctk.CTkButton(
            controls,
            text="Start / Resume",
            height=44,
            command=lambda: self.start_batch_analysis(vacation)
        )
        self.batch_start_button.pack(side="left", padx=(0, 10))

        self.batch_cancel_button = ctk.CTkButton(
            controls,
            text="Stop Safely",
            height=44,
            state="disabled",
            fg_color=("gray55", "gray35"),
            command=self.request_batch_cancel
        )
        self.batch_cancel_button.pack(side="left", padx=10)

        ctk.CTkButton(
            controls,
            text="Close",
            height=44,
            fg_color=("gray55", "gray35"),
            command=window.destroy
        ).pack(side="right")

        if not pending:
            self.batch_status.configure(
                text="All memories have already been analyzed."
            )
            self.batch_start_button.configure(state="disabled")
            self.batch_progress.set(1)

    def start_batch_analysis(self, vacation: dict):
        # Always re-read the database when Start / Resume is clicked.
        # This prevents the dialog from reusing the stale pending list that
        # existed when the window was first opened.
        pending = list_pending_media(vacation["id"])

        if not pending:
            self.batch_progress.set(1)
            self.batch_status.configure(
                text="All memories have already been analyzed."
            )
            self.batch_start_button.configure(state="disabled")
            self.batch_cancel_button.configure(state="disabled")
            return

        completed = vacation["total_files"] - len(pending)
        self.batch_cancel_requested = False
        self.batch_start_button.configure(state="disabled")
        self.batch_cancel_button.configure(state="normal")
        self.batch_status.configure(
            text=(
                f"Resuming from the first unanalyzed memory.\n"
                f"Already analyzed: {completed}\n"
                f"Remaining: {len(pending)}"
            )
        )

        threading.Thread(
            target=self.run_batch_analysis,
            args=(vacation, pending),
            daemon=True
        ).start()

    def request_batch_cancel(self):
        self.batch_cancel_requested = True
        self.batch_status.configure(
            text="Stopping safely after the current memory…"
        )
        self.batch_cancel_button.configure(state="disabled")

    def run_batch_analysis(self, vacation: dict, pending: list[dict]):
        total = len(pending)
        completed = 0
        failures: list[str] = []

        for index, media in enumerate(pending, start=1):
            if self.batch_cancel_requested:
                break

            self.after(
                0,
                self.update_batch_status,
                index - 1,
                total,
                f'Analyzing {index} of {total}: {media["filename"]}'
            )

            source = full_path(vacation, media)
            image_path = thumbnail(
                source,
                f'{media["vacation_id"]}_{media["id"]}_batch_ai',
                900
            )

            if image_path is None or not image_path.exists():
                failures.append(f'{media["filename"]}: preview unavailable')
                continue

            try:
                result = analyze_memory(
                    image_path,
                    filename=media["filename"],
                    media_type=media["media_type"],
                    created_at=media["content_created"] or "",
                    vacation_name=vacation["name"],
                    vacation_folder=vacation["folder_path"],
                )

                values = {
                    "favorite": int(media["favorite"]),
                    "scene": result["scene"],
                    "place": result["place"],
                    "emotion": result["emotion"],
                    "activity": result["activity"],
                    "story_role": result["story_role"],
                    "importance": result["importance"],
                    "memory_notes": result["memory_notes"],
                }
                save_memory_dna(media["id"], values, ai_analyzed=1)
                completed += 1

            except Exception as exc:
                failures.append(f'{media["filename"]}: {exc}')

            self.after(
                0,
                self.update_batch_status,
                index,
                total,
                f'Completed {index} of {total}'
            )

        stopped = self.batch_cancel_requested
        self.after(
            0,
            self.finish_batch_analysis,
            completed,
            total,
            failures,
            stopped
        )

    def update_batch_status(self, current: int, total: int, text: str):
        if self.batch_window is None or not self.batch_window.winfo_exists():
            return
        self.batch_progress.set(current / total if total else 0)
        self.batch_status.configure(text=text)

    def finish_batch_analysis(
        self,
        completed: int,
        total: int,
        failures: list[str],
        stopped: bool
    ):
        if self.batch_window is None or not self.batch_window.winfo_exists():
            return

        self.batch_cancel_button.configure(state="disabled")

        if stopped:
            heading = "Analysis stopped safely."
        else:
            heading = "Vacation analysis finished."

        # Re-read the database so the counters reflect every successfully
        # saved memory, including earlier runs and this run.
        vacation = get_vacation(self.current_vacation_id)
        remaining_items = (
            list_pending_media(self.current_vacation_id)
            if self.current_vacation_id is not None else []
        )
        total_memories = vacation["total_files"] if vacation else total
        already_analyzed = total_memories - len(remaining_items)

        failure_text = (
            f"\nFailures: {len(failures)}"
            if failures else "\nFailures: 0"
        )
        self.batch_status.configure(
            text=(
                f"{heading}\n"
                f"Saved this run: {completed}\n"
                f"Already analyzed: {already_analyzed}\n"
                f"Remaining: {len(remaining_items)}"
                f"{failure_text}"
            )
        )

        if remaining_items:
            self.batch_start_button.configure(
                text="Start / Resume",
                state="normal"
            )
        else:
            self.batch_progress.set(1)
            self.batch_start_button.configure(
                text="All Complete",
                state="disabled"
            )

        # Refresh the gallery so AI titles and markers appear immediately.
        self.refresh()

        if failures:
            log_path = DATA_DIR / "batch_ai_failures.txt"
            log_path.write_text("\n".join(failures), encoding="utf-8")
            messagebox.showwarning(
                "MediaLooped",
                f"{heading}\n\n"
                f"Saved: {completed}\nFailures: {len(failures)}\n\n"
                f"Failure details were saved to:\n{log_path}"
            )
        else:
            messagebox.showinfo(
                "MediaLooped",
                f"{heading}\n\nSaved {completed} memories."
            )

    def export_current(self, vacation: dict):
        path = export_memory_dna(vacation["id"], vacation["name"])
        messagebox.showinfo("MediaLooped", f"Memory DNA exported to:\n{path}")

    def coming_soon(self):
        messagebox.showinfo("Coming next", "This module is on the MediaLooped roadmap.")


if __name__ == "__main__":
    App().mainloop()
