"""
clipboard_image.py - Windows clipboard image handler for AVA.

Provides:
  - Detection of copied image files in Explorer (CF_HDROP)
  - Detection of copied bitmap screenshots / images (CF_DIB)
  - Detection of copied image paths or raw SVG markup in clipboard text
  - Automatic saving of raw clipboard images to local storage
  - Returns the local absolute file path of the image ready to paste into chat input
"""

from __future__ import annotations

import ctypes
from datetime import datetime
import os
from pathlib import Path
import re
from typing import Optional

IMAGE_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".bmp",
    ".svg",
    ".tiff",
    ".tif",
)

CAPTURED_DIR = Path(__file__).resolve().parent.parent / "data" / "captured_images"


def _get_target_dir() -> Path:
    CAPTURED_DIR.mkdir(parents=True, exist_ok=True)
    return CAPTURED_DIR


def get_clipboard_image_path() -> Optional[str]:
    """
    Check the Windows clipboard for:
      1. Copied image file(s) from File Explorer (CF_HDROP)
      2. Copied image bitmap / screenshot (CF_DIB)
      3. Clipboard text that is an image path / URL / raw SVG markup

    If found, saves bitmap/SVG if needed and returns the absolute local file path.
    If no image is in the clipboard, returns None.
    """
    if os.name != "nt":
        return None

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    shell32 = ctypes.windll.shell32

    CF_DIB = 8
    CF_UNICODETEXT = 13
    CF_HDROP = 15

    if not user32.OpenClipboard(None):
        return None

    try:
        # 1. Check for files copied in Windows Explorer (CF_HDROP)
        if user32.IsClipboardFormatAvailable(CF_HDROP):
            h_drop = user32.GetClipboardData(CF_HDROP)
            if h_drop:
                file_count = shell32.DragQueryFileW(h_drop, 0xFFFFFFFF, None, 0)
                for i in range(file_count):
                    buf = ctypes.create_unicode_buffer(1024)
                    shell32.DragQueryFileW(h_drop, i, buf, 1024)
                    filepath = buf.value
                    if filepath and filepath.lower().endswith(IMAGE_EXTENSIONS):
                        p = Path(filepath)
                        if p.is_file():
                            return str(p.resolve())

        # 2. Check for bitmap / screenshot in clipboard (CF_DIB)
        if user32.IsClipboardFormatAvailable(CF_DIB):
            h_dib = user32.GetClipboardData(CF_DIB)
            if h_dib:
                size = kernel32.GlobalSize(h_dib)
                p_dib = kernel32.GlobalLock(h_dib)
                if p_dib and size > 0:
                    try:
                        raw_bytes = ctypes.string_at(p_dib, size)
                        # DIB header is BITMAPINFOHEADER (at least 40 bytes)
                        # We construct a standard BMP file:
                        # BITMAPFILEHEADER (14 bytes) + DIB payload
                        header_size = int.from_bytes(raw_bytes[0:4], "little")
                        if header_size <= 0:
                            header_size = 40

                        # Calculate offset to pixel bits (header + color table if any)
                        bf_off_bits = 14 + header_size
                        # Check biBitCount (at offset 14 in BITMAPINFOHEADER)
                        if len(raw_bytes) >= 16:
                            bit_count = int.from_bytes(raw_bytes[14:16], "little")
                            clr_used = 0
                            if len(raw_bytes) >= 36:
                                clr_used = int.from_bytes(raw_bytes[32:36], "little")
                            if bit_count <= 8:
                                num_colors = clr_used if clr_used > 0 else (1 << bit_count)
                                bf_off_bits += num_colors * 4
                            elif len(raw_bytes) >= 20:
                                compression = int.from_bytes(raw_bytes[16:20], "little")
                                if compression == 3:  # BI_BITFIELDS
                                    bf_off_bits += 12

                        bf_size = 14 + size
                        bmp_header = (
                            b"BM"
                            + bf_size.to_bytes(4, "little")
                            + b"\x00\x00\x00\x00"
                            + bf_off_bits.to_bytes(4, "little")
                        )
                        bmp_bytes = bmp_header + raw_bytes

                        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                        save_path = _get_target_dir() / f"clipboard_{stamp}.bmp"
                        save_path.write_bytes(bmp_bytes)

                        # Try to convert to PNG using cv2 or keep bmp
                        final_path = str(save_path.resolve())
                        try:
                            import cv2

                            img = cv2.imread(final_path)
                            if img is not None:
                                png_path = _get_target_dir() / f"clipboard_{stamp}.png"
                                cv2.imwrite(str(png_path), img)
                                save_path.unlink(missing_ok=True)
                                final_path = str(png_path.resolve())
                        except Exception:
                            pass

                        return final_path
                    finally:
                        kernel32.GlobalUnlock(h_dib)

        # 3. Check for text that is an image file path or raw SVG markup
        if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            h_text = user32.GetClipboardData(CF_UNICODETEXT)
            if h_text:
                p_text = kernel32.GlobalLock(h_text)
                if p_text:
                    try:
                        text = ctypes.wstring_at(p_text).strip()
                        # Check raw SVG
                        if text.startswith("<svg") and text.endswith("</svg>"):
                            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                            svg_path = _get_target_dir() / f"clipboard_{stamp}.svg"
                            svg_path.write_text(text, encoding="utf-8")
                            return str(svg_path.resolve())

                        # Check if text is a quoted or unquoted file path
                        clean_text = text.strip('"').strip("'")
                        if clean_text.lower().endswith(IMAGE_EXTENSIONS):
                            p = Path(os.path.expanduser(os.path.expandvars(clean_text)))
                            if p.is_file():
                                return str(p.resolve())
                    finally:
                        kernel32.GlobalUnlock(h_text)

        return None
    except Exception:
        return None
    finally:
        user32.CloseClipboard()


