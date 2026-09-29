"""Generate R(a,b) and S(b,c) with a controllable average join match rate."""

import argparse


def generate(n: int, m: int, match_rate: float, destination: str) -> int:
    """Return exact output rows: each matched S row joins one distinct-key R row."""
    if n <= 0 or m <= 0:
        raise ValueError("n and m must be positive")
    if not 0 <= match_rate <= m / n:
        raise ValueError(f"match rate must be between 0 and m/n ({m/n:g})")
    matched = round(n * match_rate)
    with open(destination, "w", encoding="utf-8") as stream:
        stream.write("R(a,b) = {\n")
        for i in range(n):
            stream.write(f"{i}, {i}\n")
        stream.write("}\nS(b,c) = {\n")
        for j in range(m):
            key = (j % n) if j < matched else n + j
            stream.write(f"{key}, {j}\n")
        stream.write("}\n")
    return matched


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--n", type=int, required=True, help="rows in R")
    cli.add_argument("--m", type=int, required=True, help="rows in S")
    cli.add_argument("--match-rate", type=float, required=True,
                     help="average matching S rows per R row")
    cli.add_argument("--out", required=True, help="output relation file")
    args = cli.parse_args()
    try:
        matched = generate(args.n, args.m, args.match_rate, args.out)
    except ValueError as error:
        cli.error(str(error))
    print(f"Wrote {args.n} R rows, {args.m} S rows; expected join output: {matched} rows")


if __name__ == "__main__":
    main()
