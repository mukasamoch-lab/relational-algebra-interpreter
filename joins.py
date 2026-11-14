"""Page-bounded block nested-loop, sort-merge, and Grace hash joins."""

from contextlib import contextmanager
import struct
import zlib

from executor import Operator, node_error, nodes
from external_sort import Sort
from ra_engine import Column, compile_condition
from ra_parser import parse_condition
from record_codec import FIXED_TYPES, decode_record
from slotted_page import PAGE_SIZE
from temp_storage import EncodedTuple, PartitionWriter, RunCursor, TemporaryRuns, page_records


def field(record, schema, index):
    offset = 0
    for column_index, column in enumerate(schema):
        if column[1] in FIXED_TYPES:
            fmt = FIXED_TYPES[column[1]]
            if column_index == index:
                return struct.unpack_from(fmt, record, offset)[0]
            offset += struct.calcsize(fmt)
        else:
            length = struct.unpack_from("<H", record, offset)[0]
            offset += 2
            if column_index == index:
                return bytes(record[offset:offset + length])
            offset += length


def hash_key(value, seed=0):
    if isinstance(value, (int, float)):
        numerator, denominator = value.as_integer_ratio()
        data = f"{numerator}/{denominator}".encode("ascii")
    else:
        data = value
    return zlib.crc32(data, seed)


