"""Start the widget with a persistent diagnostic log, including silent GUI errors."""

import ctypes
import faulthandler
import logging
import sys
from pathlib import Path


BASE = Path(__file__).resolve().parent
LOGS = BASE / "logs"
LOGS.mkdir(parents=True, exist_ok=True)
error_stream = (LOGS / "widget-errors.log").open("a", encoding="utf-8", buffering=1)
sys.stderr = error_stream
sys.stdout = error_stream
faulthandler.enable(file=error_stream, all_threads=True)
logging.basicConfig(
    filename=LOGS / "widget.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)


def main() -> int:
    is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin()) if sys.platform == "win32" else False
    logging.info("Iniciando widget: python=%s, administrador=%s", sys.executable, is_admin)
    if not is_admin:
        logging.warning("Sem privilégios de administrador: sensores de CPU podem ficar indisponíveis")
    if not (BASE / "vendor" / "LibreHardwareMonitor" / "LibreHardwareMonitorLib.dll").exists():
        logging.warning("Biblioteca LibreHardwareMonitorLib.dll não encontrada")
    from hardware_widget import main as widget_main
    return widget_main()


if __name__ == "__main__":
    try:
        result = main()
    except Exception:
        logging.exception("O widget encerrou por uma falha inesperada")
        raise
    logging.info("Widget encerrado normalmente, código %s", result)
    raise SystemExit(result)
