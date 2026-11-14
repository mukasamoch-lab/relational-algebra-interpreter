# Module 4 optimizer design

## Statistics

ANALYZE reads live records from the heap once. It writes an encoded spool
and computes width from the raw byte lengths. It replays the spool once
per column through Module 2's external sorter. The stream of sorted values
provides the global distinct count and exact extrema, while a bucket
closes at position ceil(j × n / k) for j from 1 through k. Empty tables
have no buckets. A duplicate can straddle a boundary. Catalog snapshots
store the statistics alongside tuple and page counts. Repeating ANALYZE
on unchanged data leaves the snapshot unchanged. INSERT, DELETE and LOAD
invalidate the statistics. Temporary disk space trades off against memory
usage and repeated sorts.

## Estimation and cost

Equality sums the bucket density for every range containing the literal.
Ranges sum full buckets and interpolate numeric boundary buckets. For a
text value inside a bucket the fraction is one half, capped only by the
final selectivity clamp. The estimator uses independent AND/OR formulas,
complements NOT and inequality, and estimates a cross-input equality
using the larger NDV. A non-equality join conjunct keeps 10%. Unanalyzed
columns default to NDV 10 and a one-third range fraction. Base table row
and page counts still come from the catalog. All modeled cardinalities
are at least one. After rounding, separate filters can differ slightly
from one combined estimate. The estimator approximates group count by
the product of grouping NDVs capped at input rows. DISTINCT keeps the
input cardinality as an upper bound. Set estimates are coarse, and set
nodes discard column histograms. Estimates use marginal distributions
after filtering and ignore correlations between columns.

The cost printed at a node includes its children. Sequential scan uses
catalog data pages. Filters, projections, renames and limits add zero.
Page estimates for intermediates are ceil(rows × width / 4096). With
ANALYZE, average encoded table width is apportioned across columns by
declared widths. Thus a projected column's width is approximate. Sort
uses M × (1 + 2 × merge passes), where pass zero makes ceil(M/B) runs and
merge fan-in is B−1. Group and DISTINCT use sort mode. Block nested loop
adds M + ceil(M / block) × N, with block size B minus the estimated
output workspace minus one input frame. Hash adds 3 × (M + N).
Sort-merge adds both sorting costs plus M + N. These are comparative
estimates. The executor materializes inputs in temporary runs, and its
observed I/O includes those runs, heap directory reads, and hash overflow.
The handout's worked DP table charges scan-only inputs inside BNL's
formula, while this implementation charges child costs separately and
uses a local join estimate. The worked example's absolute costs therefore differ from this estimator.

## Rewrites and join order

1. Split top-level AND into separate predicates.
2. Push a predicate across an inner join or cross only if all referenced
   columns belong to the destination side.
3. Convert a cross plus an equality referencing both inputs into a join.
4. Push through a rename by translating qualified names, or through UNION
   and UNION ALL by matching column positions on both inputs.
5. Push a predicate through grouping when it refers solely to grouping keys.
   Global aggregates still create their single group on empty input.
6. Retain required columns and current predicates or sort keys when pushing
   projections. Set, grouping and DISTINCT operators act as projection
   boundaries in this implementation.
7. Remove identity projections and collapse adjacent filters.

Each rule can be disabled independently. Phase ordering can cause one rule
to change whether another applies. The DP table stores the cheapest
estimated tree for each subset of at most six inputs. Every extension
puts one input on the right. This bounds the search and supports pipeline
execution where an operator permits it. It excludes bushy trees and
does not retain alternatives by interesting sort order. Whenever an
extension has a connecting predicate, it is preferred over Cartesian
extensions for that subset. Disconnected subsets retain a Cartesian
candidate for use when necessary. A physical hash or merge join takes
one cross-input column equality, and any residual predicates remain
filters. A projection restores the original logical output column order
when DP swaps inputs.

## Sources and their effect

- [Selinger et al., SIGMOD 1979](https://doi.org/10.1145/582095.582099)
  motivated comparing access paths and reusing the best plan for a subset.
  The full-scan-only storage layer reduces the choices at singleton subsets.
- [Leis et al., PVLDB 2015](https://www.vldb.org/pvldb/vol9/p204-leis.pdf)
  motivated measuring cardinality error separately from I/O and running
  alternate join plans for the correlated test.
