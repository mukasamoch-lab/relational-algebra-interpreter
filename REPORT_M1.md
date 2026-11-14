# Module 1 measurements

The instructor approved self-generated test inputs. The trace generator
and its inputs are included. Run `python generate_m1_traces.py`, then
`python measure_m1.py --traces traces/01_worked.txt traces/02_loop.txt traces/03_sequential.txt traces/04_uniform.txt traces/05_hot_cold.txt`
to reproduce the measurements. Each policy run starts with an empty pool.

## Capacity

Each fixture was CSV-loaded with one more record than the computed first
page capacity. All string values used the stated UTF-8 byte length.

| Schema and values | Record bytes L | floor(4088/(L+4)) | Measured on first page |
|---|---|---|---|
| Three INT columns, (1,2,3) | 12 | 255 | 255 |
| INT,VARCHAR(5),INT, (42,bob,7) | 13 | 240 | 240 |
| VARCHAR(200), 200 ASCII x characters | 202 | 19 | 19 |

There is no gap. The VARCHAR limit describes a maximum, while its actual
encoded byte length determines capacity. A 200-byte value consumes two
length bytes in addition to its text.

## Cold scan of 500 data pages

The fixture contains 500 records of 4084 bytes, one per data page, and
one directory page. Each run starts with a new empty pool. The scan
consumes the generator and counts rows without collecting the table.

| Frames B | Rows | Reads | Writes |
|---|---|---|---|
| 2 | 500 | 501 | 0 |
| 8 | 500 | 501 | 0 |
| 64 | 500 | 501 | 0 |

Each page is read once. The extra read is the directory page. Increasing
the cache size changes evictions but does not reduce the reads in a
single cold sequential scan.

## Policy checks

The published six-fetch worked example gives these exact values.

| Policy | B | Hits | Misses | Evictions | Writes |
|---|---|---|---|---|---|
| LRU | 3 | 1 | 5 | 2 | 0 |
| CLOCK | 3 | 2 | 4 | 1 | 0 |

A synthetic trace matching the described sequential pattern, page IDs
0 through 79 repeated ten times, at B=64 produced 0 hits, 800 misses,
736 evictions, and 0 writes under LRU. This is trace 03 in the table below.

The reuse distance exceeds the available frames, so a page is evicted
before the next scan reaches it. Each new scan then evicts the pages
that would be useful later in that scan.

The measured hit rates show the working-set thresholds of the 12-page
loop and 80-page scan. Both policies suffer flooding when those working
sets exceed the pool. I would ship CLOCK for its reference-bit mechanism
and comparable hit rates on these traces.

## Trace workload and hit rates

- 01 repeats the six requests from the worked example once.
- 02 cycles through pages 0–11 sixty times.
- 03 cycles through pages 0–79 ten times.
- 04 makes 1,000 uniform requests to pages 0–511 using Python Random with seed 3005.
- 05 makes 1,000 requests. Every fifth request visits a distinct cold page.
  The other requests cycle among four hot pages.

Page IDs are zero based. Every fetch is clean and immediately unpinned.
Hit rate is hits divided by requests.

| Trace | Frames | LRU | CLOCK |
|---|---:|---:|---:|
| 01_worked | 4 | 33.33% | 33.33% |
| 01_worked | 16 | 33.33% | 33.33% |
| 01_worked | 64 | 33.33% | 33.33% |
| 01_worked | 256 | 33.33% | 33.33% |
| 02_loop | 4 | 0.00% | 0.00% |
| 02_loop | 16 | 98.33% | 98.33% |
| 02_loop | 64 | 98.33% | 98.33% |
| 02_loop | 256 | 98.33% | 98.33% |
| 03_sequential | 4 | 0.00% | 0.00% |
| 03_sequential | 16 | 0.00% | 0.00% |
| 03_sequential | 64 | 0.00% | 0.00% |
| 03_sequential | 256 | 90.00% | 90.00% |
| 04_uniform | 4 | 1.40% | 1.40% |
| 04_uniform | 16 | 4.20% | 4.20% |
| 04_uniform | 64 | 14.10% | 13.50% |
| 04_uniform | 256 | 43.60% | 44.20% |
| 05_hot_cold | 4 | 59.70% | 39.80% |
| 05_hot_cold | 16 | 79.60% | 79.50% |
| 05_hot_cold | 64 | 79.60% | 79.50% |
| 05_hot_cold | 256 | 79.60% | 79.60% |

## Reading

The PostgreSQL page-layout documentation informed the stable slot design,
which is the reason capacity must account for four bytes per slot.
[PostgreSQL page layout](https://www.postgresql.org/docs/18/storage-page-layout.html)

The CMU buffer-pool notes informed the sequential flooding explanation and
the choice to report cold scan cost separately from cache hit rates.
[CMU buffer-pool notes](https://15445.courses.cs.cmu.edu/spring2026/notes/04-bufferpool.pdf)
