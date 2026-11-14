"""Generate reproducible inputs and measure the optimizer and integration workload."""

import argparse
from collections import Counter
from contextlib import closing
import csv
from io import StringIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from catalog import Catalog
from opt import measure
from optimizer import Optimizer
from optimizer_stats import analyze
from sql_compiler import compile_sql
from storage import load_csv


PADS = ", ".join(f"p{i} VARCHAR(240)" for i in range(5))
SCHEMAS = {
    "Fact": f"id INT, x INT, y INT, {PADS}",
    "Other": f"id INT, {PADS}",
    "Team": "id INT, region INT",
    "Region": "id INT, country INT",
    "Country": "id INT, label VARCHAR(20)",
}
CORRELATED = ("SELECT f.id, " + ", ".join(f"{side}.p{i}" for side in ("f", "o") for i in range(5))
               + " FROM Fact f JOIN Other o ON f.id = o.id "
              "WHERE f.x = 1 AND f.y = 1")
QUERIES = [
    "SELECT id FROM Fact WHERE x = 1",
    "SELECT id FROM Fact WHERE x = 1 AND y = 1",
    "SELECT id FROM Fact WHERE x = 1 OR y = 2",
    "SELECT id FROM Fact WHERE NOT (id < 100)",
    "SELECT id FROM Fact WHERE id >= 200 AND id < 220",
    "SELECT id FROM Fact WHERE id <> 250 ORDER BY id DESC LIMIT 5",
    "SELECT DISTINCT x FROM Fact ORDER BY x",
    "SELECT x, COUNT(*) AS n FROM Fact GROUP BY x HAVING x > 4 ORDER BY x",
    "SELECT y, SUM(id) AS n FROM Fact GROUP BY y HAVING SUM(id) > 8000 ORDER BY y",
    "SELECT COUNT(*) AS n FROM Fact WHERE id = 999",
    "SELECT f.id FROM Fact f JOIN Other o ON f.id = o.id",
    CORRELATED,
    "SELECT t.id, r.id FROM Team t JOIN Region r ON t.region = r.id WHERE r.id = 1",
    "SELECT t.id, r.id FROM Team t, Region r WHERE t.region = r.id AND r.id = 1",
    "SELECT t.id, r.id, c.label FROM Team t JOIN Region r ON t.region = r.id JOIN Country c ON r.country = c.id",
    "SELECT f.id, t.id, r.id, c.label FROM Fact f JOIN Team t ON f.x = t.id "
    "JOIN Region r ON t.region = r.id JOIN Country c ON r.country = c.id WHERE f.id < 30",
    "SELECT f.id FROM Fact f WHERE f.id < 6 UNION SELECT t.id FROM Team t",
    "SELECT t.id FROM Team t UNION ALL SELECT r.id FROM Region r",
    "SELECT t.id FROM Team t INTERSECT SELECT r.id FROM Region r",
    "SELECT t.id FROM Team t EXCEPT SELECT r.id FROM Region r",
    "SELECT u.id FROM (SELECT id FROM Team UNION ALL SELECT id FROM Region) AS u WHERE u.id = 2",
    "SELECT u.x, u.n FROM (SELECT x, COUNT(*) AS n FROM Fact GROUP BY x) AS u WHERE u.x = 7",
    "SELECT c.label FROM Country c ORDER BY c.id DESC",
    "SELECT a.id, b.region FROM Team a JOIN Team b ON a.region = b.region WHERE a.id < b.id",
    CORRELATED.rsplit(" AND", 1)[0],
]


def fixture():
    text = ("p" * 240,) * 5
    return {
        "Fact": [(i, i % 8, i % 8, *text) for i in range(480)],
        "Other": [(i, *text) for i in range(480)],
        "Team": [(i, i % 4) for i in range(8)],
        "Region": [(i, i % 2) for i in range(4)],
        "Country": [(0, "North"), (1, "South")],
    }


def query_rows(database, query):
    return Counter(tuple(row) for row in database.execute(query).fetchall())


