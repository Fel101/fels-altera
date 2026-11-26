"""Read installed RAM and the largest active adapter's dedicated memory for setup."""
import ctypes
import os
import re

GIB = 1024 ** 3


def _windows_ram():
    class MemoryStatus(ctypes.Structure):
        _fields_ = [('length', ctypes.c_uint32), ('load', ctypes.c_uint32), ('total', ctypes.c_uint64),
                    ('remaining', ctypes.c_uint64 * 6)]

    info = MemoryStatus()
    info.length = ctypes.sizeof(info)
    return info.total if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(info)) else None


def _windows_vram():
    import winreg

    sizes = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DEVICEMAP\VIDEO') as video:
            for index in range(winreg.QueryInfoKey(video)[1]):
                name, path, _ = winreg.EnumValue(video, index)
                if not isinstance(path, str) or not re.fullmatch(r'\\Device\\Video\d+', name, re.I):
                    continue
                prefix = '\\Registry\\Machine\\'
                if not path.casefold().startswith(prefix.casefold()):
                    continue
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path[len(prefix):]) as adapter:
                        size, kind = winreg.QueryValueEx(adapter, 'HardwareInformation.qwMemorySize')
                    if kind == winreg.REG_QWORD and 0 < size < 1024 * GIB:
                        sizes.append(size)
                except OSError:
                    continue
    except OSError:
        return None
    # One GPU must fit the model. Several display outputs can map to the same adapter.
    return max(sizes, default=None)


def memory():
    """Return total RAM and dedicated video memory in GiB, with None for unknown."""
    if os.name == 'nt':
        ram, vram = _windows_ram(), _windows_vram()
    else:
        try:
            ram = os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
        except (OSError, ValueError, AttributeError):
            ram = None
        vram = None
    return ram / GIB if ram and ram > 0 else None, vram / GIB if vram else None
