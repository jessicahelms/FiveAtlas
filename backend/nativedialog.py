"""Fast native file/folder pickers.

Windows uses the Vista+ IFileDialog over COM, macOS uses Cocoa via `osascript`,
and tkinter is the fallback everywhere (Linux, or either of those going wrong).

Why not just tkinter on Windows:
tkinter's `askdirectory()` maps to the legacy SHBrowseForFolder tree dialog, and
`askopenfilename()` to the legacy GetOpenFileName. Both enumerate the shell
namespace up front, which stalls for many seconds when mapped network drives are
present -- the "opening the file dialog takes forever" problem.

This uses the Vista+ Common Item Dialog (IFileDialog), the same picker Explorer
and Office use: it opens immediately, lets you paste a path, and doesn't block on
offline network drives. tkinter stays as the fallback if COM is unavailable
(non-Windows, or an old Python build).

Run as a script so it can be spawned in its own process and never fight uvicorn:

    python nativedialog.py folder [initial_dir]
    python nativedialog.py file   [initial_dir]

It prints the chosen path on the last stdout line, or nothing if cancelled.
"""
from __future__ import annotations

import sys
from pathlib import Path

# ---- COM plumbing (ctypes; no third-party dependency) ------------------------
CLSID_FileOpenDialog = "{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}"
IID_IFileOpenDialog = "{D57C7288-D4AD-4768-BE02-9D969532D960}"
IID_IShellItem = "{43826D1E-E718-42EE-BC55-A1E261C37BFE}"

CLSCTX_INPROC_SERVER = 1
SIGDN_FILESYSPATH = 0x80058000

FOS_PICKFOLDERS = 0x00000020
FOS_FORCEFILESYSTEM = 0x00000040
FOS_PATHMUSTEXIST = 0x00000800
FOS_FILEMUSTEXIST = 0x00001000
FOS_NOCHANGEDIR = 0x00000008

# IFileDialog vtable slots (IUnknown 0-2, IModalWindow 3, then IFileDialog)
_SHOW = 3
_SET_FILE_TYPES = 4
_SET_OPTIONS = 9
_GET_OPTIONS = 10
_SET_FOLDER = 12
_SET_TITLE = 17
_GET_RESULT = 20
_RELEASE = 2
# IShellItem
_SI_GET_DISPLAY_NAME = 5


def _pick_com(kind, initial=None, title=None):
    """Show the modern Common Item Dialog. Returns a path, or '' if cancelled.
    Raises if COM isn't usable, so the caller can fall back."""
    import ctypes
    from ctypes import POINTER, byref, c_void_p, c_wchar_p, c_uint32, c_int

    ole32 = ctypes.OleDLL("ole32")
    shell32 = ctypes.OleDLL("shell32")

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    def guid(s):
        g = GUID()
        ole32.CLSIDFromString(c_wchar_p(s), byref(g))
        return g

    def vcall(ptr, slot, restype, argtypes, *args):
        vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
        proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
        return proto(vtbl[slot])(ptr, *args)

    ole32.CoInitialize(None)
    dialog = c_void_p()
    ole32.CoCreateInstance(byref(guid(CLSID_FileOpenDialog)), None,
                           CLSCTX_INPROC_SERVER, byref(guid(IID_IFileOpenDialog)),
                           byref(dialog))
    try:
        opts = c_uint32()
        vcall(dialog, _GET_OPTIONS, ctypes.HRESULT, [POINTER(c_uint32)], byref(opts))
        flags = opts.value | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST | FOS_NOCHANGEDIR
        flags |= FOS_PICKFOLDERS if kind == "folder" else FOS_FILEMUSTEXIST
        vcall(dialog, _SET_OPTIONS, ctypes.HRESULT, [c_uint32], flags)

        if title:
            vcall(dialog, _SET_TITLE, ctypes.HRESULT, [c_wchar_p], c_wchar_p(title))

        # Opening straight in the last-used folder is most of the speed win: the
        # dialog never has to enumerate This PC (and any dead network drives).
        if initial:
            item = c_void_p()
            try:
                shell32.SHCreateItemFromParsingName(
                    c_wchar_p(str(initial)), None, byref(guid(IID_IShellItem)), byref(item))
                if item:
                    vcall(dialog, _SET_FOLDER, ctypes.HRESULT, [c_void_p], item)
                    vcall(item, _RELEASE, ctypes.c_ulong, [])
            except OSError:
                pass          # bad/offline initial dir -> just open wherever

        try:
            vcall(dialog, _SHOW, ctypes.HRESULT, [c_void_p], None)
        except OSError:
            return ""         # user cancelled (HRESULT_FROM_WIN32(ERROR_CANCELLED))

        result = c_void_p()
        vcall(dialog, _GET_RESULT, ctypes.HRESULT, [POINTER(c_void_p)], byref(result))
        try:
            name = c_wchar_p()
            vcall(result, _SI_GET_DISPLAY_NAME, ctypes.HRESULT,
                  [c_int, POINTER(c_wchar_p)], SIGDN_FILESYSPATH, byref(name))
            path = name.value or ""
            if name:
                ctypes.windll.ole32.CoTaskMemFree(name)
            return path
        finally:
            vcall(result, _RELEASE, ctypes.c_ulong, [])
    finally:
        vcall(dialog, _RELEASE, ctypes.c_ulong, [])


