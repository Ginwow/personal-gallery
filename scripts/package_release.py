"""Package the clean build staging directory, never the live user directory."""
import sys
import zipfile
from pathlib import Path


def package(source: Path, destination: Path) -> None:
    temporary = destination.with_suffix(".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in source.rglob("*"):
                if path.is_file():
                    archive.write(path, (Path("个人画廊") / path.relative_to(source)).as_posix())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    package(Path(sys.argv[1]), Path(sys.argv[2]))
