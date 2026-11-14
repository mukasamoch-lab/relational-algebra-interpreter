# External sort update

Copy the extracted ZIP contents into the existing project folder and
allow matching files to be replaced.

Run the full test suite.

```powershell
python -m unittest discover -s tests -v
```

Expected result is 65 passing tests.

Run the included sort plan on the R table in m1_demo.

```powershell
python executor.py run --db m1_demo --plan plans/m2_sort.json --frames 4 --json
python executor.py trace --db m1_demo --plan plans/m2_sort.json --frames 4
```

For the original two-row fixture the result is
[[43,"amy",9],[42,"bob",7]], because R.a is sorted descending.

Run the seven reproducible pass and budget measurements.

```powershell
python measure_m2_sort.py > m2_sort_measurements.json
```

In the last entry, input_data_pages is 80, frames is 4,
measured_passes is 4, initial_runs is 20, and peak_accounted_frames
is 4. correct_order, no_pins_left, and temporary_files_removed must
all be true.

## I/O decisions

This implementation spools its child to temporary pages before run
generation so the child's working state is closed before all B frames
are used to sort a run. That adds real I/O beyond the textbook baseline.
The report identifies that overhead separately.

When the input occupies exactly B temporary pages, the run is written
before it is streamed. That adds a pass compared with the simple formula,
leaving a frame for the output tuple. Smaller single runs stream directly
from their input frames.

REPORT_M2.md contains the sort measurements. The join benchmark tables
and plots are still pending.
