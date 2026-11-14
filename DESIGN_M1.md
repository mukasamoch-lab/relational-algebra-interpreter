# Module 1 design

## Files and records

Each table uses its own `<table>.db`. `catalog.db` stores the catalog.
Every page is 4096 bytes and integer fields use little-endian encoding.
An INT is a signed 32-bit integer. A VARCHAR has a two-byte byte length,
followed by exactly that many UTF-8 bytes. Declared lengths are checked
after UTF-8 encoding.

## Data and directory pages

The required data-page header contains the page ID at offset 0 (4 bytes),
the slot count at offset 4 (2 bytes), and free_end at offset 6 (2 bytes).
Each slot has a two-byte record offset and a two-byte record length.
Data grows backward from offset 4096. The initial free space is 4088 bytes.
An insertion consumes the record length plus four slot bytes.

The following is the actual layout from the test fixture after inserting
13, 20, and 13 bytes and deleting slot 1.

| Byte offset | Length | Stored value |
|---|---|---|
| 0 | 4 | page_id = 0 |
| 4 | 2 | slot_count = 3 |
| 6 | 2 | free_end = 4050 |
| 8 | 4 | slot 0 = (4083, 13) |
| 12 | 4 | slot 1 = (0, 0) |
| 16 | 4 | slot 2 = (4050, 13) |
| 20 | 4030 | unused contiguous space |
| 4050 | 13 | live record in slot 2 |
| 4063 | 20 | deleted record bytes |
| 4083 | 13 | live record in slot 0 |

Compaction is omitted. A deleted slot remains in the directory and its
bytes remain unavailable. Slot IDs stay stable. Directory free-space
values therefore stay correct after deletion without a numerical change.

Directory pages have the required ten-byte header and six-byte entries.
Each entry has a four-byte data page ID followed by a two-byte free-space
count. A directory holds 681 entries. Insert uses first fit in directory
order. A full last directory links to a newly allocated directory page.
The 683-page regression test crosses this boundary.

The maximum insertable record is 4084 bytes because the empty data page
needs four bytes for its slot. The spec's separate 4088-byte record limit
does not include that slot overhead. The loader reports the table and line
when a record cannot fit.

## Buffer pool

A shared pool keys frames by file identity and page ID. The original
single-file API remains usable. A file view delegates fetch, allocation,
and unpin to the same pool. Catalog and table activity therefore share
one capacity and one replacement policy.

Frames contain page bytes, pin count, dirty state, and CLOCK reference
state. LRU access order is an OrderedDict. CLOCK has a stable slot list
and a hand. Cache hits update LRU order and set the reference bit.
Victim selection skips pinned frames. A dirty victim is written before
its frame is reused. Reads fill the existing bytearray with readinto,
and new pages reuse and clear an available victim buffer. The pool does
not allocate an extra full page for each replacement.

Every successful disk read operation increments reads once, including
read_page_into. Writes increment writes once. Allocation extends the
file and does not issue a measured read or write_page call. new_page
does not count as a fetch hit or miss. Evictions count fetch misses
that replace resident pages, following the spec's counter definition.
flush_all writes each dirty frame once and clears its dirty flag.

A heap scan pins at most one directory and one data frame. It yields
one record at a time. The generator's finally blocks release pins when
closed, including when LIMIT stops early. Callers stopping early must
close the scan. The CLI uses contextlib.closing to enforce that rule.

## Catalog page format

The catalog uses append-only JSON snapshots. Each snapshot describes
one table. The latest published root for a name wins. Old snapshots
remain available on disk. This trades growing catalog scan cost for a
small format with no in-place resizing of schemas or future statistics.

| Offset | Length | Field |
|---|---|---|
| 0 | 4 | magic CAT1 |
| 4 | 4 | this page ID |
| 8 | 4 | next continuation page ID or 0xFFFFFFFF |
| 12 | 2 | payload byte count |
| 14 | 2 | flags, 1 for a snapshot root, 0 for a continuation |
| 16 | up to 4080 | UTF-8 JSON fragment |

Continuation pages are appended in reverse order and flushed before
the snapshot root is published. Reassembly starts at a root and follows
its links. Schemas larger than one page work. The descriptor includes
name, ordered schema, file, first_page, num_pages, num_tuples, and a
statistics object reserved for Module 4.

num_pages counts data pages, excluding directory pages. first_page is
the initial directory at page 0. A table file exists when create_table
returns. The reserved table name catalog avoids a filename collision.

## CSV and command boundaries

The loader uses the standard-library CSV reader with strict CSV errors.
It validates each field before inserting its record. It preserves
duplicates. Invalid values name their table, line, and column. A later
bad row stops loading, while earlier valid rows and catalog counts are
flushed. Catalog counts are updated once per load, including a partially
successful load.

Catalog.open_table returns a tuple adapter that encodes inserts, decodes
gets and scans, and updates catalog counts after insertion or deletion.
The heap's lower-level record API is also used by tests and future
temporary-file operators. Callers modifying a named table directly
through this API must update its catalog metadata as the loader does.

The storage launcher supplies create-table, load, scan, layout, and
trace. JSON scan output is written row by row. The trace reader makes
two passes over its text input so the trace is not materialized in a
list. Its initial zero-page file preparation is outside measured I/O.

The current implementation supports sequential command execution. It
does not implement locking or transaction recovery for concurrent
writers or interrupted disk writes.

## Reading and design decisions

PostgreSQL's page-layout documentation explains the indirection from a
record ID to a slot. It reinforced the decision to leave slot numbers
stable when deleting records. This project's exact bytes still follow
the assignment format.
[PostgreSQL page layout](https://www.postgresql.org/docs/18/storage-page-layout.html)

The CMU buffer-pool notes distinguish pinning from replacement and
describe sequential flooding. They informed the test that a fully
pinned pool rejects both fetch and allocation, and the decision to
measure repeated sequential scans for both replacement policies.
[CMU buffer-pool notes](https://15445.courses.cs.cmu.edu/spring2026/notes/04-bufferpool.pdf)
