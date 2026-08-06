#!/usr/bin/env python3
from __future__ import annotations

import csv
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".gif", ".bmp", ".webp", ".dng", ".raw"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".wmv", ".webm", ".mts", ".m2ts", ".3gp"}

def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{num_bytes} B"

def scan_folder(folder: Path, progress_callback=None):
    files = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in names:
            if not name.startswith("."):
                files.append(Path(root) / name)

    rows = []
    photos = videos = others = total_bytes = 0

    for index, path in enumerate(files, start=1):
        try:
            size = path.stat().st_size
        except OSError:
            size = 0

        ext = path.suffix.lower()
        if ext in PHOTO_EXTS:
            media_type = "Photo"
            photos += 1
        elif ext in VIDEO_EXTS:
            media_type = "Video"
            videos += 1
        else:
            media_type = "Other"
            others += 1

        total_bytes += size
        rows.append({
            "type": media_type,
            "filename": path.name,
            "extension": ext,
            "size_bytes": str(size),
            "size_readable": human_size(size),
            "relative_path": str(path.relative_to(folder)),
        })

        if progress_callback and files:
            progress_callback(index, len(files))

    return {
        "folder": folder,
        "total_files": len(files),
        "photos": photos,
        "videos": videos,
        "others": others,
        "total_bytes": total_bytes,
        "rows": rows,
    }

def write_reports(result):
    folder = result["folder"]
    csv_path = folder / "MediaLooped_Inventory.csv"
    txt_path = folder / "MediaLooped_Summary.txt"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["type", "filename", "extension", "size_bytes", "size_readable", "relative_path"])
        writer.writeheader()
        writer.writerows(result["rows"])

    txt_path.write_text(
        "MediaLooped Analyzer v0.1\n"
        "==========================\n\n"
        f"Folder: {folder}\n"
        f"Total files: {result['total_files']}\n"
        f"Photos: {result['photos']}\n"
        f"Videos: {result['videos']}\n"
        f"Other files: {result['others']}\n"
        f"Total size: {human_size(result['total_bytes'])}\n",
        encoding="utf-8",
    )
    return csv_path, txt_path

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MediaLooped Analyzer v0.1")
        self.geometry("640x420")
        self.selected_folder = None

        ttk.Label(self, text="MediaLooped Analyzer", font=("Helvetica", 24, "bold")).pack(pady=(24, 6))
        ttk.Label(self, text="Choose a vacation folder, then click Analyze.").pack(pady=(0, 18))

        self.folder_var = tk.StringVar(value="No folder selected")
        ttk.Label(self, textvariable=self.folder_var, wraplength=560, justify="center").pack(pady=8)

        ttk.Button(self, text="Choose Vacation Folder", command=self.choose_folder).pack(pady=6)

        self.analyze_btn = ttk.Button(self, text="Analyze", command=self.start_scan, state="disabled")
        self.analyze_btn.pack(pady=6)

        self.progress = ttk.Progressbar(self, length=500, mode="determinate")
        self.progress.pack(pady=(18, 8))

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.status_var).pack()

        self.results = tk.Text(self, height=9, width=66, state="disabled", font=("Menlo", 12))
        self.results.pack(padx=20, pady=14, fill="both", expand=True)

    def choose_folder(self):
        chosen = filedialog.askdirectory(title="Choose your vacation folder")
        if chosen:
            self.selected_folder = Path(chosen)
            self.folder_var.set(str(self.selected_folder))
            self.analyze_btn.config(state="normal")
            self.status_var.set("Folder selected.")

    def update_progress(self, current, total):
        self.after(0, self._update_progress_ui, current, total)

    def _update_progress_ui(self, current, total):
        self.progress["value"] = int((current / total) * 100) if total else 0
        self.status_var.set(f"Scanning {current} of {total} files...")

    def start_scan(self):
        if not self.selected_folder:
            return
        self.analyze_btn.config(state="disabled")
        self.progress["value"] = 0
        threading.Thread(target=self.run_scan, daemon=True).start()

    def run_scan(self):
        try:
            result = scan_folder(self.selected_folder, self.update_progress)
            csv_path, txt_path = write_reports(result)
            self.after(0, self.show_result, result, csv_path, txt_path)
        except Exception as exc:
            self.after(0, self.show_error, str(exc))

    def show_result(self, result, csv_path, txt_path):
        output = (
            f"Total files : {result['total_files']}\n"
            f"Photos      : {result['photos']}\n"
            f"Videos      : {result['videos']}\n"
            f"Other files : {result['others']}\n"
            f"Total size  : {human_size(result['total_bytes'])}\n\n"
            f"Created:\n- {csv_path.name}\n- {txt_path.name}\n"
        )
        self.results.config(state="normal")
        self.results.delete("1.0", "end")
        self.results.insert("1.0", output)
        self.results.config(state="disabled")
        self.progress["value"] = 100
        self.status_var.set("Analysis complete.")
        self.analyze_btn.config(state="normal")
        messagebox.showinfo("MediaLooped", "Analysis complete.")

    def show_error(self, message):
        self.analyze_btn.config(state="normal")
        self.status_var.set("Error")
        messagebox.showerror("MediaLooped Error", message)

if __name__ == "__main__":
    App().mainloop()