def run(output_dir, frames):
    output_dir.mkdir(parents=True, exist_ok=True)
    postgres_statements = [
        "CREATE TEMP TABLE verification_results (query_id INT, row_count BIGINT, rows JSONB);"
    ]
    expected_rows = {}
    with TemporaryDirectory() as folder, closing(sqlite3.connect(":memory:")) as oracle:
        base = Path(folder)
        with closing(Catalog(base / "db", frames=frames)) as catalog:
            for table, records in fixture().items():
                ddl = f"CREATE TABLE {table} ({SCHEMAS[table]})"
                plan = compile_sql(ddl, catalog)
                catalog.create_table(table, plan["schema"])
                file = base / f"{table}.csv"
                with file.open("w", newline="", encoding="utf-8") as handle:
                    csv.writer(handle).writerows(records)
                load_csv(catalog, table, file)
                analyze(catalog, table)
                oracle.execute(ddl)
                oracle.executemany(f"INSERT INTO {table} VALUES ({','.join('?' for _ in records[0])})", records)
                postgres_statements.append(ddl.replace("CREATE TABLE", "CREATE TEMP TABLE", 1) + ";")
                postgres_statements.append(f"INSERT INTO {table} VALUES " + ",\n".join(
                    "(" + ",".join(str(value) if isinstance(value, int) else "'" + value.replace("'", "''") + "'"
                                   for value in row) + ")" for row in records) + ";")
        def execute(estimate, expected):
            with closing(Catalog(base / "db", frames=frames)) as catalog:
                writer = StringIO()
                result = measure(estimate, catalog, writer)
                actual = Counter(tuple(json.loads(line)) for line in writer.getvalue().splitlines())
                if actual != expected:
                    raise ValueError(f"wrong rows in {estimate.plan['op']}")
            return result

        result = []
        dp_table = None
        for index, query in enumerate(QUERIES, 1):
            with closing(Catalog(base / "db", frames=frames)) as catalog:
                opt = Optimizer(catalog)
                logical = compile_sql(query, catalog)
                optimized = opt.optimize(logical)
                if index == 16:
                    dp_table = opt.dp_tables[-1]
                baseline = opt.estimate(logical, baseline=True)
                ablated = {str(rule): opt.optimize(logical, (rule,)) for rule in range(1, 8)} if index <= 20 else {}
                expected = query_rows(oracle, query)
                expected_rows[str(index)] = list(expected.elements())
                print(f"Q{index:02d}", flush=True)
                row = {"query": query, "optimized": execute(optimized, expected),
                       "baseline": execute(baseline, expected),
                       "ablation": {rule: execute(plan, expected) for rule, plan in ablated.items()}}
                if index == 12:
                    # Test all eligible algorithms on the same rewritten inputs.
                    join = optimized
                    while join.plan["op"] not in ("nested_loop_join", "hash_join", "sort_merge_join"):
                        join = join.children[0]
                    from optimizer_rewrite import split
                    from ra_parser import parse_condition
                    left, right = join.children
                    clauses = split(parse_condition(join.plan["condition"]))
                    row["alternatives"] = {}
                    for method in ("nested_loop_join", "sort_merge_join", "hash_join"):
                        alt = opt.join(left, right, clauses, method)
                        alt = opt.project(alt, logical["columns"], logical.get("aliases", logical["columns"]))
                        row["alternatives"][method] = execute(alt, expected)
                    row["chosen"] = join.plan["op"]
                if index == 25:
                    row["frame_sweep"] = {}
                    for budget in (4, 32):
                        with closing(Catalog(base / "db", frames=budget)) as sweep_catalog:
                            estimate = Optimizer(sweep_catalog).optimize(logical)
                            row["frame_sweep"][str(budget)] = measure(estimate, sweep_catalog)
                result.append(row)
                # A CTE with synthetic output column names also supports duplicate source column names.
                sample = next(iter(expected), None)
                width = len(sample) if sample is not None else len(oracle.execute(query).description)
                columns = ",".join(f"c{i}" for i in range(width))
                postgres_statements.append(
                    f"INSERT INTO verification_results WITH q({columns}) AS ({query}) "
                    f"SELECT {index} AS query_id, COUNT(*) AS row_count, "
                    "COALESCE(jsonb_agg(to_jsonb(q) ORDER BY to_jsonb(q)), '[]'::jsonb) AS rows FROM q;")
    postgres_statements.append("SELECT * FROM verification_results ORDER BY query_id;")
    (output_dir / "expected_rows.json").write_text(json.dumps(expected_rows) + "\n", encoding="utf-8")
    (output_dir / "m4_measurements.json").write_text(json.dumps({"frames": frames, "queries": result}, indent=2) + "\n", encoding="utf-8")
    (output_dir / "m4_dp_four_tables.json").write_text(json.dumps(dp_table, indent=2) + "\n", encoding="utf-8")
    (output_dir / "postgres_workload.sql").write_text("\n\n".join(postgres_statements) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("m4_results"))
    parser.add_argument("--frames", type=int, default=4)
    args = parser.parse_args()
    run(args.output, args.frames)
