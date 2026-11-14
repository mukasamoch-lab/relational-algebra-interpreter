"""Disk-backed duplicate elimination, grouping, and set operations."""

from contextlib import contextmanager
from hashlib import blake2b
import struct

from executor import Operator, node_error, nodes
from external_sort import Sort
from record_codec import FIXED_TYPES, decode_record
from slotted_page import PAGE_SIZE
from temp_storage import EncodedTuple, PartitionWriter, RunCursor, TemporaryRuns


def compatible(op, left, right):
    if [c[1] for c in left.schema] != [c[1] for c in right.schema]:
        node_error(op, "inputs must have matching column counts and types")
    return [
        [a[0], a[1], max(a[2], b[2])] if a[1] == "VARCHAR" else list(a)
        for a, b in zip(left.schema, right.schema)
    ]


def width(schema):
    return sum(
        struct.calcsize(FIXED_TYPES[c[1]]) if c[1] in FIXED_TYPES else 2 + c[2]
        for c in schema
    )


class Concat(Operator):
    def __init__(self, left, right):
        super().__init__("union_all", compatible("union_all", left, right))
        self.inputs = left, right
        self.pool = left.pool

    def open(self):
        super().open()
        self.side = 0
        for child in self.inputs:
            for operator in nodes(child):
                for key in operator.counts:
                    operator.counts[key] = 0
        try:
            self.inputs[0].open()
        except BaseException:
            self.close()
            raise

    def _next(self):
        if self.side == 2:
            return None
        row = self.inputs[self.side].next()
        if row is not None:
            return row
        self.inputs[self.side].close()
        self.side += 1
        if self.side == 2:
            return None
        self.inputs[1].open()
        return self._next()

    def close(self):
        for child in self.inputs:
            child.close()
        super().close()


class RunInput(Operator):
    """Expose a temporary run as an input for the existing external sorter."""

    def __init__(self, pool, schema, run):
        super().__init__("run_scan", schema)
        self.pool = pool
        self.run = run
        self.cursor = None

    def open(self):
        super().open()
        self.cursor = RunCursor(self.run, self.pool)

    def _next(self):
        record = self.cursor.next()
        return None if record is None else decode_record(self.schema, record)

    def close(self):
        if self.cursor is not None:
            self.cursor.close()
            self.cursor = None
        super().close()


class RunOperator(Operator):
    def __init__(self, op, schema, inputs, catalog):
        super().__init__(op, schema)
        self.inputs = tuple(inputs)
        self.pool = catalog.pool
        self.catalog = catalog
        self.temporary = None
        self.output = None

    @contextmanager
    def io(self):
        before = self.pool.stats()
        child_before = self.child_io()
        try:
            yield
        finally:
            after = self.pool.stats()
            child_after = self.child_io()
            for key, metric in (("page_reads", "reads"), ("page_writes", "writes")):
                self.counts[key] += after[metric] - before[metric] - child_after[key] + child_before[key]

    def child_io(self):
        return {
            key: sum(op.counts[key] for child in self.inputs for op in nodes(child))
            for key in ("page_reads", "page_writes")
        }

    def open(self):
        super().open()
        for child in self.inputs:
            for operator in nodes(child):
                for key in operator.counts:
                    operator.counts[key] = 0
        try:
            self.temporary = TemporaryRuns(self.pool)
            with self.io():
                self.output = self.prepare()
        except BaseException:
            self.close()
            raise

    def spool(self, child):
        def records():
            while (row := child.next()) is not None:
                yield EncodedTuple(child.schema, row)
                row = None

        child.open()
        try:
            with self.pool.working_memory(1):
                return self.temporary.write(records())
        finally:
            child.close()

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

    def _next(self):
        with self.io(), self.pool.working_memory(self.workspace):
            return next(self.output, None)


