# tools/registry.py
# -*- coding: utf-8 -*-
"""
Registro central de herramientas (tools) para Quipu Core.

✔ Registro simple vía decorador @tool o función register("name", fn)
✔ Allowlist opcional (tools/allowlist.py o tools/allowlist.{yml,yaml})
✔ Descubrimiento automático (perezoso) de módulos en tools/
✔ Métricas por herramienta (Prometheus si está disponible)
✔ API estable: REGISTRY, Tool, register, register_tool, tool, auto_discover,
  reset_registry, call_tool, list_registered
"""

from __future__ import annotations

import importlib
import inspect
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# ---------------------------------------------------------------------------
# Tipos públicos
# ---------------------------------------------------------------------------

Tool = Callable[..., Any]  # alias público

@dataclass(frozen=True)
class ToolMeta:
    name: str
    desc: str
    fn: Tool
    module: str
    meta: Dict[str, Any]  # metadatos opcionales

# Registro global: nombre -> ToolMeta
REGISTRY: Dict[str, ToolMeta] = {}

# Descubrimiento sólo una vez (on-demand)
_DISCOVERED = False


# ---------------------------------------------------------------------------
# Allowlist opcional
# ---------------------------------------------------------------------------

def _load_allowlist() -> Optional[set[str]]:
    # 1) Python
    try:
        mod = importlib.import_module(".allowlist", __package__)
        fn = getattr(mod, "is_tool_allowed", None)
        if callable(fn):
            # Delegado dinámicamente
            return None  # sentinel: usar call directo (ver _is_allowed)
        for key in ("TOOLS_ALLOWLIST", "TOOL_ALLOWLIST", "ALLOWED_TOOLS", "ALLOW_TOOLS"):
            allow = getattr(mod, key, None)
            if allow:
                return set(allow)
    except Exception:
        pass

    # 2) YAML
    try:
        import yaml  # type: ignore
        base = Path(__file__).parent
        for y in ("allowlist.yml", "allowlist.yaml"):
            p = base / y
            if p.exists():
                data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                if isinstance(data, dict):
                    for k in ("tools", "allowlist", "allowed_tools"):
                        v = data.get(k)
                        if isinstance(v, (list, tuple, set)):
                            return set(v)
                if isinstance(data, (list, tuple, set)):
                    return set(data)
    except Exception:
        pass

    return None


_ALLOW: Optional[set[str]] = _load_allowlist()

def _is_allowed(name: str) -> bool:
    try:
        mod = importlib.import_module(".allowlist", __package__)
        fn = getattr(mod, "is_tool_allowed", None)
        if callable(fn):
            return bool(fn(name))
    except Exception:
        pass
    return True if _ALLOW is None else name in _ALLOW


# ---------------------------------------------------------------------------
# Métricas Prometheus (opcionales)
# ---------------------------------------------------------------------------

_METRICS_ON = True
try:
    from prometheus_client import Counter, Histogram  # type: ignore

    TOOL_CALLS = Counter(
        "quipu_tool_calls_total",
        "Total de invocaciones de herramientas",
        ["tool", "ok"],
    )
    TOOL_LAT = Histogram(
        "quipu_tool_latency_seconds",
        "Latencia por herramienta",
        ["tool"],
    )
except Exception:  # pragma: no cover
    _METRICS_ON = False
    TOOL_CALLS = None
    TOOL_LAT = None


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------

def _register(name: str, fn: Tool, desc: str = "", meta: Optional[Dict[str, Any]] = None) -> None:
    if not callable(fn):
        raise TypeError(f"Tool '{name}' no es callable")
    if not _is_allowed(name):
        return
    REGISTRY[name] = ToolMeta(
        name=name,
        desc=desc or (getattr(fn, "__doc__", "") or "").strip(),
        fn=fn,
        module=getattr(fn, "__module__", ""),
        meta=dict(meta or {}),
    )


def register(name: str, fn: Tool, desc: str = "", **meta: Any) -> None:
    _register(name, fn, desc, meta or None)


register_tool = register


def tool(name: Optional[str] = None, desc: str = "", **meta: Any):
    def _decorator(fn: Tool) -> Tool:
        tool_name = name or fn.__name__
        _register(tool_name, fn, desc, meta or None)
        setattr(fn, "_quipu_tool", True)
        setattr(fn, "_quipu_tool_name", tool_name)
        setattr(fn, "_quipu_tool_desc", desc or (fn.__doc__ or ""))
        setattr(fn, "_quipu_tool_meta", dict(meta or {}))
        return fn
    return _decorator


# ---------------------------------------------------------------------------
# Descubrimiento lazy de módulos en tools/
# ---------------------------------------------------------------------------

def auto_discover(force: bool = False) -> None:
    global _DISCOVERED
    if _DISCOVERED and not force:
        return

    base = Path(__file__).parent
    for py in sorted(base.glob("*.py")):
        stem = py.stem
        if stem in {"__init__", "registry", "allowlist", "instrumentation", "tool_selector"}:
            continue
        try:
            mod = importlib.import_module(f".{stem}", __package__)
        except Exception:
            continue

        for _, obj in inspect.getmembers(mod, inspect.isfunction):
            if getattr(obj, "_quipu_tool", False):
                _register(
                    getattr(obj, "_quipu_tool_name"),
                    obj,
                    getattr(obj, "_quipu_tool_desc", ""),
                    getattr(obj, "_quipu_tool_meta", {}),
                )

    _DISCOVERED = True


def reset_registry(clear_allowlist_cache: bool = False) -> None:
    global _DISCOVERED, _ALLOW
    REGISTRY.clear()
    _DISCOVERED = False
    if clear_allowlist_cache:
        _ALLOW = _load_allowlist()


# ---------------------------------------------------------------------------
# API de consulta / ejecución
# ---------------------------------------------------------------------------

def list_registered() -> Dict[str, Dict[str, str]]:
    auto_discover()
    return {
        name: {"desc": meta.desc, "module": meta.module}
        for name, meta in sorted(REGISTRY.items())
    }


def call_tool(name: str, /, **kwargs: Any):
    auto_discover()
    meta = REGISTRY.get(name)
    if not meta:
        return False, f"tool '{name}' no encontrada"

    ok = True
    result: Any = None
    t0 = time.perf_counter()
    try:
        result = meta.fn(**kwargs)
    except Exception as e:
        ok = False
        result = f"{type(e).__name__}: {e}"
    finally:
        dt = time.perf_counter() - t0
        if _METRICS_ON:
            try:
                TOOL_LAT.labels(tool=name).observe(dt)  # type: ignore
                TOOL_CALLS.labels(tool=name, ok=str(ok)).inc()  # type: ignore
            except Exception:
                pass

    return (True, result) if ok else (False, result)


__all__ = [
    "Tool",
    "ToolMeta",
    "REGISTRY",
    "register",
    "register_tool",
    "tool",
    "auto_discover",
    "reset_registry",
    "call_tool",
    "list_registered",
]