def get_clipboard_files_or_images() -> list[str]:
    """
    Check the Windows clipboard for:
      1. Copied file(s) from File Explorer (CF_HDROP) - images, documents, code, etc.
      2. Copied bitmap / screenshot (CF_DIB)
      3. Clipboard text that is an existing file path or raw SVG markup
    Returns a list of local file paths.
    """
    if os.name != "nt":
        return []

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    shell32 = ctypes.windll.shell32

    CF_DIB = 8
    CF_UNICODETEXT = 13
    CF_HDROP = 15

    if not user32.OpenClipboard(None):
        return []

    results = []
    try:
        # 1. Check for files copied in Windows Explorer (CF_HDROP)
        if user32.IsClipboardFormatAvailable(CF_HDROP):
            h_drop = user32.GetClipboardData(CF_HDROP)
            if h_drop:
                file_count = shell32.DragQueryFileW(h_drop, 0xFFFFFFFF, None, 0)
                for i in range(file_count):
                    buf = ctypes.create_unicode_buffer(1024)
                    shell32.DragQueryFileW(h_drop, i, buf, 1024)
                    filepath = buf.value
                    if filepath:
                        p = Path(filepath)
                        if p.is_file():
                            results.append(str(p.resolve()))
                if results:
                    return results

        # 2. Check for bitmap / screenshot in clipboard (CF_DIB)
        if user32.IsClipboardFormatAvailable(CF_DIB):
            h_dib = user32.GetClipboardData(CF_DIB)
            if h_dib:
                size = kernel32.GlobalSize(h_dib)
                p_dib = kernel32.GlobalLock(h_dib)
                if p_dib and size > 0:
                    try:
                        raw_bytes = ctypes.string_at(p_dib, size)
                        header_size = int.from_bytes(raw_bytes[0:4], "little")
                        if header_size <= 0:
                            header_size = 40
                        bf_off_bits = 14 + header_size
                        if len(raw_bytes) >= 16:
                            bit_count = int.from_bytes(raw_bytes[14:16], "little")
                            clr_used = 0
                            if len(raw_bytes) >= 36:
                                clr_used = int.from_bytes(raw_bytes[32:36], "little")
                            if bit_count <= 8:
                                num_colors = clr_used if clr_used > 0 else (1 << bit_count)
                                bf_off_bits += num_colors * 4
                            elif len(raw_bytes) >= 20:
                                compression = int.from_bytes(raw_bytes[16:20], "little")
                                if compression == 3:
                                    bf_off_bits += 12

                        bf_size = 14 + size
                        bmp_header = (
                            b"BM"
                            + bf_size.to_bytes(4, "little")
                            + b"\x00\x00\x00\x00"
                            + bf_off_bits.to_bytes(4, "little")
                        )
                        bmp_bytes = bmp_header + raw_bytes

                        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                        save_path = _get_target_dir() / f"clipboard_{stamp}.bmp"
                        save_path.write_bytes(bmp_bytes)

                        final_path = str(save_path.resolve())
                        try:
                            import cv2

                            img = cv2.imread(final_path)
                            if img is not None:
                                png_path = _get_target_dir() / f"clipboard_{stamp}.png"
                                cv2.imwrite(str(png_path), img)
                                save_path.unlink(missing_ok=True)
                                final_path = str(png_path.resolve())
                        except Exception:
                            pass

                        return [final_path]
                    finally:
                        kernel32.GlobalUnlock(h_dib)

        # 3. Check for text that is a file path or raw SVG markup
        if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            h_text = user32.GetClipboardData(CF_UNICODETEXT)
            if h_text:
                p_text = kernel32.GlobalLock(h_text)
                if p_text:
                    try:
                        text = ctypes.wstring_at(p_text).strip()
                        if text.startswith("<svg") and text.endswith("</svg>"):
                            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                            svg_path = _get_target_dir() / f"clipboard_{stamp}.svg"
                            svg_path.write_text(text, encoding="utf-8")
                            return [str(svg_path.resolve())]

                        clean_text = text.strip('"').strip("'")
                        p = Path(os.path.expanduser(os.path.expandvars(clean_text)))
                        if p.is_file():
                            return [str(p.resolve())]
                    finally:
                        kernel32.GlobalUnlock(h_text)

        return []
    except Exception:
        return []
    finally:
        user32.CloseClipboard()


def upload_clipboard_files_to_server() -> list[dict[str, Any]]:
    """
    Extract files or screenshots from the clipboard, upload them to the AVA server,
    and return the list of server upload records (including 'server_path', 'filename', 'url', etc.).
    If upload fails, returns a fallback dict with local path.
    """
    paths = get_clipboard_files_or_images()
    if not paths:
        return []

    try:
        from core.server_api import upload_file_remote
    except Exception:
        upload_file_remote = None

    uploaded_records = []
    for lp in paths:
        record = None
        if upload_file_remote:
            try:
                record = upload_file_remote(lp)
            except Exception:
                record = None
        if record and isinstance(record, dict) and record.get("server_path"):
            uploaded_records.append(record)
        else:
            # Fallback to local path info
            p = Path(lp)
            uploaded_records.append({
                "filename": p.name,
                "server_path": str(p.resolve()),
                "url": "",
                "is_fallback": True
            })
    return uploaded_records
