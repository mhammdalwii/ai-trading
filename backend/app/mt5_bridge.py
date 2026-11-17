"""Jembatan ke library MetaTrader5 yang berjalan di Wine (container Linux) lewat RPyC.

Library resmi `MetaTrader5` hanya ada di Windows. Di VPS Linux, terminal MT5 dan Python Windows
dijalankan di Wine (image `gmag11/metatrader5_vnc`), lalu library-nya dibuka lewat server RPyC
"classic" (mt5linux) di port 8001. Modul ini membuat objek yang bisa dipakai persis seperti
`import MetaTrader5 as mt5`, sehingga `MT5Source` tidak perlu tahu bedanya.

Supaya tidak bergantung pada versi numpy/pickle di dua sisi, setiap hasil diubah dulu menjadi
tipe bawaan Python (dict, list, angka, teks) di sisi server, baru dikirim ke sini dan disusun
ulang menjadi namedtuple atau array numpy.

PENTING: server RPyC classic mengizinkan eksekusi kode apa pun. Port-nya (8001) JANGAN pernah
dibuka ke internet; di docker-compose port ini hanya ada di jaringan internal antar-container.
"""
from __future__ import annotations

import threading
from collections import namedtuple
from functools import lru_cache

import numpy as np

from .data_sources import DataSourceError

# Kode ini dijalankan di Python Windows (Wine, Python 3.9), jadi tulis dengan sintaks lama.
_SERVER_HELPER = '''
import MetaTrader5 as _ai_mt5

def _ai_plain(v):
    if v is None or isinstance(v, (bool, int, float, str, bytes)):
        return v
    if hasattr(v, "_asdict"):
        return {"__nt__": type(v).__name__, "fields": {k: _ai_plain(x) for k, x in v._asdict().items()}}
    if hasattr(v, "dtype") and hasattr(v, "tolist"):
        if v.dtype.names:
            return {"__array__": [[str(n), v.dtype.fields[n][0].str] for n in v.dtype.names], "rows": v.tolist()}
        return v.tolist()
    if isinstance(v, (list, tuple)):
        return [_ai_plain(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _ai_plain(x) for k, x in v.items()}
    if hasattr(v, "item"):
        return v.item()
    return str(v)

def _ai_call(name, args, kwargs):
    local_args = []
    for a in args:
        local_args.append(dict(a.items()) if hasattr(a, "items") else a)
    local_kwargs = dict(kwargs.items())
    fn = getattr(_ai_mt5, name)
    return _ai_plain(fn(*local_args, **local_kwargs))
'''


class BridgeError(DataSourceError):
    """Server RPyC MT5 tidak bisa dihubungi atau koneksinya putus."""


# Panggilan yang mengubah akun TIDAK boleh diulang otomatis: jika koneksi putus setelah server
# menerima order, mengulang bisa membuka posisi dua kali.
_NO_RETRY = frozenset({"order_send"})


@lru_cache(maxsize=None)
def _nt_type(name: str, fields: tuple[str, ...]):
    return namedtuple(name, fields)


def _rebuild(v):
    """Kebalikan dari _ai_plain: susun ulang namedtuple dan array numpy di sisi lokal."""
    if isinstance(v, dict) and "__nt__" in v:
        fields = v["fields"]
        return _nt_type(v["__nt__"], tuple(fields))(*(_rebuild(x) for x in fields.values()))
    if isinstance(v, dict) and "__array__" in v:
        dtype = [(n, t) for n, t in v["__array__"]]
        return np.array([tuple(r) for r in v["rows"]], dtype=dtype)
    if isinstance(v, list):
        return tuple(_rebuild(x) for x in v)
    if isinstance(v, dict):
        return {k: _rebuild(x) for k, x in v.items()}
    return v


def _obtain(proxy):
    """Salin nilai dari server ke lokal (hanya tipe bawaan, jadi aman lintas versi Python)."""
    from rpyc.utils.classic import obtain

    return obtain(proxy)


class RemoteMT5:
    """Pengganti modul MetaTrader5 yang meneruskan semua panggilan ke server RPyC."""

    def __init__(self, host: str, port: int = 8001, timeout: float = 60.0, connect=None):
        self._host, self._port, self._timeout = host, int(port), float(timeout)
        self._connect = connect or self._default_connect
        self._conn = None
        self._lock = threading.Lock()
        self._constants: dict[str, object] = {}

    # --- koneksi ---------------------------------------------------------------
    def _default_connect(self):
        import rpyc

        return rpyc.classic.connect(self._host, self._port, keepalive=True)

    def _connection(self):
        if self._conn is None or self._conn.closed:
            try:
                conn = self._connect()
            except Exception as exc:  # noqa: BLE001
                raise BridgeError(
                    f"Tidak bisa terhubung ke server MT5 di {self._host}:{self._port} ({exc}). "
                    "Pastikan container mt5 sudah jalan dan terminal MT5 sudah terbuka."
                ) from exc
            conn._config["sync_request_timeout"] = self._timeout
            conn.execute(_SERVER_HELPER)
            self._conn = conn
        return self._conn

    def _reset(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
        self._conn = None

    def close(self) -> None:
        with self._lock:
            self._reset()

    def _call(self, name: str, args: tuple, kwargs: dict):
        with self._lock:
            attempts = 1 if name in _NO_RETRY else 2
            for attempt in range(1, attempts + 1):
                conn = self._connection()
                try:
                    return _rebuild(_obtain(conn.namespace["_ai_call"](name, args, kwargs)))
                except (EOFError, ConnectionError, OSError) as exc:  # termasuk TimeoutError
                    # koneksi putus (container mt5 restart): sambung ulang sekali untuk panggilan baca
                    self._reset()
                    if attempt == attempts:
                        if name in _NO_RETRY:
                            raise BridgeError(
                                f"Koneksi ke MT5 putus saat {name} ({exc!r}). Status order tidak pasti: "
                                "cek posisi di MT5 sebelum mencoba lagi."
                            ) from exc
                        raise BridgeError(f"Koneksi ke server MT5 putus saat {name} ({exc!r})") from exc
        raise AssertionError("unreachable")  # pragma: no cover

    def _constant(self, name: str):
        if name not in self._constants:
            with self._lock:
                self._constants[name] = _obtain(self._connection().eval(f"_ai_mt5.{name}"))
        return self._constants[name]

    # --- API seperti modul MetaTrader5 -------------------------------------------
    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        if name.isupper():
            return self._constant(name)

        def remote(*args, **kwargs):
            return self._call(name, args, kwargs)

        remote.__name__ = name
        return remote

    def shutdown(self):
        try:
            return self._call("shutdown", (), {})
        finally:
            self.close()