class Group(RunOperator):
    def __init__(self, op, child, keys, aggregates, mode, catalog):
        names = [c[0] for c in child.schema]
        keys = names if keys is None else keys
        if len(set(keys)) != len(keys):
            node_error(op, "duplicate group key")
        for key in keys:
            if key not in names:
                node_error(op, f"unknown column {key!r}")
        self.keys = keys
        self.key_indices = [names.index(key) for key in keys]
        self.aggregates = []
        schema = [child.schema[index] for index in self.key_indices]
        for function, name in aggregates:
            if function not in ("count", "sum", "avg", "min", "max"):
                node_error(op, f"unknown aggregate {function!r}")
            if name == "*" and function == "count":
                index = None
            elif name in names:
                index = names.index(name)
            else:
                node_error(op, f"invalid aggregate column {name!r}")
            if function in ("sum", "avg") and child.schema[index][1] not in ("INT", "BIGINT"):
                node_error(op, f"{function} requires an integer column")
            output_name = f"{function}_{'star' if index is None else name.replace('.', '_')}"
            if function in ("min", "max"):
                schema.append([output_name, *child.schema[index][1:]])
            else:
                schema.append([output_name, "REAL" if function == "avg" else "BIGINT"])
            self.aggregates.append((function, index))
        if len({column[0] for column in schema}) != len(schema):
            node_error(op, "duplicate output column")
        self.source = child
        self.mode = mode
        if mode == "sort" and keys:
            child = Sort(child, [[key, "asc"] for key in keys], catalog)
        super().__init__(op, schema, [child], catalog)
        # One decoded input plus one group state and output.
        self.workspace = max(2, (width(self.source.schema) + width(schema) + PAGE_SIZE - 1) // PAGE_SIZE)

    def key(self, row):
        return tuple(row[index] for index in self.key_indices)

    def update(self, values, row):
        for index, (function, column) in enumerate(self.aggregates):
            old = values[index]
            if function == "count":
                values[index] = (old or 0) + 1
            elif function in ("sum", "avg"):
                values[index] = (old or 0) + row[column]
            else:
                value = row[column]
                if old is not None:
                    self.counts["comparisons"] += 1
                if old is None or (function == "min" and value < old) or (
                    function == "max" and value > old
                ):
                    values[index] = value

    def finish(self, key, values, count):
        result = []
        for (function, _), value in zip(self.aggregates, values):
            if count == 0 and function != "count":
                node_error(self.op, f"{function} is undefined on empty input")
            result.append(value / count if function == "avg" else (0 if value is None else value))
        return key + tuple(result)

    def prepare(self):
        if self.workspace >= self.pool.capacity:
            node_error(self.op, "insufficient frames for grouping workspace")
        if self.mode == "sort" or not self.keys:
            run = self.spool(self.inputs[0])
            return self.sorted_groups(run)
        writers = [PartitionWriter(self.temporary.new_run()) for _ in range(self.pool.capacity - 1)]
        child = self.source
        child.open()
        try:
            with self.pool.working_memory(1):
                while (row := child.next()) is not None:
                    digest = blake2b(repr(self.key(row)).encode("utf-8"), digest_size=8).digest()
                    writers[int.from_bytes(digest, "little") % len(writers)].append(
                        EncodedTuple(child.schema, row)
                    )
                    row = None
        finally:
            child.close()
        partitions = []
        state_width = max(1, width(self.schema) + 8)
        for writer in writers:
            writer.finish_page()
            run = writer.run
            if not run.rows:
                continue
            if run.rows * state_width <= PAGE_SIZE:
                partitions.append((run, True))
            else:
                scan = RunInput(self.pool, self.source.schema, run)
                sorter = Sort(scan, [[key, "asc"] for key in self.keys], self.catalog)
                partitions.append((self.spool(sorter), False))
                self.counts["comparisons"] += sorter.counts["comparisons"]
                self.temporary.remove(run)
        return self.partition_groups(partitions)

    def partition_groups(self, partitions):
        for run, hashed in partitions:
            yield from (self.hash_groups(run) if hashed else self.sorted_groups(run))

    def sorted_groups(self, run):
        cursor = RunCursor(run, self.pool)
        try:
            key, count = (), 0
            values = [None] * len(self.aggregates)
            while (record := cursor.next()) is not None:
                row = decode_record(self.source.schema, record)
                current = self.key(row)
                if count:
                    self.counts["comparisons"] += 1
                    if key != current:
                        yield self.finish(key, values, count)
                        count = 0
                        values = [None] * len(self.aggregates)
                key = current
                self.update(values, row)
                count += 1
                row = None
            if count or not self.keys:
                yield self.finish(key, values, count)
        finally:
            cursor.close()

    def hash_groups(self, run):
        cursor = RunCursor(run, self.pool)
        groups = {}
        try:
            while (record := cursor.next()) is not None:
                row = decode_record(self.source.schema, record)
                key = self.key(row)
                self.counts["comparisons"] += 1
                values, count = groups.get(key, (None, 0))
                if values is None:
                    values = [None] * len(self.aggregates)
                self.update(values, row)
                groups[key] = values, count + 1
                row = None
        finally:
            cursor.close()
        for key, (values, count) in groups.items():
            yield self.finish(key, values, count)


class SetOperation(RunOperator):
    def __init__(self, op, left, right, catalog):
        schema = compatible(op, left, right)
        inputs = [
            Sort(child, [[column[0], "asc"] for column in child.schema], catalog)
            for child in (left, right)
        ]
        super().__init__(op, schema, inputs, catalog)
        self.workspace = 2

    def prepare(self):
        if self.workspace + 2 > self.pool.capacity:
            node_error(self.op, "insufficient frames for set-operation workspace")
        return self.merge(*(self.spool(child) for child in self.inputs))

    def compare(self, left, right):
        self.counts["comparisons"] += 1
        return self.inputs[0].compare(left, right, count=False)

    def advance(self, cursor, previous):
        while (record := cursor.next()) is not None:
            if self.compare(record, previous) != 0:
                return record
        return None

    def merge(self, left, right):
        a_cursor, b_cursor = RunCursor(left, self.pool), RunCursor(right, self.pool)
        try:
            a, b = a_cursor.next(), b_cursor.next()
            while a is not None or b is not None:
                if a is not None and b is not None:
                    order = self.compare(a, b)
                else:
                    order = -1 if b is None else 1
                selected = a if order <= 0 else b
                if (
                    self.op == "union"
                    or (self.op == "intersect" and order == 0)
                    or (self.op == "except" and order < 0)
                ):
                    yield decode_record(self.schema, selected)
                # A cursor may replace its page while skipping duplicates.
                previous = bytes(selected)
                if order <= 0:
                    a = self.advance(a_cursor, previous)
                if order >= 0:
                    b = self.advance(b_cursor, previous)
                previous = None
        finally:
            a_cursor.close()
            b_cursor.close()
