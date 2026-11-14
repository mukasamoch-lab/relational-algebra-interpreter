# Module 2 pipeline update

Extract this ZIP and copy its contents into the existing project folder,
allowing replacement of matching files.

Run this from the project's VS Code PowerShell terminal.

```powershell
python -m unittest discover -s tests -v
```

Expected result is 53 passing tests.

The included plan uses the R table already created in m1_demo. It scans R,
keeps rows where R.a > 42, projects R.b and R.c, and stops at one output.

```powershell
python executor.py run --db m1_demo --plan plans/m2_pipeline.json --frames 4 --json
python executor.py trace --db m1_demo --plan plans/m2_pipeline.json --frames 4
```

The JSON rows should be [["amy",9]]. The schema should be
[["R.b","VARCHAR",20],["R.c","INT"]]. The filter's comparison count is 2
for the original fixture (42,bob,7) followed by (43,amy,9).

The trace reports the four nodes with individual counters. Reads belong
to seqscan. The filter, project, and limit have zero page reads of their
own. Catalog preparation reads occur before iterator execution and do
not appear in the per-operator I/O counters.

On Linux, ./exec invokes the same entry point. On Windows, use
python executor.py.

## Current scope

Implemented operators are seqscan, filter, project, and limit.
External sort, distinct, grouping, joins, and set operators still need
implementation and measurement. DESIGN_M2.md is a working draft covering
the pipeline built so far. This is an incremental development update.
