# Final checks and recording commands

Extract the final update into the existing relational-algebra-interpreter
folder. Run these in VS Code PowerShell from that folder. Use a fresh
database directory name on a second walkthrough.

```powershell
python -m unittest discover -s tests -v
python engine.py --db bonus2_video_demo --frames 4 --file sql_pipeline_demo.sql
python engine.py --db bonus2_video_demo --frames 4 --file DEMO_CHECK.sql
```

Expect 98 tests and OK. The first engine command creates two tables and
returns Bob (South, 24) followed by Amy (North, 17). The second command
collects histograms, prints the estimated plan, and executes the plan to
show actual counts. The optimizer pushes the points filter to Players.
Drew passes that filter but has no matching team. Chris has a team but
fails the points filter. This means the optimized join takes three
filtered Players rows and outputs two. The previous unoptimized demo
joined first and returned three intermediate rows.

The SQL prompt is available with `python engine.py --db bonus2_video_demo`.
End SQL statements with a semicolon. Type `.quit` to exit. The individual
tools are `python storage.py`, `python executor.py`,
`python sql_frontend.py`, and `python opt.py`. Add `--help` to see options.

## PostgreSQL verification on OpenStack

Use the already configured OpenStack server in pgAdmin. Open a Query
Tool on the course database, use its Open File button to open
`m4_results/postgres_workload.sql`, and run the entire script in a fresh
Query Tool session. It uses temporary tables and returns 25 rows with
query_id, row_count and rows columns. In Data Output, download that
result as CSV with headers and save it as `postgres_results.csv` in the
repository folder. Run:

```powershell
python check_postgres.py postgres_results.csv
```

Expect `All 25 PostgreSQL result sets match.` The generated workload
was checked locally against SQLite. Save the actual PostgreSQL output
and record the verification in REPORT_INTEGRATION.md only after the
OpenStack run succeeds. If the instructor gives you a separate hidden
workload, report that separately using the same execution metrics.

## What to show and explain

1. Run the original parser tree command below. Explain how the lexer
   emits IDENT tokens and the parser groups the two set operators.
2. Show the storage and join measurements in REPORT_M1.md and
   REPORT_M2.md. Explain a cache hit versus a miss and why joins spill.
3. Run the two engine commands above. Explain how compiled logical
   operators become a physical plan, and trace two result rows.
4. Open REPORT_M4.md at Q12. Explain why 8 estimated can become 60
   actual, and why actual hash I/O was lower than the chosen plan.
   Q25 shows that changing frames can also change the algorithm.

```powershell
python ra.py --tree "A union B minus C"
```
