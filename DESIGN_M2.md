# Module 2 design draft

Current operators are seqscan, filter, project, limit, external merge
sort, and three joins.

## Iterator interface and schemas

Every operator supports open(), next(), close(), and counters(). A next
call returns one tuple or None at end of stream. Nodes pull through their
immediate children. Projection preserves duplicates. Filters reuse the
extracted Bonus 1 condition parser and predicate compiler and increment
comparisons once per input tuple.

Schemas retain column order, qualification, type, and VARCHAR limit.
Plan shape is checked before catalog construction by the CLI, and
semantic errors are rejected before opening operators. Conditions require
qualified column references. Unknown columns and mixed-type comparisons
are errors even for empty inputs.

Supported node fields are table on seqscan, condition and child on filter,
columns and child on project, n and child on limit, and keys and child on
sort. Sort keys are [qualified_name, asc_or_desc] pairs. The instructor confirmed companion schemas will not be posted. The
limit field is n. Each join accepts left, right, and condition, as in
the module examples.

Limit stops pulling when it has produced n tuples and immediately closes
its child. Limit zero does not read table pages. Closing the chain closes
heap scan generators and releases their pins.

## Shared page accounting

All catalog, table, and temporary file activity shares one pool. During
streaming, one frame's worth of encoded tuple payload is reserved and
resident cache pages are limited to B-1. Unary pipeline stages share
that streaming workspace rather than buffering separate intermediate
relations. Failed filter rows and previously emitted CLI rows are released
before pulling their successors.

A heap scan borrows a view of the pinned data page, avoiding a full encoded
record copy alongside the decoded output tuple. Existing M1 callers still
receive copied record bytes by default.

Frame reservations evict unpinned cache pages when needed. The pool
records the maximum of resident frame count plus reserved working pages.
For the 80-page query at B=4, that maximum is 4. This is a logical encoded
page budget. Python interpreter, schema, frame-map, offset-reference, and
comparison-wrapper overhead are administrative metadata rather than
additional copies of tuple payload. The peak does not assert that the
entire Python process occupies 16 KiB of resident memory.

## Temporary page format and I/O

Temporary runs use the Module 1 temporary-storage interface. Each page has
a two-byte used length and two-byte record count, followed by records with
two-byte lengths. The page's final bytes are zero. Records never straddle
temporary pages.

DiskManager.write_record_page is one logical 4096-byte page write. It
streams field or record fragments to the page, then writes the header and
padding, without allocating a separate 4096-byte output staging array.
The counter increments once for the completed page-write operation.
Temporary pages are read into pool frames through read_page_into.

For child spooling, EncodedTuple borrows one decoded row and streams its
field encodings. It releases that tuple once written. It does not build
an additional full encoded copy. During merging, records are views of
pinned input frames. The temporary-page writer consumes each view before
advancing the input cursor. This avoids references to data that has
already been overwritten by frame reuse.

Temporary-file removal first discards its unpinned frames and retires its
read/write counters into cumulative pool totals. Closing or early LIMIT
removes every run. The cleanup tests include an injected sort-comparison
failure.

## External sorting

The implementation uses the following stages.

1. Stream the child to an unsorted temporary run, then close the child.
   This explicit spool is additional I/O relative to the handout baseline.
2. Pin at most B temporary input pages. Collect references to their
   record views and sort those references with the standard in-memory
   list sort. No second list of decoded or encoded tuples is built.
3. Write each sorted run through the temporary-page streaming writer,
   release its input frames, and continue with the next block.
4. Merge at most B-1 runs at once. A heap stores one head reference per
   run, and each cursor pins one input page. Intermediate merges write
   a new run. A singleton group is carried forward without rewriting it.
5. The final merge yields encoded views directly to next(), which decodes
   one output tuple at a time. The final merge writes no output run.

With fewer than B input pages, a single sorted run streams from its
existing pinned frames. One workspace frame remains for the output
tuple. With exactly B pages, the run is written and its input frames
released before streaming. This conservative boundary case adds a pass
relative to the formula for an in-memory sort.

Sort comparisons count each component key comparison, including
comparisons while generating runs and while managing merge heads.
VARCHAR comparison walks UTF-8 bytes without copying whole string keys.
Equal keys use stable input/run order for deterministic merging.

## I/O attribution

Each node reports its own tuples_out, comparisons, page_reads, and
page_writes. Root totals use the root output count and sum the node I/O
and comparison counts.

