"""One-time import of the old per-second CSV and live session into daily summaries."""

import csv
import json
from pathlib import Path

from energy_log import EnergySummary


BASE = Path(__file__).resolve().parent
DAILY = BASE / "logs" / "diario"


def empty_day(day: str) -> dict:
    return {
        "data": day,
        "ultima_atualizacao": "",
        "sessoes": 0,
        "tempo_atividade_s": 0.0,
        "cpu": {"energia_wh": 0.0, "energia_kwh": 0.0, "tempo_medido_s": 0.0},
        "gpu": {"energia_wh": 0.0, "energia_kwh": 0.0, "tempo_medido_s": 0.0},
        "total": {"energia_wh": 0.0, "energia_kwh": 0.0},
    }


def add_session(days: dict, day: str, updated: str, uptime: float,
                cpu_wh: float, gpu_wh: float, cpu_measured: float, gpu_measured: float) -> None:
    data = days.setdefault(day, empty_day(day))
    data["ultima_atualizacao"] = max(data["ultima_atualizacao"], updated)
    data["sessoes"] += 1
    data["tempo_atividade_s"] += uptime
    for part, wh, measured in (("cpu", cpu_wh, cpu_measured), ("gpu", gpu_wh, gpu_measured)):
        data[part]["energia_wh"] += wh
        data[part]["tempo_medido_s"] += measured


def collect_csv(days: dict, path: Path) -> None:
    previous = None
    measured = {"cpu": 0.0, "gpu": 0.0}
    with path.open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            if previous is not None:
                delta = float(row["elapsed_s"]) - float(previous["elapsed_s"])
                if 0 < delta <= 5:
                    for part in ("cpu", "gpu"):
                        if row[f"{part}_w"] and previous[f"{part}_w"]:
                            measured[part] += delta
            previous = row
    if previous is None:
        return
    add_session(days, previous["timestamp"][:10], previous["timestamp"],
                float(previous["elapsed_s"]), float(previous["cpu_wh"]),
                float(previous["gpu_wh"]), measured["cpu"], measured["gpu"])


def main() -> None:
    days = {}
    for path in (BASE / "logs").glob("consumo_*.csv"):
        collect_csv(days, path)
    live_path = BASE / "consumo_atual.json"
    if live_path.exists():
        live = json.loads(live_path.read_text(encoding="utf-8"))
        add_session(days, live["inicio"][:10], live["ultima_atualizacao"],
                    live["tempo_atividade_s"], live["cpu"]["energia_wh"],
                    live["gpu"]["energia_wh"], live["cpu"]["tempo_medido_s"],
                    live["gpu"]["tempo_medido_s"])
    DAILY.mkdir(parents=True, exist_ok=True)
    for day, data in days.items():
        target = DAILY / f"{day}.json"
        if target.exists():
            print(f"Preservado: {target}")
            continue
        for part in ("cpu", "gpu"):
            data[part]["energia_wh"] = round(data[part]["energia_wh"], 6)
            data[part]["energia_kwh"] = round(data[part]["energia_wh"] / 1000, 9)
            data[part]["tempo_medido_s"] = round(data[part]["tempo_medido_s"], 3)
        total = data["cpu"]["energia_wh"] + data["gpu"]["energia_wh"]
        data["total"] = {"energia_wh": round(total, 6), "energia_kwh": round(total / 1000, 9)}
        data["tempo_atividade_s"] = round(data["tempo_atividade_s"], 3)
        EnergySummary._write_atomic(target, data)
        print(f"Importado: {target}")


if __name__ == "__main__":
    main()
