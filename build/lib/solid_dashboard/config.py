import json
from pathlib import Path
from typing import Any, Dict

def load_config(path: str | None) -> Dict[str, Any]:
    """
    Загружает конфиг верификатора из JSON-файла.
    Если путь не передан, ищет solid_config.json в той же директории, где лежит сам скрипт.
    """
    # Если путь явно указан через --config
    if path:
        config_path = Path(path).resolve()
    else:
        # Получаем абсолютный путь к папке, где находится текущий файл (config.py)
        # Привязываем поиск конфига к расположению исходного кода дашборда,
        # а не к тому месту, откуда пользователь (или git hook) вызывает команду в терминале
        base_dir = Path(__file__).resolve().parent
        config_path = base_dir / "solid_config.json"

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # Минимальная валидация ключей
    if "package_root" not in data:
        raise ValueError("Config must contain 'package_root'")
    if "layers" not in data or not isinstance(data["layers"], dict):
        raise ValueError("Config must contain 'layers' dict")
    if "ignore_dirs" not in data or not isinstance(data["ignore_dirs"], list):
        raise ValueError("Config must contain 'ignore_dirs' list")

    return data
