"""Opt-in wgpu runtime detection, adapter/device caching, and diagnostics.

All ``wgpu`` imports are deferred so ``build123d-techdraw`` remains 100% usable
without GPU dependencies installed. When ``build123d-techdraw[gpu]`` is
installed, GPU acceleration is auto-enabled whenever a compatible hardware or
software (e.g. Mesa Lavapipe) adapter can be initialized, unless overridden by
an explicit ``gpu=False`` argument or ``BUILD123D_TECHDRAW_GPU=0``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import wgpu

GPU_ENV_VAR = "BUILD123D_TECHDRAW_GPU"

_INSTALL_HINT = (
    "GPU acceleration requires the optional 'gpu' dependency. "
    "Install it with: pip install build123d-techdraw[gpu]"
)

_FALSY_ENV = frozenset({"0", "false", "off", "no"})
_TRUTHY_ENV = frozenset({"1", "true", "on", "yes"})
_AUTO_ENV = frozenset({"", "auto"})

_SOFTWARE_ADAPTER_MARKERS = (
    "llvmpipe",
    "lavapipe",
    "swiftshader",
    "softpipe",
    "microsoft basic render",
    "warp",
)

# Cache keyed by (allow_software, force_fallback) -> (adapter, device) or None.
_DEVICE_CACHE: dict[tuple[bool, bool], tuple[Any, Any] | None] = {}


def _import_wgpu() -> Any | None:
    """Import ``wgpu`` lazily, returning ``None`` if unavailable or unloadable."""
    try:
        import wgpu as wgpu_mod
    except (ImportError, OSError):
        return None
    return wgpu_mod


def _adapter_info_dict(adapter: Any) -> dict[str, Any]:
    """Return adapter metadata as a plain dictionary."""
    raw = adapter.info
    if isinstance(raw, Mapping):
        return dict(raw)
    return {}


def _is_software_adapter(adapter: Any) -> bool:
    """Return True if ``adapter`` represents a CPU/software Vulkan/WebGPU driver."""
    info = _adapter_info_dict(adapter)
    adapter_type = str(info.get("adapter_type", "")).lower()
    if adapter_type == "cpu":
        return True
    if bool(info.get("is_fallback_adapter", False)):
        return True
    haystack = " ".join(str(info.get(k, "")) for k in ("device", "vendor", "description")).lower()
    return any(marker in haystack for marker in _SOFTWARE_ADAPTER_MARKERS)


def _try_create_device(adapter: Any) -> tuple[Any, Any] | None:
    """Attempt to create a ``GPUDevice`` from ``adapter``."""
    try:
        device = adapter.request_device_sync()
    except Exception:
        return None
    return (adapter, device)


def _probe_device(*, allow_software: bool, force_fallback: bool) -> tuple[Any, Any] | None:
    """Probe wgpu adapters and return ``(adapter, device)`` or ``None``."""
    cache_key = (allow_software, force_fallback)
    if cache_key in _DEVICE_CACHE:
        return _DEVICE_CACHE[cache_key]

    wgpu_mod = _import_wgpu()
    if wgpu_mod is None:
        _DEVICE_CACHE[cache_key] = None
        return None

    result: tuple[Any, Any] | None = None

    if force_fallback:
        try:
            fallback = wgpu_mod.gpu.request_adapter_sync(force_fallback_adapter=True)
        except Exception:
            fallback = None
        if fallback is not None:
            result = _try_create_device(fallback)
        _DEVICE_CACHE[cache_key] = result
        return result

    # Step 1: Try preferred high-performance hardware adapter first.
    try:
        adapter = wgpu_mod.gpu.request_adapter_sync(power_preference="high-performance")
    except Exception:
        adapter = None

    if adapter is not None:
        is_sw = _is_software_adapter(adapter)
        if not is_sw:
            result = _try_create_device(adapter)
            if result is not None:
                _DEVICE_CACHE[(True, False)] = result
                _DEVICE_CACHE[(False, False)] = result
                return result
        elif allow_software:
            # High-performance request already resolved to a software adapter (e.g. Vulkan Lavapipe).
            result = _try_create_device(adapter)
            if result is not None:
                _DEVICE_CACHE[(True, False)] = result
                _DEVICE_CACHE[(True, True)] = result
                return result

    # Step 2: When hardware-only is requested, scan enumerated adapters for a non-software GPU
    # in case request_adapter_sync returned a software adapter ahead of a hardware one.
    if not allow_software:
        try:
            enumerated = list(wgpu_mod.gpu.enumerate_adapters_sync())
        except Exception:
            enumerated = []
        for candidate in enumerated:
            if not _is_software_adapter(candidate):
                result = _try_create_device(candidate)
                if result is not None:
                    _DEVICE_CACHE[(True, False)] = result
                    _DEVICE_CACHE[(False, False)] = result
                    return result
        _DEVICE_CACHE[(False, False)] = None
        return None

    # Step 3: Software fallback (e.g. Mesa Lavapipe / llvmpipe in headless CI).
    try:
        fallback = wgpu_mod.gpu.request_adapter_sync(force_fallback_adapter=True)
    except Exception:
        fallback = None
    if fallback is not None:
        result = _try_create_device(fallback)
        if result is not None:
            _DEVICE_CACHE[(True, False)] = result
            _DEVICE_CACHE[(True, True)] = result
            return result

    _DEVICE_CACHE[cache_key] = None
    return None


def reset_gpu_cache() -> None:
    """Clear cached wgpu adapter/device probes (primarily for tests)."""
    _DEVICE_CACHE.clear()


def is_gpu_installed() -> bool:
    """Return True if the optional ``wgpu`` package can be imported cleanly."""
    return _import_wgpu() is not None


def is_gpu_available(*, allow_software: bool = True) -> bool:
    """Return True if a usable wgpu adapter and device can be initialized.

    Hardware adapters are preferred. When ``allow_software=True`` (the default),
    software Vulkan/WebGPU drivers such as Mesa Lavapipe are accepted as a
    fallback so headless CI environments work seamlessly.
    """
    return _probe_device(allow_software=allow_software, force_fallback=False) is not None


def is_gpu_enabled(gpu: bool | None = None) -> bool:
    """Resolve whether GPU acceleration should be used for an operation.

    Resolution order:
    1. ``gpu=False`` -> always returns ``False`` without probing adapters.
    2. ``gpu=True`` -> requires ``wgpu`` and a working adapter; raises
       :class:`ImportError` if ``wgpu`` is not installed or :class:`RuntimeError`
       if no adapter is available.
    3. ``gpu=None`` -> consults ``BUILD123D_TECHDRAW_GPU``:
       - ``"0"`` / ``"false"`` / ``"off"`` / ``"no"`` -> ``False``
       - ``"1"`` / ``"true"`` / ``"on"`` / ``"yes"`` -> require GPU (same as ``gpu=True``)
       - unset / empty / ``"auto"`` -> ``True`` iff ``is_gpu_installed()`` and
         ``is_gpu_available()``.
    """
    if gpu is False:
        return False

    require_gpu = gpu is True
    if gpu is None:
        raw_env = os.environ.get(GPU_ENV_VAR)
        if raw_env is not None:
            normalized = raw_env.strip().lower()
            if normalized in _FALSY_ENV:
                return False
            if normalized in _TRUTHY_ENV:
                require_gpu = True
            elif normalized not in _AUTO_ENV:
                raise ValueError(
                    f"Invalid {GPU_ENV_VAR} value {raw_env!r}; expected one of "
                    "'0', '1', 'false', 'true', 'off', 'on', or 'auto'."
                )

    if require_gpu:
        if not is_gpu_installed():
            raise ImportError(_INSTALL_HINT)
        if not is_gpu_available():
            raise RuntimeError(
                "GPU acceleration was explicitly enabled, but no compatible "
                "Vulkan, Metal, DirectX 12, or software (Lavapipe) wgpu adapter "
                "could be initialized."
            )
        return True

    return is_gpu_installed() and is_gpu_available()


def get_gpu_device(
    *,
    allow_software: bool = True,
    force_fallback: bool = False,
) -> tuple[wgpu.GPUAdapter, wgpu.GPUDevice]:
    """Return the cached ``(adapter, device)`` pair for wgpu execution.

    Raises:
        ImportError: If ``wgpu`` is not installed.
        RuntimeError: If no compatible wgpu adapter/device could be created.
    """
    if not is_gpu_installed():
        raise ImportError(_INSTALL_HINT)
    pair = _probe_device(allow_software=allow_software, force_fallback=force_fallback)
    if pair is None:
        mode = (
            "software fallback"
            if force_fallback
            else ("hardware or software" if allow_software else "hardware")
        )
        raise RuntimeError(f"No compatible {mode} wgpu adapter could be initialized.")
    return pair


def gpu_info(*, allow_software: bool = True) -> dict[str, Any]:
    """Return diagnostic metadata describing the active wgpu runtime environment."""
    wgpu_mod = _import_wgpu()
    installed = wgpu_mod is not None
    wgpu_version = str(wgpu_mod.__version__) if wgpu_mod is not None else None

    pair = _probe_device(allow_software=allow_software, force_fallback=False) if installed else None
    available = pair is not None

    try:
        enabled = is_gpu_enabled(None)
    except (ImportError, RuntimeError, ValueError):
        enabled = False

    if pair is None:
        return {
            "installed": installed,
            "available": False,
            "enabled": enabled,
            "backend": None,
            "adapter_name": None,
            "vendor": None,
            "description": None,
            "device_type": None,
            "is_software": False,
            "wgpu_version": wgpu_version,
        }

    adapter, _device = pair
    info = _adapter_info_dict(adapter)
    return {
        "installed": installed,
        "available": available,
        "enabled": enabled,
        "backend": str(info.get("backend_type", "Unknown")),
        "adapter_name": str(info.get("device", "")),
        "vendor": str(info.get("vendor", "")),
        "description": str(info.get("description", "")),
        "device_type": str(info.get("adapter_type", "Unknown")),
        "is_software": _is_software_adapter(adapter),
        "wgpu_version": wgpu_version,
    }
