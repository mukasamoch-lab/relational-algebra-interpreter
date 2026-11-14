"""Check all rows returned by postgres_workload.sql, including duplicates."""

import argparse
from collections import Counter
import csv
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("postgres_csv", type=Path)
    parser.add_argument("--expected", type=Path, default=Path("m4_results/expected_rows.json"))
    args = parser.parse_args()
    expected = json.loads(args.expected.read_text(encoding="utf-8"))
    csv.field_size_limit(2**31 - 1)
    found, mismatches = set(), []
    with args.postgres_csv.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if not {"query_id", "row_count", "rows"} <= set(reader.fieldnames or []):
            parser.error("CSV must include query_id, row_count, and rows headers")
        for row in reader:
            query = row["query_id"]
            if query not in expected or query in found:
                parser.error(f"unexpected or duplicate query ID {query}")
            found.add(query)
            actual = Counter(tuple(record[f"c{i}"] for i in range(len(record)))
                             for record in json.loads(row["rows"]))
            goal = Counter(map(tuple, expected[query]))
            if actual != goal or int(row["row_count"]) != len(expected[query]):
                mismatches.append(query)
    missing = sorted(expected.keys() - found, key=int)
    if mismatches or missing:
        print(f"Mismatches: {mismatches}; missing: {missing}")
        return 1
    print(f"All {len(found)} PostgreSQL result sets match.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
