# tools/solid_verifier/solid_dashboard/report/solid_project_tree.py
from pathlib import Path

# комментарий (ru): директории и файлы, которые не нужно показывать в дереве
IGNORE_DIRS = {".git", ".venv", "__pycache__", ".idea", ".mypy_cache", ".pytest_cache"}
IGNORE_FILES = {".DS_Store"}

def print_tree(root: Path, prefix: str = "") -> None:
    # комментарий (ru): собираем директории и файлы по отдельности
    entries = [p for p in root.iterdir() if p.name not in IGNORE_FILES]
    entries = [
        p for p in entries
        if (p.is_dir() and p.name not in IGNORE_DIRS) or p.is_file()
    ]
    # комментарий (ru): сначала директории, потом файлы, по имени
    entries.sort(key=lambda p: (p.is_file(), p.name))

    for index, path in enumerate(entries):
        connector = "└── " if index == len(entries) - 1 else "├── "
        print(prefix + connector + path.name)
        if path.is_dir():
            extension = "    " if index == len(entries) - 1 else "│   "
            print_tree(path, prefix + extension)

if __name__ == "__main__":
    # комментарий (ru): файл лежит в
    # tools/solid_verifier/solid_dashboard/report/solid_project_tree.py
    # значит, поднимаемся на 3 уровня до корня scopus_search_code
    project_root = Path(__file__).resolve().parents[3]
    print(project_root.name + "/")
    print_tree(project_root)