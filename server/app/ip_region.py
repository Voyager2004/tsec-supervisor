"""IP 归属地离线查询（ip2region xdb，进程内缓存）。

调用方均在 Database 锁内串行调用，缓存无需独立加锁。
"""

from __future__ import annotations

from pathlib import Path

from .ip2region import new_with_buffer, util

EMPTY_SEGMENTS = {"", "0"}
NON_LOCATION_SEGMENTS = {"", "0", "Reserved", "内网IP"}


class IpRegionResolver:
    def __init__(self, db_path: Path):
        data = Path(db_path).read_bytes()
        version = util.version_from_header(util.Header(data[:util.HeaderInfoLength]))
        self._searcher = new_with_buffer(version, data)
        self._cache: dict[str, str | None] = {}

    def _raw(self, ip: str) -> str | None:
        if ip in self._cache:
            return self._cache[ip]
        try:
            raw = self._searcher.search(ip)
        except ValueError:
            raw = None
        self._cache[ip] = raw or None
        return raw or None

    def segments(self, ip: str | None) -> list[str] | None:
        if not ip:
            return None
        normalized = ip.removeprefix("::ffff:")
        raw = self._raw(normalized)
        if raw is None:
            return None
        return raw.split("|")

    def location_text(self, ip: str | None) -> str | None:
        segments = self.segments(ip)
        if segments is None or segments[0] == "Reserved":
            return None
        parts = [segment for segment in segments if segment not in EMPTY_SEGMENTS]
        if not parts:
            return None
        return " ".join(parts)

    def is_outside_province(self, ip: str | None, province: str) -> bool:
        segments = self.segments(ip)
        if segments is None or len(segments) < 2:
            return False
        found_province = segments[1]
        return found_province not in NON_LOCATION_SEGMENTS and found_province != province


def make_resolver(db_path: str | Path | None) -> IpRegionResolver | None:
    if not db_path:
        return None
    path = Path(db_path)
    if not path.is_file():
        return None
    return IpRegionResolver(path)
