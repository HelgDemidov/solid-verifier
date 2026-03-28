import argparse
import json
from pathlib import Path

from .pipeline import run_pipeline
from .config import load_config

from .adapters.radon_adapter import RadonAdapter
from .adapters.cohesion_adapter import CohesionAdapter
from .adapters.import_graph_adapter import ImportGraphAdapter
from .adapters.import_linter_adapter import ImportLinterAdapter
from .adapters.pyan3_adapter import Pyan3Adapter

def main() -> None:
    parser = argparse.ArgumentParser(description="SOLID-Verifier Dashboard")
    parser.add_argument(
        "--target-dir",
        required=True,
        help="Path to analyzed project (Python package root)",
    )
    parser.add_argument(
        "--config",
        required=False,
        help="Path to solid_config.json (search performed in current catalog by default)",
    )

    args = parser.parse_args()

    # Загружаем конфиг верификатора
    config = load_config(args.config)

    # Инициализируем адаптеры Блока 1
    adapters = [
        RadonAdapter(),
        CohesionAdapter(),
        ImportGraphAdapter(),
        ImportLinterAdapter(),
        Pyan3Adapter(),
    ]

    results = run_pipeline(args.target_dir, config, adapters)
    
    # Форматируем результат в JSON строку
    report_text = json.dumps(results, indent=2, ensure_ascii=False)

    # Печатаем в консоль
    print("\n=== Pipeline Result ===")
    print(report_text)
    
    # Сохраняем в файл report/solid_report.log
    # Получаем путь к директории, где лежит текущий файл (__main__.py)
    base_dir = Path(__file__).resolve().parent
    report_dir = base_dir / "report"
    
    # Создаем папку report, если ее еще нет
    report_dir.mkdir(exist_ok=True)
    
    report_path = report_dir / "solid_report.log"
    with report_path.open("w", encoding="utf-8") as f:
        f.write(report_text)
        
    print(f"\nReport successfully saved to: {report_path}")

if __name__ == "__main__":
    main()
