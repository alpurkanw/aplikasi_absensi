import ctypes
import ctypes.wintypes as wintypes
import time
from pathlib import Path


SDK_DLL = "DPFPApi.dll"
OUTPUT_FILE = Path(__file__).with_name("fingerprint_sample.bin")
DP_SAMPLE_TYPE_IMAGE = 4
DP_SAMPLE_TYPE_RAW = 0  # Alternative: raw sensor data
DP_PRIORITY_NORMAL = 2
DP_PRIORITY_HIGH = 0    # Alternative: high priority
WN_COMPLETED = 0
WN_ERROR = 1
WN_DISCONNECT = 2
WN_RECONNECT = 3
WN_FINGER_TOUCHED = 5
WN_FINGER_GONE = 6
WM_APP = 0x8000
WM_FP_NOTIFY = WM_APP + 1
HWND_MESSAGE = wintypes.HWND(-3)
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
SW_SHOW = 5


class DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.c_void_p),
    ]


class Guid(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class WndClass(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HCURSOR),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dpfp = ctypes.WinDLL(SDK_DLL)

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)

user32.DefWindowProcW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.RegisterClassW.argtypes = [ctypes.POINTER(WndClass)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    ctypes.c_void_p,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UpdateWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

# DPFPApi uses HRESULT for API functions.
dpfp.DPFPInit.restype = ctypes.c_long
dpfp.DPFPTerm.restype = None
dpfp.DPFPCreateAcquisition.argtypes = [
    ctypes.c_int,
    ctypes.POINTER(Guid),
    wintypes.ULONG,
    wintypes.HWND,
    wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG),
]
dpfp.DPFPCreateAcquisition.restype = ctypes.c_long
dpfp.DPFPStartAcquisition.argtypes = [wintypes.ULONG]
dpfp.DPFPStartAcquisition.restype = ctypes.c_long
dpfp.DPFPStopAcquisition.argtypes = [wintypes.ULONG]
dpfp.DPFPStopAcquisition.restype = ctypes.c_long
dpfp.DPFPDestroyAcquisition.argtypes = [wintypes.ULONG]
dpfp.DPFPDestroyAcquisition.restype = ctypes.c_long


