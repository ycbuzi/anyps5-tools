r"""Local SDL3 patches for the Quest OS app (NativeActivity, no SDL Java activity). Idempotent: python -I sdl-patches.py <SDL3 source dir>

1. Android_WaitLifecycleEvent returns at once when SDL's Java activity never created its semaphore; without it the
   renderer spins forever.
2. The offscreen video driver falls back to eglGetDisplay(EGL_DEFAULT_DISPLAY) when EGL device enumeration is missing
   (Android), so the console can draw with OpenGL ES instead of the slow software renderer.
"""
import io
import os
import sys

PATCHES = [
    ("src/core/android/SDL_android.c",
     "    bool got_event = false;\n",
     "    bool got_event = false;\n    if (!Android_LifecycleEventSem) return false;\n"),
    ("src/video/SDL_egl.c",
     "    // Check for all extensions that are optional until used and fail if any is missing\n"
     "    if (!_this->egl_data->eglQueryDevicesEXT) {\n",
     "    if (!_this->egl_data->eglQueryDevicesEXT && _this->egl_data->eglGetDisplay) {\n"
     "        _this->egl_data->egl_display = _this->egl_data->eglGetDisplay(EGL_DEFAULT_DISPLAY);\n"
     "        if (_this->egl_data->egl_display == EGL_NO_DISPLAY || _this->egl_data->eglInitialize(_this->egl_data->egl_display, NULL, NULL) != EGL_TRUE) {\n"
     "            return SDL_SetError(\"Could not initialize the default EGL display\");\n"
     "        }\n"
     "        SDL_EGL_GetVersion(_this);\n"
     "        _this->egl_data->is_offscreen = true;\n"
     "        return true;\n"
     "    }\n"
     "    // Check for all extensions that are optional until used and fail if any is missing\n"
     "    if (!_this->egl_data->eglQueryDevicesEXT) {\n"),
]

CRLF = "\r\n"
LF = "\n"


def main(root):
    for rel, old, new in PATCHES:
        path = os.path.join(root, rel)
        s = io.open(path, encoding="utf-8", newline="").read()
        nl = CRLF if CRLF in s else LF
        s = s.replace(CRLF, LF)
        if new in s:
            continue
        if s.count(old) != 1:
            sys.exit("patch does not apply: %s" % rel)
        io.open(path, "w", encoding="utf-8", newline="").write(s.replace(old, new).replace(LF, nl))
        print("patched", rel)


if __name__ == "__main__":
    main(sys.argv[1])
