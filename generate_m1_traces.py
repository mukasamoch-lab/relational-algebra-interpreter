"""Write reproducible, read-only page request traces for the M1 report."""

from pathlib import Path
from random import Random


def generate(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    rng = Random(3005)
    patterns = {
        "01_worked": [1, 2, 3, 1, 4, 2],
        "02_loop": list(range(12)) * 60,
        "03_sequential": list(range(80)) * 10,
        "04_uniform": [rng.randrange(512) for _ in range(1000)],
        "05_hot_cold": [
            page for index in range(1000)
            for page in (8 + index if index % 5 == 4 else index % 4,)
        ],
    }
    paths = []
    for name, pages in patterns.items():
        path = directory / (name + ".txt")
        path.write_text("".join(f"{page}\n" for page in pages), encoding="ascii")
        paths.append(path)
    return paths


if __name__ == "__main__":
    generate("traces")
