"""Keep compact live and daily snapshots of cumulative CPU and GPU energy."""

import json
import math
import os
import time
from datetime import datetime
from pathlib import Path


class EnergySummary:
    def __init__(self, path: Path, daily_dir: Path | None = None, *, clock=time.monotonic, started_at=None):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.daily_dir = daily_dir or path.parent / "logs" / "diario"
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.started_at = started_at or datetime.now().astimezone()
        self.start = clock()
        self.previous_time = self.start
        self.previous = {"cpu": None, "gpu": None}
        self.energy_wh = {"cpu": 0.0, "gpu": 0.0}
        self.measured_seconds = {"cpu": 0.0, "gpu": 0.0}
        self.samples = 0
        self.day = None
        self.day_data = None
        self._select_day(self.started_at)
        self._write_daily()
        self.write(now=self.start, timestamp=self.started_at)

    def _select_day(self, timestamp: datetime) -> None:
        day = timestamp.date().isoformat()
        if day == self.day:
            return
        self.day = day
        self.daily_path = self.daily_dir / f"{day}.json"
        if self.daily_path.exists():
            self.day_data = json.loads(self.daily_path.read_text(encoding="utf-8"))
        else:
            self.day_data = {
                "data": day,
                "ultima_atualizacao": timestamp.isoformat(timespec="seconds"),
                "sessoes": 0,
                "tempo_atividade_s": 0.0,
                "cpu": {"energia_wh": 0.0, "energia_kwh": 0.0, "tempo_medido_s": 0.0},
                "gpu": {"energia_wh": 0.0, "energia_kwh": 0.0, "tempo_medido_s": 0.0},
                "total": {"energia_wh": 0.0, "energia_kwh": 0.0},
            }
        self.day_data["sessoes"] += 1

    @staticmethod
    def _write_atomic(path: Path, data: dict) -> None:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)

    def _write_daily(self) -> None:
        self._write_atomic(self.daily_path, self.day_data)

    def record(self, readings: dict, *, now=None, timestamp=None) -> None:
        now = self.clock() if now is None else now
        timestamp = datetime.now().astimezone() if timestamp is None else timestamp
        delta_seconds = max(0.0, now - self.previous_time)
        self._select_day(timestamp)
        additions_wh = {"cpu": 0.0, "gpu": 0.0}
        measured_additions = {"cpu": 0.0, "gpu": 0.0}
        for part in ("cpu", "gpu"):
            value = readings.get(f"{part}_power")
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                value = None
            if value is not None:
                previous = self.previous[part]
                if previous is not None and delta_seconds <= 5:
                    additions_wh[part] = (previous + value) / 2 * delta_seconds / 3600
                    measured_additions[part] = delta_seconds
                elif self.samples == 0 and delta_seconds <= 5:
                    # Estimate only the first interval when its first reading exists.
                    additions_wh[part] = value * delta_seconds / 3600
                    measured_additions[part] = delta_seconds
            self.energy_wh[part] += additions_wh[part]
            self.measured_seconds[part] += measured_additions[part]
            self.previous[part] = value
            day_part = self.day_data[part]
            day_part["energia_wh"] = round(day_part["energia_wh"] + additions_wh[part], 6)
            day_part["energia_kwh"] = round(day_part["energia_wh"] / 1000, 9)
            day_part["tempo_medido_s"] = round(day_part["tempo_medido_s"] + measured_additions[part], 3)
        self.day_data["tempo_atividade_s"] = round(
            self.day_data["tempo_atividade_s"] + (delta_seconds if delta_seconds <= 5 else 0), 3
        )
        total_wh = self.day_data["cpu"]["energia_wh"] + self.day_data["gpu"]["energia_wh"]
        self.day_data["total"] = {
            "energia_wh": round(total_wh, 6),
            "energia_kwh": round(total_wh / 1000, 9),
        }
        self.day_data["ultima_atualizacao"] = timestamp.isoformat(timespec="seconds")
        self.previous_time = now
        self.samples += 1
        self.write(now=now, timestamp=timestamp)
        self._write_daily()

    def write(self, *, now=None, timestamp=None) -> None:
        now = self.clock() if now is None else now
        timestamp = datetime.now().astimezone() if timestamp is None else timestamp
        cpu_wh = self.energy_wh["cpu"]
        gpu_wh = self.energy_wh["gpu"]
        total_wh = cpu_wh + gpu_wh
        snapshot = {
            "inicio": self.started_at.isoformat(timespec="seconds"),
            "ultima_atualizacao": timestamp.isoformat(timespec="seconds"),
            "tempo_atividade_s": round(max(0.0, now - self.start), 3),
            "cpu": {
                "energia_wh": round(cpu_wh, 6),
                "energia_kwh": round(cpu_wh / 1000, 9),
                "tempo_medido_s": round(self.measured_seconds["cpu"], 3),
            },
            "gpu": {
                "energia_wh": round(gpu_wh, 6),
                "energia_kwh": round(gpu_wh / 1000, 9),
                "tempo_medido_s": round(self.measured_seconds["gpu"], 3),
            },
            "total": {
                "energia_wh": round(total_wh, 6),
                "energia_kwh": round(total_wh / 1000, 9),
            },
        }
        self._write_atomic(self.path, snapshot)

    def close(self) -> None:
        self.write()
