"""Reproduce join comparisons using one encoded tuple per data page."""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import math

from catalog import Catalog
from executor import JOINS, build_plan, totals
from record_codec import encode_record


BUDGETS = (4, 16, 64, 256)
SIZES = ((8, 12), (24, 32), (64, 96))
SCHEMA = [["k", "INT"]] + [[f"p{i}", "VARCHAR", 255] for i in range(15)] + [["tail", "VARCHAR", 223]]
PADDING = ("x" * 255,) * 15 + ("y" * 223,)


def dataset(directory, sizes, skew):
    catalog = Catalog(directory, frames=4)
    for name, size in zip(("R", "S"), sizes):
        info = catalog.create_table(name, SCHEMA)
        heap = catalog.open_heap(info)
        for index in range(size):
            key = 0 if skew and index < size * 2 // 5 else index + 1
            heap.insert(encode_record(SCHEMA, (key, *PADDING)))
        info.update(num_pages=heap.num_data_pages(), num_tuples=size)
        catalog.pool.flush_all()
        catalog.save_table(info)
    catalog.close()


def measure(directory, sizes, frames, op, skew=False):
    m, n = sizes
    with CatalogSession(directory, frames) as catalog:
        root = build_plan({
            "op": op, "condition": "R.k = S.k",
            "left": {"op": "seqscan", "table": "R"},
            "right": {"op": "seqscan", "table": "S"},
        }, catalog)
        root.open()
        try:
            while root.next() is not None:
                pass
        finally:
            root.close()
        actual = totals(root)
        expected = min(m, n)
        if skew:
            expected = (m * 2 // 5) * (n * 2 // 5) + min(m, n) - max(m * 2 // 5, n * 2 // 5)
        assert actual["tuples_out"] == expected
        assert all(not frame.pin_count for frame in catalog.pool.frames.values())
        assert catalog.pool.peak_accounted_frames <= frames

        def sort_cost(pages):
            runs = math.ceil(pages / frames)
            passes = 1 + math.ceil(math.log(runs, frames - 1)) if runs > 1 else 1
            return pages * (2 * passes - 1)

        formula = {
            "nested_loop_join": m + math.ceil(m / (frames - 2)) * n,
            "hash_join": 3 * (m + n),
            "sort_merge_join": sort_cost(m) + sort_cost(n) + m + n,
        }[op]
        return dict(m=m, n=n, frames=frames, op=op, skew=skew, formula=formula,
                    **actual, partitions=root.partition_pages,
                    overflow=root.overflow_partitions, peak=catalog.pool.peak_accounted_frames)


class CatalogSession:
    def __init__(self, directory, frames):
        self.catalog = Catalog(directory, frames=frames)

    def __enter__(self):
        return self.catalog

    def __exit__(self, *exc):
        self.catalog.close()


def run():
    results = []
    with TemporaryDirectory() as folder:
        for sizes in SIZES:
            path = Path(folder) / f"{sizes[0]}-{sizes[1]}"
            dataset(path, sizes, False)
            for frames in BUDGETS:
                for op in sorted(JOINS):
                    results.append(measure(path, sizes, frames, op))
        # Exact 40% frequency on each side, with a matching uniform control.
        sizes = (40, 60)
        for skew in (False, True):
            path = Path(folder) / f"skew-{skew}"
            dataset(path, sizes, skew)
            results.append(measure(path, sizes, 16, "hash_join", skew))
    return results


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
