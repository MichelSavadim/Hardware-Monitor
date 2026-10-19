import os
from threading import Event
import unittest
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
import hardware_widget


class VisibilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_each_metric_disappears_without_hiding_last_row(self):
        with patch.object(hardware_widget, "desktop_host", return_value=0), \
             patch.object(hardware_widget, "collect_basic", return_value={}), \
             patch.object(hardware_widget, "read_lhm", return_value={}), \
             patch.object(hardware_widget, "EnergySummary"), \
             patch.object(hardware_widget, "read_settings", return_value={}), \
             patch.object(hardware_widget, "save_settings"):
            widget = hardware_widget.HardwareWidget()
            try:
                self.app.processEvents()
                initial_height = widget.height()
                for index, (key, _, _) in enumerate(hardware_widget.METRICS[:-1]):
                    action = widget.metrics_menu.actions()[index]
                    action.trigger()
                    self.app.processEvents()
                    self.assertNotIn(key, widget.enabled)
                    self.assertFalse(widget.rows[key].isVisible(), key)
                    self.assertEqual(widget.layout.indexOf(widget.rows[key]), -1, key)
                    self.assertTrue(widget.rows["ram_clock"].isVisible(), key)
                    self.assertGreaterEqual(widget.layout.indexOf(widget.rows["ram_clock"]), 0, key)
                    self.assertLess(widget.rows["ram_clock"].geometry().bottom(), widget.height(), key)
                    self.assertLess(widget.height(), initial_height)
                    action.trigger()
                    self.app.processEvents()
                    self.assertEqual(widget.height(), initial_height)
            finally:
                widget.close()

    def test_basic_metrics_update_while_hardware_library_is_slow(self):
        release = Event()

        def slow_sensors():
            release.wait(timeout=2)
            return {"cpu_temp": 45.0}

        with patch.object(hardware_widget, "desktop_host", return_value=0), \
             patch.object(hardware_widget, "collect_basic", return_value={"gpu_use": 23.0, "cpu_use": 7.0}), \
             patch.object(hardware_widget, "read_lhm", side_effect=slow_sensors), \
             patch.object(hardware_widget, "EnergySummary") as energy_summary, \
             patch.object(hardware_widget.logging, "exception"), \
             patch.object(hardware_widget, "read_settings", return_value={}), \
             patch.object(hardware_widget, "save_settings"):
            energy_summary.return_value.record.side_effect = PermissionError("OneDrive locked")
            widget = hardware_widget.HardwareWidget()
            try:
                widget.basic_pending.result(timeout=2)
                self.assertFalse(widget.sensor_pending.done())
                widget.tick()
                self.assertEqual(widget.rows["gpu_use"].value, 23.0)
                self.assertEqual(widget.rows["cpu_use"].value, 7.0)
            finally:
                release.set()
                widget.close()


if __name__ == "__main__":
    unittest.main()
