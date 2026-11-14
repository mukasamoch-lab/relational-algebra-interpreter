# Relational algebra interpreter

Python 3, standard library only. The scanner and recursive descent parser are handwritten; no regular expressions, parser generators, `eval`, or SQL engine are used.

## Project 2 SQL compiler

```powershell
python sql_frontend.py parse --query "SELECT a FROM R WHERE a > 42" --explain
python sql_frontend.py compile --db m1_demo --file sample.sql --json
python sql_frontend.py check --db m1_demo --file sample.sql
```

For the compile example, put `SELECT b FROM R ORDER BY a DESC;`
in sample.sql. Compile emits a logical plan. See GRAMMAR_SQL.md and
DESIGN_M3.md for the supported SQL and plan fields.

## Project 2 SQL execution baseline

Run the demo against a fresh directory. The script creates and loads its own tables.

```powershell
python sql_frontend.py run --db sql_pipeline_demo_db --file sql_pipeline_demo.sql --frames 4
```

The final result has two rows, Bob then Amy, followed by total and per-operator
counters and elapsed milliseconds. For a repeat run, choose a fresh directory
name. SQL scripts compile before execution, then statements apply in order.
The script is not transactional. The runner buffers each query result before
printing JSON, so use it with workloads whose results fit in memory.

The baseline uses block nested-loop joins in written order and sort-based
grouping and DISTINCT. A cross product uses a constant true join predicate.
This baseline is intended for comparison with the Module 4 optimizer.

## Project 2 execution pipeline

Current Module 2 operators are seqscan, filter, project, limit, and external sort.
See [START_HERE_M2.md](START_HERE_M2.md) for the sample plan and commands.
See [START_HERE_SORT.md](START_HERE_SORT.md) for sort tests and measurements.
Block nested-loop, sort-merge, and Grace hash joins are available.
Grouping and DISTINCT support sort and hash modes. Set operators support
UNION ALL, UNION, INTERSECT, and EXCEPT.

## Project 2 storage

See [START_HERE_M1.md](START_HERE_M1.md) for the Windows installation
and end-to-end checks. The five storage commands are available through
`python storage.py` and the Linux `./storage` launcher. All files share
one buffer pool. The catalog is persisted in pages.

The named-table library API is `catalog.open_table(name)`, whose
insert/get/delete/scan methods use decoded tuples and maintain live-row
counts. HeapFile remains the lower-level encoded-record API.

`python measure_m1.py` reproduces capacity and scan-cost measurements.
REPORT_M1.md includes five self-generated traces and all 40 policy measurements.
Use generate_m1_traces.py to reproduce the input files.

## Run

Put relation definitions in `data.ra`:

```text
Employees(EID, Name, Age, DID) = {
  E1, John, 32, D1
  E2, Alice, 28, D2
  E3, Bob, 29, D1
}
```

Print a tree without executing or loading a data file:

```bash
python3 ra.py --tree 'A union B minus C'
```

Execute a query; `--stats` shows total condition evaluations and a separate count for each `select` or `join` occurrence, identified by its query position:

```bash
python3 ra.py --data data.ra --stats 'project[Name](select[Age>30](Employees))'
```

Run the required cases and additional checks:

```bash
python3 -m unittest discover -s tests -v
```

Generate data with 1,000 tuples in each relation and an average of 0.5 matching S tuples per R tuple:

```bash
python3 generate_data.py --n 1000 --m 1000 --match-rate 0.5 --out generated.ra
python3 ra.py --data generated.ra --stats 'R join[R.b=S.b] S'
```

Benchmark all required sizes and save raw measurements; `plot_results.py` uses Matplotlib only for the report plot:

```bash
python3 benchmark.py --match-rate 0 --csv measurements.csv
python3 plot_results.py --csv measurements.csv --out timing-loglog.png
```

The last commands may take a very long time. `benchmark.py` prints each completed size as it goes when run without `--csv`; with `--csv` it flushes each completed row to the file. The report must identify the actual machine that ran the measurements and include only completed runs.

## Syntax and semantics

See `GRAMMAR.md` for EBNF, precedence, literals, and the ambiguity demonstration. Supported operations: `select`, `project`, `rename`, `union`, `intersect`, `minus`, `times`, and conditional `join`. Projection preserves duplicate rows. Use `distinct(project[Name](Employees))` to remove them. The Bonus 1 input loader and the set operators retain their original duplicate handling. Schema checks precede set operations; conditions compare two attributes or an attribute and a literal. An unqualified column shared by two inputs is ambiguous. Query errors are reported with positions and without stack traces.

`rename[E2](Emp)` makes a self join possible: `rename[E2](Emp) join[Emp.MgrID=E2.EID] Emp`. Without renaming, both copies would have the same qualified column names (for example, `Emp.EID`); there would be no unambiguous way to say which copy each reference denotes. The join keeps columns from both inputs.

## Code structure

- `ra.py`: command-line entry point and legacy imports.
- `ra_lexer.py`: tokenizer and query errors.
- `ra_parser.py`: query parser and standalone `parse_condition`.
- `ra_tree.py`: AST, tree rendering, and `tree_to_json` / `tree_from_json`.
- `ra_data.py`: line-aware relation loader; quoted/bare values and schema inference.
- `ra_engine.py`: relation representation, explicit tuple equality, operators, checks, and counters.
- `generate_data.py`: size and match-rate controlled `R(a,b)` / `S(b,c)` generator.
- `benchmark.py`: query timings and measured counters.
- `tests/`: all 25 numbered assignment cases and additional checks.

Library callers can pass `evaluate` a mapping of relation names or a function
that returns a `Relation` with a fresh iterable of tuples for each lookup.
This evaluator still materializes operator results. Module 2 defines the
iterator interface for streaming execution.

## Known limits and remaining deliverables

- Relation schemas infer column types from nonempty data. An empty relation has unknown column types, so a comparison involving one cannot be statically type checked until values exist.
- Tuple deduplication uses hash buckets only to narrow candidate rows, then calls our own `equal_tuple` to decide equality. Joins still use nested loops and no hash join. An unusually large number of hash collisions can slow deduplication.
- Comments occupy whole lines; tuple rows start on the line after `{`. The one-line form `{}` is accepted for an empty relation.
- `REPORT.md` contains one complete 1,000–64,000 row performance study, a log-log graph, an extrapolation, and one match-rate comparison from a Windows i7-1355U machine. The video and GitHub submission remain to be completed.
