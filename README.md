# Relational algebra interpreter

Python 3, standard library only. The scanner and recursive descent parser are handwritten; no regular expressions, parser generators, `eval`, or SQL engine are used.

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

See `GRAMMAR.md` for EBNF, precedence, literals, and the ambiguity demonstration. Supported operations: `select`, `project`, `rename`, `union`, `intersect`, `minus`, `times`, and conditional `join`. Relations use set semantics. Tuple equality is explicitly defined by `equal_tuple` and checked when inserting output tuples. Schema checks precede set operations; conditions compare two attributes or an attribute and a literal. An unqualified column shared by two inputs is ambiguous. Query errors are reported with positions and without stack traces.

`rename[E2](Emp)` makes a self join possible: `rename[E2](Emp) join[Emp.MgrID=E2.EID] Emp`. Without renaming, both copies would have the same qualified column names (for example, `Emp.EID`); there would be no unambiguous way to say which copy each reference denotes. The join keeps columns from both inputs.

## Code structure

- `ra.py`: query scanner, parser, AST/tree printer, command-line entry point.
- `ra_data.py`: line-aware relation loader; quoted/bare values and schema inference.
- `ra_engine.py`: relation representation, explicit tuple equality, operators, checks, and counters.
- `generate_data.py`: size and match-rate controlled `R(a,b)` / `S(b,c)` generator.
- `benchmark.py`: query timings and measured counters.
- `tests/`: all 25 numbered assignment cases and additional checks.

## Known limits and remaining deliverables

- Relation schemas infer column types from nonempty data. An empty relation has unknown column types, so a comparison involving one cannot be statically type checked until values exist.
- Tuple deduplication uses hash buckets only to narrow candidate rows, then calls our own `equal_tuple` to decide equality. Joins still use nested loops and no hash join. An unusually large number of hash collisions can slow deduplication.
- Comments occupy whole lines; tuple rows start on the line after `{`. The one-line form `{}` is accepted for an empty relation.
- `REPORT.md` contains one complete 1,000–64,000 row performance study, a log-log graph, an extrapolation, and one match-rate comparison from a Windows i7-1355U machine. The video and GitHub submission remain to be completed.
