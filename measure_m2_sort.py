"""Reproduce sort pass counts and the 80-page, four-frame budget check."""

import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory

from catalog import Catalog
from executor import build_plan, totals
from record_codec import encode_record


SCHEMA = [["key", "INT"]] + [[f"p{i}", "VARCHAR", 255] for i in range(15)]
SCHEMA.append(["tail", "VARCHAR", 223])


def measure_sort(pages, frames):
    with TemporaryDirectory() as folder:
        catalog = Catalog(folder, frames=frames)
        info = catalog.create_table("R", SCHEMA)
        heap = catalog.open_heap(info)
        padding = ("x" * 255,) * 15 + ("y" * 223,)
        for key in range(pages, 0, -1):
            heap.insert(encode_record(SCHEMA, (key, *padding)))
        info["num_tuples"] = pages
        info["num_pages"] = heap.num_data_pages()
        catalog.pool.flush_all()
        catalog.save_table(info)
        catalog.close()

        catalog = Catalog(folder, frames=frames)
        root = build_plan({
            "op": "sort", "keys": [["R.key", "asc"]],
            "child": {"op": "seqscan", "table": "R"},
        }, catalog)
        correct = True
        count = 0
        temporary_path = None
        try:
            root.open()
            temporary_path = Path(root.temporary.directory.name)
            while True:
                row = root.next()
                if row is None:
                    break
                count += 1
                correct = correct and row[0] == count
        finally:
            root.close()
        runs = math.ceil(pages / frames)
        theoretical = 1 + (math.ceil(math.log(runs, frames - 1)) if runs > 1 else 0)
        result = {
            "input_data_pages": info["num_pages"], "frames": frames,
            "initial_runs": root.initial_runs, "formula_passes": theoretical,
            "measured_passes": root.passes, "peak_accounted_frames": root.peak_frames,
            "rows": count, "correct_order": correct and count == pages,
            "execution": totals(root), "sort": root.counters(),
            "no_pins_left": all(frame.pin_count == 0 for frame in catalog.pool.frames.values()),
            "temporary_files_removed": not temporary_path.exists(),
        }
        assert result["correct_order"] and result["no_pins_left"]
        assert result["temporary_files_removed"] and root.peak_frames <= frames
        catalog.close()
        return result


if __name__ == "__main__":
    results = [measure_sort(pages, frames) for pages, frames in (
        (1, 4), (4, 4), (8, 3), (10, 4), (20, 8), (33, 4), (80, 4),
    )]
    print(json.dumps(results, indent=2))
