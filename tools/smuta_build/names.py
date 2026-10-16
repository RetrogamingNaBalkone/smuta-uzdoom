"""Имена DAT: правило исследованного EXE и независимые от имён ASCII-ключи."""

from .dat import Archive, Entry


EXE_SHA256 = "1b7f8e5d010e225139f3a03f3721aff39d5e0b8c1c4ef42106c14002ea6405bb"
POLICY = "cp1251-exact-bytes-first-v1"


def name_bytes(entry: Entry) -> bytes:
    """Байты до первого NUL; заполнение поля после NUL не участвует в поиске."""
    return entry.raw[:16].split(b"\0", 1)[0]


def output_name(entry: Entry) -> str:
    """Восемь ASCII-символов с префиксом Смуты, без расширения/типа ресурса."""
    if not 0 <= entry.index < 65535:
        raise ValueError("Индекс вне диапазона каталога DAT")
    return f"sm{entry.index:06d}"


class NameResolver:
    """Явно применяет правило EXE 0040D47C–0040D4D9, не проверяет версию EXE.

    Таблица содержит первые совпадения; Archive продолжает хранить все записи.
    Имена не нормализуются, не обрезаются и никогда не трактуются как пути.
    """

    def __init__(self, archive: Archive):
        self._first: dict[bytes, Entry] = {}
        for entry in archive.entries:
            self._first.setdefault(name_bytes(entry), entry)

    def resolve(self, name: str | bytes) -> Entry:
        key = name.encode("cp1251") if isinstance(name, str) else name
        if not isinstance(key, bytes):
            raise ValueError("Имя ссылки должно быть str CP1251 или bytes")
        if b"\0" in key or len(key) > 15:
            raise ValueError("Имя ссылки содержит NUL или длиннее 15 байт")
        try:
            return self._first[key]
        except KeyError:
            raise ValueError(f"Не найдена обязательная ссылка DAT: {key.hex()}") from None


def resolution_policy() -> dict:
    """Источник алгоритма, а не утверждение о происхождении входного DAT."""
    return {"id": POLICY, "evidence_exe_sha256": EXE_SHA256,
            "evidence_va": "0040D47C-0040D4D9"}
