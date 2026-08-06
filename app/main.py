#!/usr/bin/env python3
from __future__ import annotations

import os
import sqlite3
import subprocess
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

APP_TITLE = "MediaLooped Studio"
APP_VERSION = "0.5.0-alpha"
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


def list_vacations() -> list[dict]:
    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, name, folder_path, analyzed_at, total_files, photos, videos,
               other_files, duplicates, total_bytes
        FROM vacations
        ORDER BY analyzed_at DESC
    """).fetchall()
    conn.close()

    return [{**dict(r), "total_size": human_size(r["total_bytes"])} for r in rows]


def list_media(vacation_id: int, media_filter: str = "All", search: str = "") -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    query = """
        SELECT id, vacation_id, media_type, filename, relative_path, extension,
               size_bytes, width, height, duration_seconds, content_created,
               modified, quality_score, duplicate_of
        FROM media
        WHERE vacation_id = ?
    """
    params: list[object] = [vacation_id]

    if media_filter in {"Photo", "Video"}:
        query += " AND media_type = ?"
        params.append(media_filter)

    if search.strip():
        query += " AND lower(filename) LIKE ?"
        params.append(f"%{search.strip().lower()}%")

    query += " ORDER BY quality_score DESC, filename ASC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_vacation(vacation_id: int) -> dict | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("""
        SELECT id, name, folder_path, analyzed_at, total_files, photos, videos,
               other_files, duplicates, total_bytes
        FROM vacations WHERE id = ?
    """, (vacation_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def favorite_column_exists() -> bool:
    if not DB_PATH.exists():
        return False
    conn = sqlite3.connect(DB_PATH)
    cols = conn.execute("PRAGMA table_info(media)").fetchall()
    conn.close()
    return any(col[1] == "favorite" for col in cols)


def ensure_favorite_column() -> None:
    if not DB_PATH.exists() or favorite_column_exists():
        return
    conn = sqlite3.connect(DB_PATH)
    conn.execute("ALTER TABLE media ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    conn.close()


def get_favorite(media_id: int) -> bool:
    ensure_favorite_column()
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT favorite FROM media WHERE id = ?", (media_id,)).fetchone()
    conn.close()
    return bool(row[0]) if row else False


def set_favorite(media_id: int, value: bool) -> None:
    ensure_favorite_column()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE media SET favorite = ? WHERE id = ?", (1 if value else 0, media_id))
    conn.commit()
    conn.close()


def full_media_path(vacation: dict, media: dict) -> Path:
    return Path(vacation["folder_path"]) / media["relative_path"]


def create_thumbnail(source: Path, cache_key: str, size: int = 260) -> Path | None:
    target = THUMB_DIR / f"{cache_key}.png"
    if target.exists():
        return target

    try:
        subprocess.run(
            ["qlmanage", "-t", "-s", str(size), "-o", str(THUMB_DIR), str(source)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=25,
        )
        generated = THUMB_DIR / f"{source.name}.png"
        if generated.exists():
            generated.replace(target)
            return target
    except Exception:
        return None
    return None


class MediaLoopedStudio(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")

        self.title(f"{APP_TITLE} — {APP_VERSION}")
        self.geometry("1180x760")
        self.minsize(1000, 660)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.current_vacation_id: int | None = None
        self.current_media: list[dict] = []
        self.thumb_images: list[ctk.CTkImage] = []
        self.selected_media: dict | None = None

        self._build_sidebar()
        self._build_main()
        self.show_home()

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, width=225, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_rowconfigure(8, weight=1)

        ctk.CTkLabel(sidebar, text="MediaLooped", font=ctk.CTkFont(size=25, weight="bold")).grid(
            row=0, column=0, padx=24, pady=(28, 4), sticky="w"
        )
        ctk.CTkLabel(sidebar, text="Studio", text_color=("gray45", "gray70"),
                     font=ctk.CTkFont(size=15)).grid(
            row=1, column=0, padx=24, pady=(0, 24), sticky="w"
        )

        buttons = [
            ("My Memories", self.show_home),
            ("Memory Explorer", self.open_first_vacation),
            ("Story Builder", self.coming_soon),
            ("Create Documentary", self.coming_soon),
            ("Settings", self.coming_soon),
        ]
        for row, (label, command) in enumerate(buttons, start=2):
            ctk.CTkButton(
                sidebar, text=label, command=command, height=42, anchor="w",
                fg_color="transparent", text_color=("gray10", "gray90"),
                hover_color=("gray80", "gray25")
            ).grid(row=row, column=0, padx=14, pady=4, sticky="ew")

        ctk.CTkLabel(sidebar, text=f"Alpha {APP_VERSION}",
                     text_color=("gray50", "gray60"),
                     font=ctk.CTkFont(size=12)).grid(
            row=9, column=0, padx=24, pady=20, sticky="sw"
        )

    def _build_main(self) -> None:
        self.main = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(0, weight=1)

    def clear_main(self) -> None:
        for widget in self.main.winfo_children():
            widget.destroy()
        self.thumb_images.clear()

    def show_home(self) -> None:
        self.clear_main()
        page = ctk.CTkScrollableFrame(self.main, fg_color="transparent")
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(page, text="My Memories",
                     font=ctk.CTkFont(size=34, weight="bold")).grid(
            row=0, column=0, padx=40, pady=(34, 8), sticky="w"
        )
        ctk.CTkLabel(page, text="Open a journey to explore its photos and videos.",
                     text_color=("gray40", "gray70"),
                     font=ctk.CTkFont(size=16)).grid(
            row=1, column=0, padx=40, pady=(0, 24), sticky="w"
        )

        vacations = list_vacations()
        if not vacations:
            ctk.CTkLabel(page, text="No analyzed vacations found. Run Alpha 0.4 first.").grid(
                row=2, column=0, padx=40, pady=40, sticky="w"
            )
            return

        for row_index, vacation in enumerate(vacations, start=2):
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
                command=lambda vid=vacation["id"]: self.show_memory_explorer(vid),
                width=200, height=42
            ).grid(row=0, column=1, rowspan=2, padx=24, pady=24)

    def open_first_vacation(self) -> None:
        vacations = list_vacations()
        if not vacations:
            messagebox.showinfo("MediaLooped", "No analyzed vacation is available yet.")
            return
        self.show_memory_explorer(vacations[0]["id"])

    def show_memory_explorer(self, vacation_id: int) -> None:
        self.current_vacation_id = vacation_id
        self.clear_main()

        vacation = get_vacation(vacation_id)
        if not vacation:
            messagebox.showerror("MediaLooped", "Vacation not found.")
            self.show_home()
            return

        root = ctk.CTkFrame(self.main, fg_color="transparent")
        root.grid(row=0, column=0, sticky="nsew")
        root.grid_columnconfigure(0, weight=3)
        root.grid_columnconfigure(1, weight=2)
        root.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(root, text=f'{vacation["name"]} — Memory Explorer',
                     font=ctk.CTkFont(size=30, weight="bold")).grid(
            row=0, column=0, columnspan=2, padx=28, pady=(24, 12), sticky="w"
        )

        toolbar = ctk.CTkFrame(root, corner_radius=14)
        toolbar.grid(row=1, column=0, columnspan=2, padx=28, pady=(0, 14), sticky="ew")
        toolbar.grid_columnconfigure(0, weight=1)

        self.search_entry = ctk.CTkEntry(toolbar, placeholder_text="Search filename...")
        self.search_entry.grid(row=0, column=0, padx=(16, 8), pady=14, sticky="ew")

        self.filter_menu = ctk.CTkOptionMenu(
            toolbar,
            values=["All", "Photo", "Video"],
            command=lambda _: self.refresh_gallery()
        )
        self.filter_menu.set("All")
        self.filter_menu.grid(row=0, column=1, padx=8, pady=14)

        ctk.CTkButton(toolbar, text="Search", width=100,
                      command=self.refresh_gallery).grid(row=0, column=2, padx=8, pady=14)
        ctk.CTkButton(toolbar, text="Back", width=90,
                      command=self.show_home).grid(row=0, column=3, padx=(8, 16), pady=14)

        self.gallery = ctk.CTkScrollableFrame(root, corner_radius=16)
        self.gallery.grid(row=2, column=0, padx=(28, 10), pady=(0, 24), sticky="nsew")

        self.detail = ctk.CTkScrollableFrame(root, corner_radius=16)
        self.detail.grid(row=2, column=1, padx=(10, 28), pady=(0, 24), sticky="nsew")

        self.refresh_gallery()

    def refresh_gallery(self) -> None:
        assert self.current_vacation_id is not None
        for widget in self.gallery.winfo_children():
            widget.destroy()
        self.thumb_images.clear()

        search = self.search_entry.get() if hasattr(self, "search_entry") else ""
        media_filter = self.filter_menu.get() if hasattr(self, "filter_menu") else "All"
        self.current_media = list_media(self.current_vacation_id, media_filter, search)

        ctk.CTkLabel(
            self.gallery,
            text=f"{len(self.current_media)} memories",
            font=ctk.CTkFont(size=16, weight="bold")
        ).grid(row=0, column=0, columnspan=3, padx=12, pady=(12, 16), sticky="w")

        vacation = get_vacation(self.current_vacation_id)
        assert vacation is not None

        for col in range(3):
            self.gallery.grid_columnconfigure(col, weight=1)

        for index, media in enumerate(self.current_media):
            row = index // 3 + 1
            col = index % 3
            source = full_media_path(vacation, media)
            cache_key = f'{self.current_vacation_id}_{media["id"]}'
            thumb_path = create_thumbnail(source, cache_key)

            card = ctk.CTkFrame(self.gallery, corner_radius=12)
            card.grid(row=row, column=col, padx=8, pady=8, sticky="nsew")

            if thumb_path and thumb_path.exists():
                try:
                    pil = Image.open(thumb_path)
                    pil.thumbnail((180, 125))
                    img = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
                    self.thumb_images.append(img)
                    preview = ctk.CTkButton(
                        card, text="", image=img, fg_color="transparent",
                        hover_color=("gray85", "gray25"),
                        command=lambda m=media: self.show_media_detail(m)
                    )
                except Exception:
                    preview = ctk.CTkButton(
                        card, text=media["media_type"],
                        command=lambda m=media: self.show_media_detail(m)
                    )
            else:
                preview = ctk.CTkButton(
                    card, text=media["media_type"],
                    command=lambda m=media: self.show_media_detail(m)
                )

            preview.pack(padx=8, pady=(8, 4), fill="both", expand=True)

            short_name = media["filename"]
            if len(short_name) > 22:
                short_name = short_name[:19] + "..."
            ctk.CTkLabel(card, text=short_name, font=ctk.CTkFont(size=11)).pack(
                padx=8, pady=(2, 8)
            )

        if self.current_media:
            self.show_media_detail(self.current_media[0])
        else:
            for widget in self.detail.winfo_children():
                widget.destroy()
            ctk.CTkLabel(self.detail, text="No matching memories.").pack(padx=20, pady=30)

    def show_media_detail(self, media: dict) -> None:
        self.selected_media = media
        for widget in self.detail.winfo_children():
            widget.destroy()

        vacation = get_vacation(media["vacation_id"])
        assert vacation is not None
        source = full_media_path(vacation, media)
        cache_key = f'{media["vacation_id"]}_{media["id"]}_large'
        thumb_path = create_thumbnail(source, cache_key, size=700)

        if thumb_path and thumb_path.exists():
            try:
                pil = Image.open(thumb_path)
                pil.thumbnail((360, 280))
                image = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
                self.thumb_images.append(image)
                ctk.CTkLabel(self.detail, text="", image=image).pack(padx=18, pady=(18, 12))
            except Exception:
                pass

        ctk.CTkLabel(self.detail, text=media["filename"],
                     wraplength=360,
                     font=ctk.CTkFont(size=19, weight="bold")).pack(
            padx=18, pady=(8, 12), anchor="w"
        )

        duration = media["duration_seconds"]
        duration_text = f"{duration:.1f} seconds" if duration else "—"
        dimensions = (
            f'{media["width"]} × {media["height"]}'
            if media["width"] and media["height"] else "—"
        )

        details = (
            f'Type: {media["media_type"]}\n'
            f'Quality score: {media["quality_score"]}\n'
            f'Dimensions: {dimensions}\n'
            f'Duration: {duration_text}\n'
            f'Size: {human_size(media["size_bytes"])}\n'
            f'Created: {media["content_created"] or "Unknown"}\n'
            f'Duplicate: {"Yes" if media["duplicate_of"] else "No"}'
        )
        ctk.CTkLabel(self.detail, text=details, justify="left",
                     font=ctk.CTkFont(family="Menlo", size=13)).pack(
            padx=18, pady=10, anchor="w"
        )

        favorite = get_favorite(media["id"])
        fav_btn = ctk.CTkButton(
            self.detail,
            text="Remove Favorite" if favorite else "Add to Favorites",
            command=lambda: self.toggle_favorite(media["id"], fav_btn),
            height=42
        )
        fav_btn.pack(padx=18, pady=(14, 8), fill="x")

        ctk.CTkButton(
            self.detail,
            text="Open Original",
            command=lambda: subprocess.run(["open", str(source)], check=False),
            height=42
        ).pack(padx=18, pady=8, fill="x")

    def toggle_favorite(self, media_id: int, button: ctk.CTkButton) -> None:
        new_value = not get_favorite(media_id)
        set_favorite(media_id, new_value)
        button.configure(text="Remove Favorite" if new_value else "Add to Favorites")

    def coming_soon(self) -> None:
        messagebox.showinfo("Coming next", "This module is on the MediaLooped roadmap.")


if __name__ == "__main__":
    MediaLoopedStudio().mainloop()