class Join(Operator):
    def __init__(self, op, left, right, condition, catalog):
        super().__init__(op, left.schema + right.schema)
        self.pool = catalog.pool
        self.inputs = left, right
        names = [column[0] for column in self.schema]
        if len(set(names)) != len(names):
            node_error(op, "duplicate qualified columns")
        tree = parse_condition(condition)
        columns = tuple(
            Column(name.rpartition(".")[2], name.rpartition(".")[0],
                   "number" if kind in FIXED_TYPES else "string")
            for name, kind, *options in self.schema
        )
        self.predicate = compile_condition(tree, columns)
        if op != "nested_loop_join":
            if tree.kind != "Eq" or any(node.kind != "Attr" for node in tree.children):
                node_error(op, "requires equality between columns on opposite inputs")
            keys = [node.label for node in tree.children]
            left_names = names[:len(left.schema)]
            right_names = names[len(left.schema):]
            if keys[0] in right_names:
                keys.reverse()
            if keys[0] not in left_names or keys[1] not in right_names:
                node_error(op, "requires qualified join keys on opposite inputs")
            self.indices = left_names.index(keys[0]), right_names.index(keys[1])
            if op == "sort_merge_join":
                self.inputs = tuple(
                    Sort(child, [[key, "asc"]], catalog)
                    for child, key in zip(self.inputs, keys)
                )
        width = sum(struct.calcsize(FIXED_TYPES[c[1]]) if c[1] in FIXED_TYPES else 2 + c[2] for c in self.schema)
        self.workspace = (width + PAGE_SIZE - 1) // PAGE_SIZE
        self.block_pages = self.pool.capacity - self.workspace - 1
        self.temporary = None
        self.output = None
        self.partition_pages = []
        self.overflow_partitions = 0

    def _child_io(self, key):
        return sum(operator.counts[key] for child in self.inputs for operator in nodes(child))

    @contextmanager
    def _io(self):
        before = self.pool.stats()
        child_before = [self._child_io(key) for key in ("page_reads", "page_writes")]
        try:
            yield
        finally:
            after = self.pool.stats()
            for key, metric, old in zip(
                ("page_reads", "page_writes"), ("reads", "writes"), child_before
            ):
                self.counts[key] += after[metric] - before[metric] - self._child_io(key) + old

    def _records(self, child):
        while True:
            row = child.next()
            if row is None:
                return
            yield EncodedTuple(child.schema, row)
            row = None

    def _prepare(self, child, side):
        child.open()
        try:
            with self.pool.working_memory(1):
                records = self._records(child)
                if self.op != "hash_join":
                    return self.temporary.write(records)
                writers = [PartitionWriter(self.temporary.new_run())
                           for _ in range(self.pool.capacity - 1)]
                for record in records:
                    key = record.row[self.indices[side]]
                    if isinstance(key, str):
                        key = key.encode("utf-8")
                    writers[hash_key(key) % len(writers)].append(record)
                for writer in writers:
                    writer.finish_page()
                return [writer.run for writer in writers]
        finally:
            child.close()

    def open(self):
        if self.block_pages < 1:
            node_error(self.op, "frame budget is too small for both inputs and output")
        super().open()
        self.overflow_partitions = 0
        self.partition_pages = []
        try:
            self.temporary = TemporaryRuns(self.pool)
            with self._io():
                left = self._prepare(self.inputs[0], 0)
                right = self._prepare(self.inputs[1], 1)
            if self.op == "nested_loop_join":
                self.output = self._block_join(left, right)
            elif self.op == "sort_merge_join":
                self.output = self._merge_join(left, right)
            else:
                self.partition_pages = [(a.pages, b.pages) for a, b in zip(left, right)]
                self.output = self._hash_join(left, right)
        except BaseException:
            self.close()
            raise

    def _next(self):
        with self._io(), self.pool.working_memory(self.workspace):
            return next(self.output, None)

    def close(self):
        if self.output is not None:
            self.output.close()
            self.output = None
        for child in self.inputs:
            child.close()
        if self.temporary is not None:
            self.temporary.close()
            self.temporary = None
        super().close()

    @contextmanager
    def _block(self, run, start, end):
        frames = []
        try:
            for page_id in range(start, end):
                frames.append(self.pool.fetch_page(page_id, disk=run.disk))
            yield frames
        finally:
            for frame in frames:
                self.pool.unpin_page(frame.page_id, disk=run.disk)

    def _row(self, left, right):
        return (decode_record(self.inputs[0].schema, left)
                + decode_record(self.inputs[1].schema, right))

    def _key(self, record, side):
        return field(record, self.inputs[side].schema, self.indices[side])

    def _block_join(self, left, right):
        if not right.rows:
            return
        for start in range(0, left.pages, self.block_pages):
            with self._block(left, start, min(start + self.block_pages, left.pages)) as frames:
                inner = RunCursor(right, self.pool)
                try:
                    while (record := inner.next()) is not None:
                        for frame in frames:
                            for outer in page_records(frame):
                                self.counts["comparisons"] += 1
                                row = self._row(outer, record)
                                if self.predicate(row):
                                    yield row
                                row = None
                finally:
                    inner.close()

    def _compare(self, left, right):
        self.counts["comparisons"] += 1
        return (left > right) - (left < right)

    def _merge_join(self, left, right):
        outer, inner = RunCursor(left, self.pool), RunCursor(right, self.pool)
        try:
            a, b = outer.next(), inner.next()
            while a is not None and b is not None:
                key = self._key(a, 0)
                comparison = self._compare(key, self._key(b, 1))
                if comparison < 0:
                    a = outer.next()
                elif comparison > 0:
                    b = inner.next()
                else:
                    mark = inner.position()
                    while True:
                        yield self._row(a, b)
                        b = inner.next()
                        while b is not None and self._compare(key, self._key(b, 1)) == 0:
                            yield self._row(a, b)
                            b = inner.next()
                        a = outer.next()
                        if a is None or self._compare(self._key(a, 0), key) != 0:
                            break
                        b = inner.seek(mark)
        finally:
            outer.close()
            inner.close()

    def _hash_join(self, left, right):
        for build, probe in zip(left, right):
            if not build.rows or not probe.rows:
                continue
            if build.pages > self.block_pages:
                self.overflow_partitions += 1
                yield from self._block_join(build, probe)
                continue
            with self._block(build, 0, build.pages) as frames:
                buckets = {}
                for frame in frames:
                    for record in page_records(frame):
                        code = hash_key(self._key(record, 0), seed=1)
                        buckets.setdefault(code, []).append(record)
                cursor = RunCursor(probe, self.pool)
                try:
                    while (record := cursor.next()) is not None:
                        key = self._key(record, 1)
                        for match in buckets.get(hash_key(key, seed=1), ()):
                            if self._compare(self._key(match, 0), key) == 0:
                                yield self._row(match, record)
                finally:
                    cursor.close()
                    buckets.clear()
