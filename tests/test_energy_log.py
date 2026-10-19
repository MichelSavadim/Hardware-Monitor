import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import energy_log
from energy_log import EnergySummary

TEST_DIR = Path(__file__).resolve().parent


class EnergySummaryTest(unittest.TestCase):
    def test_overwrites_one_snapshot_with_cumulative_energy(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIR) as temp:
            self.assertTrue(Path(temp).resolve().is_relative_to(TEST_DIR))
            path = Path(temp) / "consumo_atual.json"
            started = datetime(2026, 10, 13, tzinfo=timezone.utc)
            summary = EnergySummary(path, clock=lambda: 0.0, started_at=started)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["total"]["energia_wh"], 0)

            summary.record({"cpu_power": 36.0, "gpu_power": 72.0}, now=1.0, timestamp=started)
            first = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(first["tempo_atividade_s"], 1)
            self.assertEqual(first["cpu"]["energia_wh"], 0.01)
            self.assertEqual(first["gpu"]["energia_wh"], 0.02)
            self.assertEqual(first["total"]["energia_kwh"], 0.00003)

            summary.record({"cpu_power": 72.0, "gpu_power": 0.0}, now=2.0, timestamp=started)
            summary.record({"cpu_power": None, "gpu_power": 36.0}, now=3.0, timestamp=started)
            summary.record({"cpu_power": 36.0, "gpu_power": 36.0}, now=4.0, timestamp=started)
            latest = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(latest["tempo_atividade_s"], 4)
            self.assertEqual(latest["cpu"]["energia_wh"], 0.025)
            self.assertEqual(latest["cpu"]["tempo_medido_s"], 2)
            self.assertEqual(latest["gpu"]["energia_wh"], 0.045)
            self.assertEqual(latest["gpu"]["tempo_medido_s"], 4)
            self.assertEqual(latest["total"]["energia_wh"], 0.07)
            daily_path = Path(temp) / "logs" / "diario" / "2026-10-13.json"
            day = json.loads(daily_path.read_text(encoding="utf-8"))
            self.assertEqual(day["total"]["energia_wh"], 0.07)
            self.assertEqual(day["tempo_atividade_s"], 4)
            self.assertEqual(day["sessoes"], 1)

            restarted = EnergySummary(path, clock=lambda: 4.0, started_at=started)
            restarted.record({"cpu_power": 36.0, "gpu_power": 72.0}, now=5.0, timestamp=started)
            day = json.loads(daily_path.read_text(encoding="utf-8"))
            self.assertEqual(day["sessoes"], 2)
            self.assertEqual(day["total"]["energia_wh"], 0.1)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["total"]["energia_wh"], 0.03)

    def test_creates_new_day_without_overwriting_previous_day(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIR) as temp:
            self.assertTrue(Path(temp).resolve().is_relative_to(TEST_DIR))
            path = Path(temp) / "consumo_atual.json"
            before = datetime(2026, 10, 13, 23, 59, 58, tzinfo=timezone.utc)
            after = datetime(2026, 10, 14, 0, 0, 0, tzinfo=timezone.utc)
            summary = EnergySummary(path, clock=lambda: 0.0, started_at=before)
            summary.record({"cpu_power": 36.0, "gpu_power": 0.0}, now=1.0, timestamp=before)
            summary.record({"cpu_power": 36.0, "gpu_power": 0.0}, now=2.0, timestamp=after)
            old_day = json.loads((Path(temp) / "logs" / "diario" / "2026-10-13.json").read_text())
            new_day = json.loads((Path(temp) / "logs" / "diario" / "2026-10-14.json").read_text())
            self.assertEqual(old_day["cpu"]["energia_wh"], 0.01)
            self.assertEqual(new_day["cpu"]["energia_wh"], 0.01)

    def test_retries_temporary_windows_file_lock(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIR) as temp:
            self.assertTrue(Path(temp).resolve().is_relative_to(TEST_DIR))
            path = Path(temp) / "consumo_atual.json"
            real_replace = energy_log.os.replace
            attempts = 0

            def locked_twice(source, target):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise PermissionError("temporarily locked")
                return real_replace(source, target)

            with patch.object(energy_log.os, "replace", side_effect=locked_twice), \
                 patch.object(energy_log.time, "sleep"):
                EnergySummary(path, clock=lambda: 0.0,
                              started_at=datetime(2026, 10, 13, tzinfo=timezone.utc))
            self.assertEqual(attempts, 4)  # Daily file retried twice; live file once.
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["total"]["energia_wh"], 0)


if __name__ == "__main__":
    unittest.main()
