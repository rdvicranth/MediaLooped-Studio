#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import html
import os
import plistlib
import subprocess
import threading
import tkinter as tk
from datetime import datetime
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

def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def mdls_metadata(path: Path) -> dict:
    try:
        proc = subprocess.run(
            ["mdls", "-plist", "-", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout:
            return {}
        return plistlib.loads(proc.stdout)
    except Exception:
        return {}

def quick_hash(path: Path, block_size=1024 * 1024) -> str:
    h = hashlib.sha256()
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size <= block_size * 3:
                while True:
                    chunk = f.read(block_size)
                    if not chunk:
                        break
                    h.update(chunk)
            else:
                h.update(f.read(block_size))
                f.seek(max(size // 2 - block_size // 2, 0))
                h.update(f.read(block_size))
                f.seek(max(size - block_size, 0))
                h.update(f.read(block_size))
        h.update(str(size).encode("utf-8"))
        return h.hexdigest()
    except Exception:
        return ""

def quality_score(media_type: str, size: int, width, height, duration) -> int:
    score = 0
    megapixels = None
    if width and height:
        megapixels = (width * height) / 1_000_000

    if media_type == "Photo":
        if megapixels is not None:
            score += min(55, int(megapixels * 4))
        score += min(35, int(size / (1024 * 1024) * 5))
        if width and height and max(width, height) >= 3000:
            score += 10
    elif media_type == "Video":
        if width and height:
            if max(width, height) >= 3840:
                score += 45
            elif max(width, height) >= 1920:
                score += 35
            elif max(width, height) >= 1280:
                score += 22
        if duration:
            if 4 <= duration <= 45:
                score += 35
            elif 2 <= duration <= 90:
                score += 25
            else:
                score += 12
        score += min(20, int(size / (20 * 1024 * 1024)))
    else:
        score = 0

    return max(0, min(100, score))

def scan_folder(folder: Path, progress_callback=None):
    files = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in names:
            if name.startswith(".") or name.startswith("MediaLooped_"):
                continue
            files.append(Path(root) / name)

    rows = []
    hashes = {}
    photos = videos = others = total_bytes = 0

    for index, path in enumerate(files, start=1):
        try:
            stat = path.stat()
            size = stat.st_size
            modified = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
        except OSError:
            size = 0
            modified = ""

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

        metadata = mdls_metadata(path)
        width = safe_int(metadata.get("kMDItemPixelWidth"))
        height = safe_int(metadata.get("kMDItemPixelHeight"))
        duration = safe_float(metadata.get("kMDItemDurationSeconds"))
        content_created = metadata.get("kMDItemContentCreationDate")
        if hasattr(content_created, "isoformat"):
            content_created = content_created.isoformat(timespec="seconds")
        elif content_created is None:
            content_created = ""

        digest = quick_hash(path) if media_type in {"Photo", "Video"} else ""
        duplicate_of = ""
        if digest:
            if digest in hashes:
                duplicate_of = hashes[digest]
            else:
                hashes[digest] = str(path.relative_to(folder))

        score = quality_score(media_type, size, width, height, duration)
        total_bytes += size

        rows.append({
            "type": media_type,
            "score": score,
            "filename": path.name,
            "extension": ext,
            "size_bytes": size,
            "size_readable": human_size(size),
            "width": width or "",
            "height": height or "",
            "duration_seconds": f"{duration:.2f}" if duration is not None else "",
            "content_created": content_created,
            "modified": modified,
            "duplicate_of": duplicate_of,
            "relative_path": str(path.relative_to(folder)),
        })

        if progress_callback and files:
            progress_callback(index, len(files))

    rows.sort(key=lambda r: (r["type"] == "Other", -int(r["score"]), r["filename"].lower()))
    return {
        "folder": folder,
        "total_files": len(files),
        "photos": photos,
        "videos": videos,
        "others": others,
        "duplicates": sum(1 for r in rows if r["duplicate_of"]),
        "total_bytes": total_bytes,
        "rows": rows,
    }

def write_reports(result):
    folder = result["folder"]
    csv_path = folder / "MediaLooped_Inventory_v0_2.csv"
    txt_path = folder / "MediaLooped_Summary_v0_2.txt"
    html_path = folder / "MediaLooped_Report_v0_2.html"

    fieldnames = [
        "type", "score", "filename", "extension", "size_bytes", "size_readable",
        "width", "height", "duration_seconds", "content_created", "modified",
        "duplicate_of", "relative_path",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(result["rows"])

    txt_path.write_text(
        "MediaLooped Analyzer v0.2\n"
        "==========================\n\n"
        f"Folder: {folder}\n"
        f"Total files: {result['total_files']}\n"
        f"Photos: {result['photos']}\n"
        f"Videos: {result['videos']}\n"
        f"Other files: {result['others']}\n"
        f"Possible exact duplicates: {result['duplicates']}\n"
        f"Total size: {human_size(result['total_bytes'])}\n\n"
        "Note: The score is a preliminary technical-quality score based on\n"
        "resolution, duration and file size. It does not yet judge emotion,\n"
        "composition, lakes, mountains, faces or story value.\n",
        encoding="utf-8",
    )

    media_rows = [r for r in result["rows"] if r["type"] in {"Photo", "Video"}]
    top_rows = media_rows[:50]
    table_rows = []
    for r in top_rows:
        dims = f"{r['width']}×{r['height']}" if r["width"] and r["height"] else ""
        duration = f"{r['duration_seconds']} sec" if r["duration_seconds"] else ""
        duplicate = html.escape(str(r["duplicate_of"])) if r["duplicate_of"] else ""
        table_rows.append(
            "<tr>"
            f"<td>{r['score']}</td>"
            f"<td>{html.escape(str(r['type']))}</td>"
            f"<td>{html.escape(str(r['filename']))}</td>"
            f"<td>{html.escape(dims)}</td>"
            f"<td>{html.escape(duration)}</td>"
            f"<td>{html.escape(str(r['size_readable']))}</td>"
            f"<td>{duplicate}</td>"
            "</tr>"
        )

    html_path.write_text(
        """<!doctype html>
<html><head><meta charset="utf-8"><title>MediaLooped Report</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;margin:32px;color:#222}
h1{margin-bottom:4px}.muted{color:#666}.cards{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0}
.card{border:1px solid #ddd;border-radius:12px;padding:16px;min-width:135px}
.card b{font-size:24px;display:block}table{border-collapse:collapse;width:100%;margin-top:18px}
th,td{border-bottom:1px solid #ddd;text-align:left;padding:9px}th{background:#f5f5f5}
.note{background:#fff8dc;padding:14px;border-radius:10px;margin-top:18px}
</style></head><body>
<h1>MediaLooped Analyzer v0.2</h1>
<div class="muted">Preliminary technical inventory</div>
<div class="cards">
"""
        + f'<div class="card"><b>{result["total_files"]}</b>Total files</div>'
        + f'<div class="card"><b>{result["photos"]}</b>Photos</div>'
        + f'<div class="card"><b>{result["videos"]}</b>Videos</div>'
        + f'<div class="card"><b>{result["duplicates"]}</b>Duplicates</div>'
        + f'<div class="card"><b>{human_size(result["total_bytes"])}</b>Total size</div>'
        + """</div>
<div class="note"><b>Important:</b> Scores currently measure technical quality only.
They do not yet understand scenery, family emotion or storytelling.</div>
<h2>Top 50 technical candidates</h2>
<table><thead><tr><th>Score</th><th>Type</th><th>File</th><th>Dimensions</th><th>Duration</th><th>Size</th><th>Duplicate of</th></tr></thead>
<tbody>"""
        + "".join(table_rows)
        + """</tbody></table></body></html>""",
        encoding="utf-8",
    )
    return csv_path, txt_path, html_path

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MediaLooped Analyzer v0.2")
        self.geometry("680x460")
        self.selected_folder = None

        ttk.Label(self, text="MediaLooped Analyzer", font=("Helvetica", 24, "bold")).pack(pady=(24, 6))
        ttk.Label(self, text="Technical scoring, metadata and duplicate detection").pack(pady=(0, 18))

        self.folder_var = tk.StringVar(value="No folder selected")
        ttk.Label(self, textvariable=self.folder_var, wraplength=600, justify="center").pack(pady=8)
        ttk.Button(self, text="Choose Vacation Folder", command=self.choose_folder).pack(pady=6)

        self.analyze_btn = ttk.Button(self, text="Analyze", command=self.start_scan, state="disabled")
        self.analyze_btn.pack(pady=6)

        self.progress = ttk.Progressbar(self, length=540, mode="determinate")
        self.progress.pack(pady=(18, 8))

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.status_var).pack()

        self.results = tk.Text(self, height=11, width=72, state="disabled", font=("Menlo", 12))
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
        self.status_var.set(f"Analyzing {current} of {total} files...")

    def start_scan(self):
        if not self.selected_folder:
            return
        self.analyze_btn.config(state="disabled")
        self.progress["value"] = 0
        threading.Thread(target=self.run_scan, daemon=True).start()

    def run_scan(self):
        try:
            result = scan_folder(self.selected_folder, self.update_progress)
            paths = write_reports(result)
            self.after(0, self.show_result, result, paths)
        except Exception as exc:
            self.after(0, self.show_error, str(exc))

    def show_result(self, result, paths):
        output = (
            f"Total files : {result['total_files']}\n"
            f"Photos      : {result['photos']}\n"
            f"Videos      : {result['videos']}\n"
            f"Duplicates  : {result['duplicates']}\n"
            f"Total size  : {human_size(result['total_bytes'])}\n\n"
            "Created:\n"
            + "\n".join(f"- {p.name}" for p in paths)
        )
        self.results.config(state="normal")
        self.results.delete("1.0", "end")
        self.results.insert("1.0", output)
        self.results.config(state="disabled")
        self.progress["value"] = 100
        self.status_var.set("Analysis complete.")
        self.analyze_btn.config(state="normal")
        messagebox.showinfo("MediaLooped", "v0.2 analysis complete.")

    def show_error(self, message):
        self.analyze_btn.config(state="normal")
        self.status_var.set("Error")
        messagebox.showerror("MediaLooped Error", message)

if __name__ == "__main__":
    App().mainloop()
