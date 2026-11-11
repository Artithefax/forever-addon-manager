"""Schlüssel und Tokens verschlüsselt speichern.

Unter Windows mit der Datenschutz-API (DPAPI): Nur dasselbe Windows-Konto auf demselben PC kann die Werte
wieder lesen. Auf einem anderen PC müssen sie neu eingegeben werden – Leeroyzan meldet das.
Ohne DPAPI (Linux, macOS) bleiben die Werte wie bisher im Klartext.
"""
from __future__ import annotations

import base64
import os

_ENTROPY = b"Leeroyzan secrets v1"


def _dpapi():
    """Die beiden DPAPI-Funktionen (schützen, entschützen) oder None, wenn es sie nicht gibt."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    args = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt32.CryptProtectData.argtypes = args
    crypt32.CryptProtectData.restype = wintypes.BOOL
    unargs = list(args)
    unargs[1] = ctypes.POINTER(wintypes.LPWSTR)
    crypt32.CryptUnprotectData.argtypes = unargs
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    ui_forbidden = 0x01

    def blob(data: bytes):
        buf = ctypes.create_string_buffer(data, len(data))
        return Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def run(fn, data: bytes, desc) -> bytes:
        inp, keep1 = blob(data)
        ent, keep2 = blob(_ENTROPY)
        out = Blob()
        if not fn(ctypes.byref(inp), desc, ctypes.byref(ent), None, None, ui_forbidden, ctypes.byref(out)):
            raise OSError(ctypes.get_last_error(), "DPAPI")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))

    return (lambda d: run(crypt32.CryptProtectData, d, "Leeroyzan"),
            lambda d: run(crypt32.CryptUnprotectData, d, None))


_FUNCS: list = []


def _funcs():
    if not _FUNCS:
        try:
            _FUNCS.append(_dpapi())
        except Exception:  # noqa – DLL fehlt: dann Klartext wie früher
            _FUNCS.append(None)
    return _FUNCS[0]


def available() -> bool:
    return _funcs() is not None


def seal(value: str) -> str | dict:
    """Verschlüsselt einen Wert für die Einstellungsdatei (ohne DPAPI: unverändert)."""
    f = _funcs()
    if not f or not value:
        return value
    try:
        return {"dpapi": base64.b64encode(f[0](value.encode("utf-8"))).decode("ascii")}
    except OSError:
        return value


def unseal(value) -> str:
    """Gegenstück zu seal(). Wirft eine Ausnahme, wenn der Wert hier nicht lesbar ist."""
    if not isinstance(value, dict):
        return str(value or "")
    f = _funcs()
    if not f:
        raise OSError("DPAPI ist auf diesem System nicht verfügbar")
    return f[1](base64.b64decode(value["dpapi"])).decode("utf-8")
