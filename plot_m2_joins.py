"""Plot total measured page I/O for the two report comparisons."""

import json
from pathlib import Path

import matplotlib.pyplot as plt


def plot(rows, labels, axis, filename):
    fig, ax = plt.subplots(figsize=(8, 4.5), layout="constrained")
    for op, label in (("nested_loop_join", "Block nested-loop"),
                      ("sort_merge_join", "Sort-merge"), ("hash_join", "Grace hash")):
        values = [r["page_reads"] + r["page_writes"] for r in rows if r["op"] == op]
        ax.plot(labels, values, marker="o", label=label)
    ax.set_xlabel(axis)
    ax.set_ylabel("Page reads + writes")
    ax.set_yscale("log")
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    fig.savefig(filename, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    rows = [r for r in json.loads(Path("m2_join_measurements.json").read_text()) if not r["skew"]][:36]
    plot([r for r in rows if r["m"] == 8], ["4", "16", "64", "256"],
         "Buffer frames", "m2_join_crossover_frames.png")
    plot([r for r in rows if r["frames"] == 4], ["8 / 12", "24 / 32", "64 / 96"],
         "Left / right data pages", "m2_join_crossover_size.png")