def _as_str(s):
    """Quote a Python string as an AppleScript string literal."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _pick_mac(kind, initial=None, title=None):
    """Native Cocoa picker on macOS, driven through osascript.

    Preferred over tkinter here for two reasons: a Homebrew/pyenv Python often
    ships without Tk at all, and a Tk dialog spawned from a .app subprocess tends
    to open *behind* the frontmost window with no way to raise it. `osascript`
    has neither problem -- `activate` puts the dialog on top, and the binary is
    part of macOS so there is nothing to bundle.
    """
    import subprocess

    verb = "choose folder" if kind == "folder" else "choose file"
    parts = [verb, "with prompt", _as_str(title or "Select")]
    if kind != "folder":
        # UTIs rather than extensions: "public.json" covers .json, and .geojson is
        # not a registered type on every macOS version, so allow plain text too.
        parts += ['of type {"public.json", "public.plain-text", "public.data"}']
    # `default location` needs a real absolute directory; AppleScript errors on a
    # relative one, and "" would resolve to the server's CWD.
    if initial and Path(initial).is_absolute() and Path(initial).is_dir():
        parts += ["default location POSIX file", _as_str(initial)]

    script = "activate\nset _c to (%s)\nreturn POSIX path of _c" % " ".join(parts)
    res = subprocess.run(["osascript", "-e", script],
                         capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        # Cancelling raises "User canceled. (-128)" -- that is a normal outcome,
        # not an error, and must not fall through to the tkinter retry.
        if "-128" in (res.stderr or "") or "canceled" in (res.stderr or "").lower():
            return ""
        raise RuntimeError((res.stderr or "osascript failed").strip())
    # `POSIX path of` puts a trailing slash on folders; the rest of the app is
    # happy either way, but strip it so paths compare equal to what scan.py stores.
    out = (res.stdout or "").strip()
    return out.rstrip("/") if kind == "folder" and out != "/" else out


def _pick_tk(kind, initial=None, title=None):
    """Legacy fallback. Slow with network drives, but always available."""
    import tkinter as tk
    from tkinter import filedialog
    r = tk.Tk()
    r.withdraw()
    r.attributes("-topmost", True)
    kw = {"title": title or ""}
    if initial:
        kw["initialdir"] = str(initial)
    if kind == "folder":
        return filedialog.askdirectory(**kw) or ""
    return filedialog.askopenfilename(
        filetypes=[("GeoJSON", "*.geojson *.json"), ("All files", "*.*")], **kw) or ""


def pick(kind="folder", initial=None, title=None):
    """Pick a folder or a file. Modern dialog where possible, tkinter otherwise."""
    if sys.platform == "win32":
        try:
            return _pick_com(kind, initial, title)
        except Exception as e:                     # COM unavailable/misbehaving
            print(f"[nativedialog] COM picker unavailable ({e}); using tkinter",
                  file=sys.stderr)
    elif sys.platform == "darwin":
        try:
            return _pick_mac(kind, initial, title)
        except Exception as e:                     # osascript missing/refused
            print(f"[nativedialog] osascript picker unavailable ({e}); using tkinter",
                  file=sys.stderr)
    return _pick_tk(kind, initial, title)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "folder"
    start = sys.argv[2] if len(sys.argv) > 2 else None
    heading = ("Select dataset folder" if which == "folder"
               else "Select a GeoJSON region file")
    print(pick(which, start or None, heading))
