# Module 4 report

The instructor approved generated report inputs. Q01–Q20 form the optimizer workload.
Q01–Q25 also form the local integration rehearsal. The workload is
defined in `measure_m4.py`, including the full SQL and data generator.
Fact and Other each have 480 rows. Their five VARCHAR(240) fields
contain 240 ASCII bytes each. Fact has `x = y = id mod 8`.
Team, Region and Country have eight, four and two rows.
Each measurement starts with a new 4-frame buffer pool. I/O is reads plus writes.
Rows were compared as multisets to an independent SQLite run for every
baseline, optimized and rule-disabled variant. Ordered demo output is
also tested separately. Run `python measure_m4.py` and then
`python summarize_m4.py` to reproduce these files.

## Estimation and I/O

The [raw measurements](m4_results/m4_measurements.json) list estimated and
actual rows and counters at every operator, including implicit sorts.
Across 140 nodes, median q-error is 1.000 and
worst finite recorded q-error is 95.80 at Q06 sort
(479 estimated and 5 actual). The finite summary floors both
counts at one. There are 1 zero-actual nodes, each with infinite
unadjusted q-error because the positive estimate is divided by zero.

Baseline means joins in written order using block nested loops with no
rewrite. The optimized-to-baseline I/O ratio ranges from 0.024 to 1.000.
Q11 benefits from narrow join inputs after projection. Most scans
read the same heap pages after rewriting because there is no index.

| Query | Estimated root rows | Actual root rows | Optimized I/O | Baseline I/O | Ratio |
| --- | ---: | ---: | ---: | ---: | ---: |
| Q01 | 60 | 60 | 161 | 161 | 1.000 |
| Q02 | 8 | 60 | 161 | 161 | 1.000 |
| Q03 | 113 | 120 | 161 | 161 | 1.000 |
| Q04 | 380 | 380 | 161 | 161 | 1.000 |
| Q05 | 129 | 20 | 161 | 161 | 1.000 |
| Q06 | 5 | 5 | 163 | 163 | 1.000 |
| Q07 | 480 | 8 | 167 | 167 | 1.000 |
| Q08 | 8 | 3 | 763 | 2075 | 0.368 |
| Q09 | 3 | 8 | 2075 | 2075 | 1.000 |
| Q10 | 1 | 1 | 161 | 161 | 1.000 |
| Q11 | 480 | 480 | 326 | 13602 | 0.024 |
| Q12 | 8 | 60 | 2122 | 13602 | 0.156 |
| Q13 | 2 | 2 | 8 | 8 | 1.000 |
| Q14 | 2 | 2 | 8 | 8 | 1.000 |
| Q15 | 8 | 8 | 14 | 14 | 1.000 |
| Q16 | 31 | 30 | 179 | 1133 | 0.158 |
| Q17 | 15 | 8 | 171 | 171 | 1.000 |
| Q18 | 12 | 12 | 4 | 4 | 1.000 |
| Q19 | 8 | 4 | 12 | 12 | 1.000 |
| Q20 | 8 | 4 | 12 | 12 | 1.000 |

## One rule disabled at a time

| Rule disabled | All rules I/O | Without rule I/O | Difference |
| --- | ---: | ---: | ---: |
| 1. Split AND | 6990 | 6990 | +0 |
| 2. Select through join | 6990 | 10026 | +3036 |
| 3. Cross to join | 6990 | 6990 | +0 |
| 4. Select through rename or union | 6990 | 6990 | +0 |
| 5. Select through group | 6990 | 8302 | +1312 |
| 6. Projection pushdown | 6990 | 11838 | +4848 |
| 7. Cleanup | 6990 | 6990 | +0 |

Rule 3 changes Q14's logical shape, but its tables fit on a page each.
Rule 7 changes pipeline structure and has no direct page cost.
Rule 6 can reduce spool widths. Disabling one rule can also prevent
another rule from firing, so differences need not add up.

## Correlated failure on Q12

Each marginal predicate `x = 1` and `y = 1` keeps 60 of 480
tuples. Independence predicts ceil(480 × 1/8 × 1/8) = 8.
Every row has x = y, so both predicates together still keep 60.
Each surviving ID matches exactly one Other row. The optimizer
expected a small outer spool and chose block nested loops,
but the actual outer input caused extra passes over Other.

| Algorithm on the same rewritten inputs | Estimated cumulative cost | Actual I/O |
| --- | ---: | ---: |
| nested_loop_join | 609 | 2122 |
| sort_merge_join | 1756 | 2393 |
| hash_join | 758 | 1116 |

Hash saves 1006 page I/Os. The chosen plan used 1.90× as much I/O.
A joint frequency for (x, y), or a dependency statistic that records
y as determined by x, would retain the true 1/8 selectivity.
With 60 estimated outer rows, the larger outer page estimate
makes hash cheaper under the four-frame cost model.

## Join search

The [full DP table](m4_results/m4_dp_four_tables.json) records
all 15 nonempty subsets for Q16, including their complete winning plans.
Q25 illustrates a frame-dependent choice on the same SQL.

| Frames | Chosen join | Actual I/O |
| ---: | --- | ---: |
| 4 | hash_join | 1233 |
| 32 | nested_loop_join | 682 |

The cost-based search follows Selinger et al. The decision to
compare an incorrect estimate against a measured alternative follows
the experimental separation of estimation and execution in Leis et al.
[Selinger et al.](https://doi.org/10.1145/582095.582099) ·
[Leis et al.](https://www.vldb.org/pvldb/vol9/p204-leis.pdf)
