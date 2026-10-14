"""Create a deployment source ZIP from an allowlist, excluding all local data."""
from datetime import datetime
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def main():
    files = [ROOT / name for name in (
        "manage.py", "requirements.txt", ".python-version", "vercel.json", ".vercelignore")]
    for name in ("config", "tasks", "templates", "static"):
        files.extend(path for path in (ROOT / name).rglob("*") if path.is_file()
                     and "__pycache__" not in path.parts and path.suffix != ".pyc"
                     and not path.name.startswith("test_"))
    forbidden = {".venv", "data", "backups", "change-backups", ".deployment-tools", ".vercel"}
    for path in files:
        relative = path.relative_to(ROOT)
        assert path.is_file() and not forbidden.intersection(relative.parts)
        assert path.name != ".env" and path.suffix not in {".sqlite3", ".db", ".log", ".dump", ".zip", ".xlsx", ".csv"}
    output = ROOT / "dist" / f"dugyeon-vercel-source-{datetime.now():%Y%m%d-%H%M%S-%f}.zip"
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(files)):
            archive.write(path, path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
    print(f"Deployment source package created: {output} ({len(set(files))} files)")


if __name__ == "__main__":
    main()
