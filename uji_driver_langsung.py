"""Probe aman untuk DLL driver DigitalPersona/U.are.U 4500.

File ini hanya memuat DLL dan memeriksa fungsi yang diekspor.
Ia tidak memanggil fungsi capture/enrollment yang belum terdokumentasi.
"""

import argparse
import ctypes
import os
import sys
from pathlib import Path

DEFAULT_DLL_DIR = Path(
    r"D:\sementara\SFW-02580-DP4500 Fingerprint Reader Driver "
    r"(Legacy) with installer v.4.1.1.221 (1)\Legacy-4.1.1.221\DP4500-4.1.1.221\x64"
)

DLL_NAMES = (
    "dpDevCtlx64.dll",
    "dpDevDatx64.dll",
    "dpd00701x64.dll",
    "dpi00701x64.dll",
)

# Nama yang umum pada SDK DigitalPersona. Hanya dicek keberadaannya.
KNOWN_API_NAMES = (
    "dpfpdd_init",
    "dpfpdd_query_devices",
    "dpfpdd_open",
    "dpfpdd_capture",
    "dpfpdd_close",
    "DPDeviceInit",
    "DPDeviceEnumerate",
    "DPDeviceOpen",
    "DPDeviceCapture",
)


def load_driver_dll(path):
    try:
        return ctypes.WinDLL(str(path))
    except OSError as error:
        print(f"[GAGAL] {path.name}: {error}")
        return None


def inspect_dll(path):
    print(f"\nDLL: {path}")
    library = load_driver_dll(path)
    if library is None:
        return False

    print("[OK] DLL berhasil dimuat oleh Python 64-bit.")
    found = []
    for name in KNOWN_API_NAMES:
        if getattr(library, name, None) is not None:
            found.append(name)

    if found:
        print("[INFO] Fungsi API yang dikenal ditemukan:")
        for name in found:
            print(f"  - {name}")
    else:
        print("[INFO] Tidak ada nama API SDK standar yang ditemukan.")
        print("       DLL ini kemungkinan komponen internal driver, bukan SDK capture.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Uji pemuatan DLL driver U.are.U 4500")
    parser.add_argument("--dll-dir", type=Path, default=DEFAULT_DLL_DIR)
    args = parser.parse_args()

    if sys.maxsize <= 2**32:
        print("[GAGAL] Jalankan dengan Python 64-bit untuk DLL x64.")
        return 2

    dll_dir = args.dll_dir.expanduser().resolve()
    if not dll_dir.is_dir():
        print(f"[GAGAL] Folder tidak ditemukan: {dll_dir}")
        return 1

    os.add_dll_directory(str(dll_dir))
    print(f"Folder driver: {dll_dir}")
    print(f"Python: {sys.executable}")

    loaded = 0
    for dll_name in DLL_NAMES:
        dll_path = dll_dir / dll_name
        if dll_path.exists() and inspect_dll(dll_path):
            loaded += 1

    print(f"\nDLL berhasil dimuat: {loaded}")
    print("Kesimpulan: pemuatan DLL bukan berarti capture sidik jari tersedia.")
    print("Capture membutuhkan API SDK yang terdokumentasi atau program perantara resmi.")
    return 0 if loaded else 1


if __name__ == "__main__":
    raise SystemExit(main())
