"""Atomic JSON storage, process locking, and recoverable multi-file commits."""

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from valutatrade_hub.core.exceptions import StorageError
from valutatrade_hub.infra.settings import SettingsLoader

DEFAULTS = {
    "users.json": [],
    "portfolios.json": [],
    "rates.json": {"pairs": {}, "last_refresh": None},
    "exchange_rates.json": [],
    "transactions.json": [],
}


class DatabaseManager:
    """Singleton using __new__; one local data root and a shared thread lock."""

    _instance = None
    _singleton_lock = RLock()

    def __new__(cls):
        with cls._singleton_lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance.root = Path(SettingsLoader().get("DATA_DIR"))
                instance._lock = RLock()
                cls._instance = instance
            return cls._instance

    @contextmanager
    def transaction(self):
        """Lock read-modify-write across threads and processes; recover interrupted commits."""
        with self._lock:
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                with (self.root / ".lockfile").open("a+b") as lock:
                    lock.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        if lock.read(1) == b"":
                            lock.write(b"0")
                            lock.flush()
                        lock.seek(0)
                        msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                    try:
                        journal = self.root / ".pending.json"
                        if journal.exists():
                            self._apply(self._read_path(journal))
                            journal.unlink()
                        yield self
                    finally:
                        if os.name == "nt":
                            lock.seek(0)
                            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            except (OSError, json.JSONDecodeError, UnicodeError) as exc:
                raise StorageError(f"Ошибка JSON-хранилища ({type(exc).__name__})") from None

    def initialize(self):
        """Create missing runtime JSON files without replacing existing user data."""
        with self.transaction():
            for name, default in DEFAULTS.items():
                if not (self.root / name).exists():
                    self._atomic_write(self.root / name, default)

    @staticmethod
    def _read_path(path):
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)

    def read(self, name):
        """Read a known document; callers hold transaction() for consistency."""
        if name not in DEFAULTS:
            raise StorageError("Неизвестный документ хранилища")
        path = self.root / name
        result = self._read_path(path) if path.exists() else json.loads(json.dumps(DEFAULTS[name]))
        if not isinstance(result, type(DEFAULTS[name])):
            raise StorageError(f"Неверная структура {name}; восстановите файл из резервной копии")
        return result

    @staticmethod
    def _atomic_write(path, data):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
            ) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    def _apply(self, documents):
        for name, data in documents.items():
            if name not in DEFAULTS:
                raise StorageError("Недопустимый документ в журнале восстановления")
            self._atomic_write(self.root / name, data)

    def save_many(self, documents):
        """Commit related documents using a durable roll-forward journal under transaction()."""
        if not documents or not set(documents) <= DEFAULTS.keys():
            raise StorageError("Недопустимый набор документов")
        journal = self.root / ".pending.json"
        self._atomic_write(journal, documents)
        self._apply(documents)
        journal.unlink()