Seqscan includes actual directory reads. Catalog preparation occurs before
iterator execution and is excluded from execution counters. Sort snapshots
the cumulative disk counters around its work and subtracts changes already
attributed to its child subtree. Nested-sort tests verify that totals equal
actual changes in the shared disk counters.

## Worked examples and remaining work

The 8-page, B=3 sorter uses three runs of sizes 3,3,2. Its intermediate
merge reads/writes six pages and carries the third run. The final merge
reads eight pages and writes none. These sorting phases reproduce the
handout's 22 reads and 14 writes. The preparatory spool adds eight writes,
and the child scan adds nine reads including its directory page. Total
execution I/O is therefore 31 reads and 22 writes.

The 80-page, B=4 test uses 20 initial runs and four sorting passes.
Initial run generation, intermediate merges, and final streaming read
80+80+72+80 = 312 pages. The preparatory spool plus generated/intermediate
runs write 80+80+80+72 = 312 pages. The child scan adds 81 reads.

The join measurement matrix, crossover plots, and skew measurements are in REPORT_M2.md.

## Joins

Each join consumes the children serially during open to avoid overlapping
their working memory. Block nested-loop spools the two child streams first,
then scans the inner run once per outer block. It is consequently blocking
during preparation and pays extra spool I/O compared with the handout's
direct page-scan model. After preparation it streams results. Every
candidate pair is tested once.

Sort-merge reuses the external sorter on each child and writes sorted
streams to rewindable temporary runs. It marks the first matching inner
record by page and slot. It rewinds to that bookmark for every additional
outer record with the same key, so duplicate groups can exceed memory.
Sorting comparisons and I/O are counted on the sort nodes. The merge
node counts its own key comparisons and temporary I/O.

Grace hash join sends each row to one of B-1 partitions using CRC32 on
the encoded key. The partition writers keep offsets and counts in memory.
They write fragments directly and count a logical page write once its
header is completed. Build partitions fit in the pinned outer pages.
The hash index stores references to those page records and a seeded
checksum. Probing verifies actual key equality to handle collisions.
A build partition that exceeds the available build pages falls back
to block nested-loop on that partition pair.

For one-page output rows the join budget is B-2 build/outer pages,
one inner page, and one tuple workspace page. This also explains why
hash fallback can occur at B-1 build pages. A wider declared output
reserves extra workspace pages, reducing the build/outer capacity.
The current temporary format requires each spooled record to fit a
single page. A wide join can return a row directly if enough workspace
is available, but spooling that row in a later operator may fail.


## Sources consulted and their effect

Graefe's iterator discussion in Section III-B informed the uniform
open/next/close interface and recursive cleanup. In particular, sort
performs blocking work in open and streams its final output through next.
[Graefe, 1994, Volcano](https://people.eecs.berkeley.edu/~prabal/teaching/resources/eecs582/graefe94volcano.pdf)

The CMU sorting notes informed bounded run generation, fan-in of B-1,
and the need to measure pass counts and page I/O separately. The extra
preparatory spool and exactly-B boundary pass are implementation choices
documented above.
[CMU, Sorting and Aggregation Algorithms](https://15445.courses.cs.cmu.edu/spring2026/notes/11-queryI.pdf)

## Grouping and set operations

The plan chooses sort or hash mode for grouping and DISTINCT. Sort mode
orders the child by the group key, spools that order to a temporary run,
and folds each adjacent group into one result. DISTINCT groups on every
column with zero aggregates. An empty global COUNT returns zero. Other
global aggregates on empty input raise an error.

Hash mode partitions the input first. A partition uses an in-memory
aggregate table when its row count times the maximum encoded group
state size fits one page. The estimate charges eight bytes for each
group's row count. Larger partitions use the existing external sorter
and the adjacent-group fold. This conservative rule can spill even
when the actual number of distinct keys is small. The workspace
accounts for encoded tuple payload and excludes Python object overhead,
as in the sort and join measurements.

COUNT and SUM produce signed 64-bit BIGINT output, and AVG produces
REAL with four decimal places in CLI output. MIN and MAX preserve the
input type. Derived numeric fields can pass through temporary files,
sorts, and filters. Table schemas still use the Module 1 storage types.

UNION ALL opens one child at a time and preserves duplicate rows. The
other set operators sort each child on all columns, then merge two
temporary runs. They keep the left column names and widen VARCHAR
limits to the greater declared length from the two inputs. Two pinned
run pages and two workspace pages suffice for the merge. One workspace
holds the duplicate comparison record across page boundaries and one
holds the decoded result. As with joins, spooled records must fit a
temporary page.
