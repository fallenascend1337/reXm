# Third-party notices

The released reXm builds include or depend on the following open-source software. Each remains under
its own licence; source code is available from the linked projects.

| Component | Licence | Used for |
|---|---|---|
| [Python](https://www.python.org) | PSF License | Runtime (bundled in the Windows build) |
| [pywebview](https://github.com/r0x0r/pywebview) | BSD-3-Clause | App window |
| [pystray](https://github.com/moses-palmer/pystray) | LGPL-3.0 | Tray icon |
| [Pillow](https://github.com/python-pillow/Pillow) | MIT-CMU (HPND) | Icon drawing |
| [pywinrt / winrt-*](https://github.com/pywinrt/pywinrt) | MIT | Windows Bluetooth (Windows) |
| [pythonnet](https://github.com/pythonnet/pythonnet), clr_loader | MIT | WebView2 bridge (Windows) |
| [bottle](https://github.com/bottlepy/bottle) | MIT | pywebview dependency |
| proxy_tools | BSD | pywebview dependency |
| typing_extensions | PSF License | pywebview dependency |
| [PyGObject](https://gitlab.gnome.org/GNOME/pygobject), GTK 3, WebKitGTK | LGPL-2.1+ | Window on Linux (system libraries) |
| [python-xlib](https://github.com/python-xlib/python-xlib) | LGPL-2.1+ | Tray on Linux (system package) |
| Microsoft Edge WebView2 Runtime | Microsoft | Window rendering on Windows (part of Windows) |

The LGPL components are used unmodified as separate libraries; you may replace them with your own builds
(in the Windows build they live in the app folder's `_internal` directory).

Protocol documentation: [Gadgetbridge](https://codeberg.org/Freeyourgadget/Gadgetbridge) (AGPL-3.0).
reXm contains no Gadgetbridge code; it implements the publicly documented message format independently.
