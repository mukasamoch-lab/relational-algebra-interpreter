"""Exact catalog statistics using a single heap scan and external sorting."""

from contextlib import closing
from math import ceil

from executor import Operator, SeqScan
from external_sort import Sort
from record_codec import decode_record
from temp_storage import RunCursor, TemporaryRuns


class ScanRun(Operator):
    def __init__(self, run, pool, schema):
        super().__init__("statistics_spool", schema)
        self.run = run
        self.pool = pool

    def open(self):
        super().open()
        self.cursor = RunCursor(self.run, self.pool)

    def _next(self):
        record = self.cursor.next()
        return None if record is None else decode_record(self.schema, record)

    def close(self):
        if self.opened:
            self.cursor.close()
        super().close()


def analyze(catalog, table, buckets=20):
    if buckets < 1 or catalog.pool.capacity < 3:
        raise ValueError("ANALYZE requires positive buckets and at least three frames")
    info = catalog.get_table(table)
    scan = SeqScan(catalog, info)
    total_bytes = 0

    def records():
        nonlocal total_bytes
        with closing(scan.heap.scan()) as rows:
            for _, record in rows:
                total_bytes += len(record)
                yield record

    with closing(TemporaryRuns(catalog.pool)) as temp:
        with closing(records()) as source:
            run = temp.write(source)
        stats = {
            "n_tuples": run.rows, "n_pages": scan.heap.num_data_pages(),
            "width": total_bytes / run.rows if run.rows else 0,
            "buckets": buckets, "columns": {},
        }
        for index, (name, *_) in enumerate(info["schema"]):
            histogram = []
            ndv = 0
            first = prev = low = None
            bucket_count = distinct = 0
            k = min(run.rows, buckets)
            if k:
                replay = ScanRun(run, catalog.pool, scan.schema)
                with closing(Sort(replay, [[scan.schema[index][0], "asc"]], catalog)) as sort:
                    sort.open()
                    for i in range(1, run.rows + 1):
                        value = sort.next()[index]
                        if i == 1:
                            first = value
                        if value != prev:
                            ndv += 1
                        if bucket_count == 0:
                            low = value
                        if bucket_count == 0 or value != prev:
                            distinct += 1
                        prev = value
                        bucket_count += 1
                        if i == ceil((len(histogram) + 1) * run.rows / k):
                            histogram.append({"min": low, "max": value,
                                              "count": bucket_count, "ndv": distinct})
                            bucket_count = distinct = 0
            stats["columns"][name] = {"min": first, "max": prev, "ndv": ndv,
                                      "hist": histogram}
    if info["statistics"] != stats:
        catalog.save_table({**info, "statistics": stats})
    return stats
