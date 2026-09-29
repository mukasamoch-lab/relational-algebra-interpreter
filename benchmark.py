"""Run reproducible join/select/project timings without printing result tuples."""

import argparse
import os
import tempfile
from time import perf_counter

from generate_data import generate
from ra import parse
from ra_data import load_relations
from ra_engine import Counters, evaluate


QUERIES = {
    "join": "R join[R.b=S.b] S",
    "select": "select[a>=0](R)",
    "project": "project[a](R)",
}


def measure(n: int, match_rate: float) -> list[tuple]:
    with tempfile.TemporaryDirectory() as directory:
        data_path = os.path.join(directory, "relations.ra")
        generate(n, n, match_rate, data_path)
        with open(data_path, encoding="utf-8") as stream:
            catalog = load_relations(stream.read())
    records = []
    for name, query in QUERIES.items():
        counters = Counters()
        start = perf_counter()
        result = evaluate(parse(query), catalog, counters)
        seconds = perf_counter() - start
        records.append((name, n, n, match_rate, counters.join_comparisons,
                        counters.select_evaluations, seconds, len(result.rows)))
    return records


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--sizes", nargs="+", type=int,
                     default=[1000, 2000, 4000, 8000, 16000, 32000, 64000])
    cli.add_argument("--match-rate", type=float, default=0,
                     help="average matching S rows per R row (0 to 1 for equal sizes)")
    cli.add_argument("--csv", help="optional output CSV; otherwise print to stdout")
    args = cli.parse_args()
    header = "operator,n,m,match_rate,join_comparisons,select_evaluations,wall_seconds,output_tuples\n"
    out = open(args.csv, "w", encoding="utf-8") if args.csv else None
    try:
        target = out if out else __import__("sys").stdout
        target.write(header)
        target.flush()
        for n in args.sizes:
            for record in measure(n, args.match_rate):
                target.write(",".join(map(str, record)) + "\n")
                target.flush()
    finally:
        if out:
            out.close()


if __name__ == "__main__":
    main()
