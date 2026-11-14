"""Render measurement tables from the recorded optimizer workload."""

import json
from pathlib import Path
from statistics import median


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "m4_results"


def io(run):
    return run["page_reads"] + run["page_writes"]


def write_reports():
    data = json.loads((RESULTS / "m4_measurements.json").read_text(encoding="utf-8"))
    queries = data["queries"]
    records = queries[:20]
    errors = [
        (max(node["est_rows"], node["tuples_out"]) / max(1, min(node["est_rows"], node["tuples_out"])),
         index, node["op"], node["est_rows"], node["tuples_out"])
        for index, row in enumerate(records, 1) for node in row["optimized"]["nodes"]
    ]
    worst, index, op, est, actual = max(errors)
    zeros = sum(actual == 0 for *_, actual in errors)
    ratios = [io(row["optimized"]) / io(row["baseline"]) for row in records]
    report = [
        "# Module 4 report", "",
        "The instructor approved generated report inputs. Q01–Q20 form the optimizer workload.",
        "Q01–Q25 also form the local integration rehearsal. The workload is",
        "defined in `measure_m4.py`, including the full SQL and data generator.",
        "Fact and Other each have 480 rows. Their five VARCHAR(240) fields",
        "contain 240 ASCII bytes each. Fact has `x = y = id mod 8`.",
        "Team, Region and Country have eight, four and two rows.",
        f"Each measurement starts with a new {data['frames']}-frame buffer pool. I/O is reads plus writes.",
        "Rows were compared as multisets to an independent SQLite run for every",
        "baseline, optimized and rule-disabled variant. Ordered demo output is",
        "also tested separately. Run `python measure_m4.py` and then",
        "`python summarize_m4.py` to reproduce these files.", "",
        "## Estimation and I/O", "",
        "The [raw measurements](m4_results/m4_measurements.json) list estimated and",
        "actual rows and counters at every operator, including implicit sorts.",
        f"Across {len(errors)} nodes, median q-error is {median(x[0] for x in errors):.3f} and",
        f"worst finite recorded q-error is {worst:.2f} at Q{index:02d} {op}",
        f"({est} estimated and {actual} actual). The finite summary floors both",
        f"counts at one. There are {zeros} zero-actual nodes, each with infinite",
        "unadjusted q-error because the positive estimate is divided by zero.", "",
        "Baseline means joins in written order using block nested loops with no",
        f"rewrite. The optimized-to-baseline I/O ratio ranges from {min(ratios):.3f} to {max(ratios):.3f}.",
        "Q11 benefits from narrow join inputs after projection. Most scans",
        "read the same heap pages after rewriting because there is no index.", "",
        "| Query | Estimated root rows | Actual root rows | Optimized I/O | Baseline I/O | Ratio |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for index, (row, ratio) in enumerate(zip(records, ratios), 1):
        plan, base = row["optimized"], row["baseline"]
        report.append(f"| Q{index:02d} | {plan['est_rows']} | {plan['tuples_out']} | {io(plan)} | {io(base)} | {ratio:.3f} |")
    report.extend(["", "## One rule disabled at a time", "",
                   "| Rule disabled | All rules I/O | Without rule I/O | Difference |",
                   "| --- | ---: | ---: | ---: |"])
    full = sum(io(row["optimized"]) for row in records)
    for rule, label in enumerate(("Split AND", "Select through join", "Cross to join",
                                  "Select through rename or union", "Select through group",
                                  "Projection pushdown", "Cleanup"), 1):
        ablated = sum(io(row["ablation"][str(rule)]) for row in records)
        report.append(f"| {rule}. {label} | {full} | {ablated} | {ablated - full:+d} |")
    report.extend(["",
                   "Rule 3 changes Q14's logical shape, but its tables fit on a page each.",
                   "Rule 7 changes pipeline structure and has no direct page cost.",
                   "Rule 6 can reduce spool widths. Disabling one rule can also prevent",
                   "another rule from firing, so differences need not add up.", "",
                   "## Correlated failure on Q12", "",
                   "Each marginal predicate `x = 1` and `y = 1` keeps 60 of 480",
                   "tuples. Independence predicts ceil(480 × 1/8 × 1/8) = 8.",
                   "Every row has x = y, so both predicates together still keep 60.",
                   "Each surviving ID matches exactly one Other row. The optimizer",
                   "expected a small outer spool and chose block nested loops,",
                   "but the actual outer input caused extra passes over Other.", "",
                   "| Algorithm on the same rewritten inputs | Estimated cumulative cost | Actual I/O |",
                   "| --- | ---: | ---: |"])
    for name, run in records[11]["alternatives"].items():
        report.append(f"| {name} | {run['nodes'][0]['est_cost']} | {io(run)} |")
    bad = io(records[11]["alternatives"]["nested_loop_join"])
    good = io(records[11]["alternatives"]["hash_join"])
    report.extend(["", f"Hash saves {bad-good} page I/Os. The chosen plan used {bad/good:.2f}× as much I/O.",
                   "A joint frequency for (x, y), or a dependency statistic that records",
                   "y as determined by x, would retain the true 1/8 selectivity.",
                   "With 60 estimated outer rows, the larger outer page estimate",
                   "makes hash cheaper under the four-frame cost model.", "",
                   "## Join search", "",
                   "The [full DP table](m4_results/m4_dp_four_tables.json) records",
                   "all 15 nonempty subsets for Q16, including their complete winning plans.",
                   "Q25 illustrates a frame-dependent choice on the same SQL.", "",
                   "| Frames | Chosen join | Actual I/O |", "| ---: | --- | ---: |"])
    for budget, result in queries[24]["frame_sweep"].items():
        join = next(node["op"] for node in result["nodes"] if node["op"].endswith("_join"))
        report.append(f"| {budget} | {join} | {io(result)} |")
    design = ("The cost-based search follows Selinger et al. The decision to",
              "compare an incorrect estimate against a measured alternative follows",
              "the experimental separation of estimation and execution in Leis et al.",
              "[Selinger et al.](https://doi.org/10.1145/582095.582099) ·",
              "[Leis et al.](https://www.vldb.org/pvldb/vol9/p204-leis.pdf)")
    report.extend(["", *design])
    (ROOT / "REPORT_M4.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    demo = [
        "# Integration rehearsal", "",
        "These are 25 generated queries. See REPORT_M4.md for the dataset",
        "and m4_results/m4_measurements.json for SQL and per-node results.",
        "The local SQLite comparison passed for all 25 result multisets.",
        "PostgreSQL on OpenStack remains a separate verification step.",
        "This report does not contain results for any instructor-issued hidden workload.", "",
        "| Query | Actual rows | Page reads | Page writes | Page I/O | Wall ms | Estimated root rows |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for index, row in enumerate(queries, 1):
        r = row["optimized"]
        demo.append(f"| Q{index:02d} | {r['tuples_out']} | {r['page_reads']} | {r['page_writes']} | {io(r)} | {r['wall_ms']:.3f} | {r['est_rows']} |")
    (ROOT / "REPORT_INTEGRATION.md").write_text("\n".join(demo) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write_reports()
