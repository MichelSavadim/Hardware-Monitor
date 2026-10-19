"""Present Qt's rendered pixels on a transparent Win32 desktop child window."""

import ctypes
from ctypes import wintypes

from PySide6.QtGui import QImage, QPixmap


class BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD), ("width", wintypes.LONG), ("height", wintypes.LONG),
        ("planes", wintypes.WORD), ("bit_count", wintypes.WORD),
        ("compression", wintypes.DWORD), ("image_size", wintypes.DWORD),
        ("x_pixels_per_meter", wintypes.LONG), ("y_pixels_per_meter", wintypes.LONG),
        ("colors_used", wintypes.DWORD), ("colors_important", wintypes.DWORD),
    ]


class BitmapInfo(ctypes.Structure):
    _fields_ = [("header", BitmapInfoHeader), ("colors", wintypes.DWORD * 1)]


class BlendFunction(ctypes.Structure):
    _fields_ = [("operation", ctypes.c_ubyte), ("flags", ctypes.c_ubyte),
                ("constant_alpha", ctypes.c_ubyte), ("alpha_format", ctypes.c_ubyte)]


user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
user32.GetDC.argtypes = (wintypes.HWND,)
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = (wintypes.HDC, ctypes.POINTER(BitmapInfo), wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HANDLE)
gdi32.SelectObject.restype = wintypes.HANDLE
gdi32.DeleteObject.argtypes = (wintypes.HANDLE,)
gdi32.DeleteDC.argtypes = (wintypes.HDC,)
user32.UpdateLayeredWindow.argtypes = (
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE),
    wintypes.HDC, ctypes.POINTER(wintypes.POINT), wintypes.DWORD,
    ctypes.POINTER(BlendFunction), wintypes.DWORD,
)
user32.UpdateLayeredWindow.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
user32.GetWindowLongW.restype = ctypes.c_int
user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_int)
user32.SetWindowLongW.restype = ctypes.c_int


def present_layered(hwnd: int, pixmap: QPixmap) -> tuple[bool, int]:
    """Copy a Qt snapshot into the HWND's per-pixel alpha surface."""
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    width, height = image.width(), image.height()
    if width <= 0 or height <= 0:
        return False, 0
    info = BitmapInfo()
    info.header.size = ctypes.sizeof(BitmapInfoHeader)
    info.header.width = width
    info.header.height = -height  # Top-down DIB, matching QImage row order.
    info.header.planes = 1
    info.header.bit_count = 32
    info.header.compression = 0
    bits = ctypes.c_void_p()
    screen_dc = user32.GetDC(0)
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = None
    original = None
    try:
        bitmap = gdi32.CreateDIBSection(screen_dc, ctypes.byref(info), 0,
                                        ctypes.byref(bits), 0, 0)
        if not bitmap or not bits.value:
            return False, ctypes.get_last_error()
        original = gdi32.SelectObject(memory_dc, bitmap)
        pixels = bytes(image.bits())
        ctypes.memmove(bits, pixels, min(len(pixels), width * height * 4))
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        destination = wintypes.POINT(rect.left, rect.top)
        size = wintypes.SIZE(width, height)
        source = wintypes.POINT(0, 0)
        blend = BlendFunction(0, 0, 255, 1)
        exstyle = user32.GetWindowLongW(hwnd, -20)
        if not (exstyle & 0x00080000):
            user32.SetWindowLongW(hwnd, -20, exstyle | 0x00080000)
        success = user32.UpdateLayeredWindow(hwnd, screen_dc, ctypes.byref(destination),
                                             ctypes.byref(size), memory_dc, ctypes.byref(source),
                                             0, ctypes.byref(blend), 0x00000002)
        return bool(success), 0 if success else ctypes.get_last_error()
    finally:
        if original:
            gdi32.SelectObject(memory_dc, original)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        if screen_dc:
            user32.ReleaseDC(0, screen_dc)
