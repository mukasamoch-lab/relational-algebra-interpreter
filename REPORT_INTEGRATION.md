# Integration rehearsal

These are 25 generated queries. See REPORT_M4.md for the dataset
and m4_results/m4_measurements.json for SQL and per-node results.
The local SQLite comparison passed for all 25 result multisets.
All 25 generated rehearsal queries matched PostgreSQL on OpenStack.
The comparison checked row counts and complete result sets, including duplicates.
This report does not contain results for any instructor-issued hidden workload.

| Query | Actual rows | Page reads | Page writes | Page I/O | Wall ms | Estimated root rows |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Q01 | 60 | 161 | 0 | 161 | 15.080 | 60 |
| Q02 | 60 | 161 | 0 | 161 | 15.128 | 8 |
| Q03 | 120 | 161 | 0 | 161 | 15.468 | 113 |
| Q04 | 380 | 161 | 0 | 161 | 16.623 | 380 |
| Q05 | 20 | 161 | 0 | 161 | 83.139 | 129 |
| Q06 | 5 | 162 | 1 | 163 | 16.820 | 5 |
| Q07 | 8 | 164 | 3 | 167 | 31.361 | 480 |
| Q08 | 3 | 462 | 301 | 763 | 45.485 | 8 |
| Q09 | 8 | 1118 | 957 | 2075 | 102.322 | 3 |
| Q10 | 1 | 161 | 0 | 161 | 13.607 | 1 |
| Q11 | 480 | 324 | 2 | 326 | 380.432 | 480 |
| Q12 | 60 | 1942 | 180 | 2122 | 303.279 | 8 |
| Q13 | 2 | 6 | 2 | 8 | 4.823 | 2 |
| Q14 | 2 | 6 | 2 | 8 | 3.132 | 2 |
| Q15 | 8 | 10 | 4 | 14 | 4.887 | 8 |
| Q16 | 30 | 173 | 6 | 179 | 21.494 | 31 |
| Q17 | 8 | 167 | 4 | 171 | 18.271 | 15 |
| Q18 | 12 | 4 | 0 | 4 | 2.527 | 12 |
| Q19 | 4 | 8 | 4 | 12 | 3.505 | 8 |
| Q20 | 4 | 8 | 4 | 12 | 3.865 | 8 |
| Q21 | 2 | 4 | 0 | 4 | 2.946 | 2 |
| Q22 | 1 | 1117 | 956 | 2073 | 123.371 | 1 |
| Q23 | 2 | 3 | 1 | 4 | 1.680 | 2 |
| Q24 | 4 | 4 | 2 | 6 | 3.477 | 2 |
| Q25 | 60 | 1051 | 182 | 1233 | 177.310 | 60 |

## Sources and their effect

Graefe's *Volcano* paper informed the iterator interface between the
physical plan and the executor. The SQL shell pulls rows from the root
operator and uses that same execution path to collect actual counts
for EXPLAIN ANALYZE.
[Graefe, Volcano](https://people.eecs.berkeley.edu/~prabal/teaching/resources/eecs582/graefe94volcano.pdf)

PostgreSQL's SELECT documentation informed the result comparison.
UNION ALL retains duplicates, and row order is unspecified without
ORDER BY. The workload checker compares rows with their multiplicities,
while the ordered shell demo is checked separately.
[PostgreSQL SELECT](https://www.postgresql.org/docs/current/sql-select.html)