class FingerprintCaptureAgent:
    def __init__(self, output_file=OUTPUT_FILE, on_event=None, on_sample=None, sample_type=DP_SAMPLE_TYPE_IMAGE):
        self.output_file = Path(output_file)
        self.on_event = on_event
        self.on_sample = on_sample
        self.sample_type = sample_type
        self.operation = wintypes.ULONG()
        self.window = None
        self.thread_id = None
        self.class_name = f"HCISFingerprintCapture_{id(self)}"
        self.wnd_proc = WNDPROC(self._window_proc)
        self._last_touch_event = None
        self._last_touch_ts = 0.0
        self._finger_down_at = 0.0
        self._capture_started_at = 0.0
        self._capture_timeout_seconds = 30  # 30 seconds for sensor to process
        self._min_hold_time = 0.5  # finger must stay down for at least 0.5s to capture
        self._touch_count = 0
        self._remove_count = 0

    def _window_proc(self, hwnd, message, wparam, lparam):
        if message == WM_FP_NOTIFY:
            self._handle_notification(wparam, lparam)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _emit(self, message):
        print(message)
        if self.on_event:
            self.on_event(message)

    def _handle_notification(self, event, value):
        if event == WN_COMPLETED:
            blob = ctypes.cast(value, ctypes.POINTER(DataBlob)).contents
            sample = ctypes.string_at(blob.pbData, blob.cbData)
            if self.on_sample:
                self.on_sample(sample)
            self.output_file.write_bytes(sample)
            self._emit(f"[OK] Fingerprint captured: {len(sample)} bytes")
            self._emit(f"[OK] Saved: {self.output_file}")
            self._emit("[INFO] Ready for the next finger...")
            self._last_touch_event = None
            self._last_touch_ts = 0.0
            self._finger_down_at = 0.0
            self._capture_started_at = 0.0
        elif event == WN_ERROR:
            self._emit(f"[ERROR] SDK capture error: 0x{value & 0xFFFFFFFF:08X}")
            self._stop_and_close()
            user32.PostQuitMessage(1)
        elif event in (WN_FINGER_TOUCHED, WN_FINGER_GONE):
            current_ts = time.time()
            if self._last_touch_event == event and (current_ts - self._last_touch_ts) < 0.8:
                return
            self._last_touch_event = event
            self._last_touch_ts = current_ts
            if event == WN_FINGER_TOUCHED:
                self._touch_count += 1
                self._finger_down_at = current_ts
                self._emit(f"[INFO] Finger touched (count: {self._touch_count}) - tahan stabil di sensor")
            else:
                self._remove_count += 1
                if self._finger_down_at > 0:
                    hold_time = current_ts - self._finger_down_at
                    self._emit(f"[INFO] Finger removed (count: {self._remove_count}) - hold time: {hold_time:.1f}s")
                self._emit(f"[INFO] Finger removed (count: {self._remove_count})")
        elif event == WN_DISCONNECT:
            self._emit("[ERROR] Fingerprint reader disconnected")
        elif event == WN_RECONNECT:
            self._emit("[INFO] Fingerprint reader connected")

    def stop(self):
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)

    def _stop_and_close(self):
        if self.operation.value:
            dpfp.DPFPStopAcquisition(self.operation)
            dpfp.DPFPDestroyAcquisition(self.operation)
            self.operation.value = 0

    def run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        self._capture_started_at = time.time()
        result = dpfp.DPFPInit()
        if result not in (0, 1):
            raise RuntimeError(f"DPFPInit failed: 0x{result & 0xFFFFFFFF:08X}")

        try:
            wnd_class = WndClass()
            wnd_class.lpfnWndProc = ctypes.cast(self.wnd_proc, ctypes.c_void_p).value
            wnd_class.hInstance = kernel32.GetModuleHandleW(None)
            wnd_class.lpszClassName = self.class_name
            if not user32.RegisterClassW(ctypes.byref(wnd_class)):
                error = ctypes.get_last_error()
                raise ctypes.WinError(error)

            self.window = user32.CreateWindowExW(
                WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                self.class_name,
                "HCIS Fingerprint Capture - Tempelkan Jari",
                WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                100,
                100,
                520,
                180,
                None,
                None,
                wnd_class.hInstance,
                None,
            )
            if not self.window:
                raise ctypes.WinError(ctypes.get_last_error())
            user32.ShowWindow(self.window, SW_SHOW)
            user32.UpdateWindow(self.window)
            user32.SetForegroundWindow(self.window)

            null_guid = Guid()
            self._emit(f"[DEBUG] Initializing DPFP acquisition: priority={DP_PRIORITY_NORMAL}, sample_type={self.sample_type}")
            result = dpfp.DPFPCreateAcquisition(
                DP_PRIORITY_NORMAL,
                ctypes.byref(null_guid),
                self.sample_type,
                self.window,
                WM_FP_NOTIFY,
                ctypes.byref(self.operation),
            )
            if result != 0:
                raise RuntimeError(f"DPFPCreateAcquisition failed: 0x{result & 0xFFFFFFFF:08X}")

            result = dpfp.DPFPStartAcquisition(self.operation)
            if result != 0:
                raise RuntimeError(f"DPFPStartAcquisition failed: 0x{result & 0xFFFFFFFF:08X}")

            self._emit("Fingerprint reader ready. Tempelkan jari pada reader...")
            self._emit("[HINT] Tahan jari stabil di sensor selama minimal 1-2 detik tanpa gerak")
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if self._capture_started_at and (time.time() - self._capture_started_at) > self._capture_timeout_seconds:
                    self._emit(f"\n[DIAGNOSTIC] Timeout setelah {self._capture_timeout_seconds}s")
                    self._emit(f"[DIAGNOSTIC] Events detected: {self._touch_count} touches, {self._remove_count} removes")
                    self._emit("[DIAGNOSTIC] Sensor merespons tapi tidak mengeluarkan sample yang valid")
                    self._emit("[DIAGNOSTIC] Kemungkinan penyebab:")
                    self._emit("  1. Jari tidak cukup lama menempel (coba tahan 2-3 detik)")
                    self._emit("  2. Tekanan jari tidak konsisten (tahan dengan tekanan stabil)")
                    self._emit("  3. Sensor memerlukan pembersihan")
                    self._stop_and_close()
                    user32.PostQuitMessage(1)
                    break
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            self._stop_and_close()
            if self.window:
                user32.DestroyWindow(self.window)
                self.window = None
            dpfp.DPFPTerm()


if __name__ == "__main__":
    FingerprintCaptureAgent().run()
