"""Plot measured join, selection and projection time on log-log axes."""

import argparse
import csv
import math


def fit_slope(points: list[tuple[int, float]]) -> float:
    xs = [math.log(n) for n, _ in points]
    ys = [math.log(t) for _, t in points]
    avg_x, avg_y = sum(xs) / len(xs), sum(ys) / len(ys)
    return sum((x - avg_x) * (y - avg_y) for x, y in zip(xs, ys)) / sum(
        (x - avg_x) ** 2 for x in xs
    )


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--csv", default="measurements.csv")
    cli.add_argument("--out", default="timing-loglog.png")
    args = cli.parse_args()
    import matplotlib.pyplot as plt  # allowed for report plotting only

    series: dict[str, list[tuple[int, float]]] = {"join": [], "select": [], "project": []}
    with open(args.csv, newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            if row["operator"] in series and float(row["match_rate"]) == 0:
                series[row["operator"]].append((int(row["n"]), float(row["wall_seconds"])))
    for name, points in series.items():
        points.sort()
        if points:
            plt.loglog([n for n, _ in points], [t for _, t in points], "o-", label=name.title())
    slope = fit_slope(series["join"])
    plt.title("Measured query time by relation size")
    plt.xlabel("Tuples per relation (n = m)")
    plt.ylabel("Wall time (seconds)")
    plt.grid(True, which="both", alpha=0.25)
    plt.legend()
    plt.text(0.98, 0.03, f"Join log-log slope: {slope:.2f}",
             transform=plt.gca().transAxes, ha="right", va="bottom",
             bbox={"facecolor": "white", "alpha": 0.8, "edgecolor": "none"})
    plt.tight_layout()
    plt.savefig(args.out, dpi=180)
    print(f"Saved {args.out}; join log-log slope = {slope:.3f}")


if __name__ == "__main__":
    main()
