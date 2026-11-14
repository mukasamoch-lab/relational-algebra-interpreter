# Apply and verify the Module 1 update

1. Extract this update ZIP.
2. Copy its contents into the existing relational-algebra-interpreter folder.
   Allow Windows to replace the matching files. Keep the existing project
   folder and Git history.
3. Open the existing project in VS Code.
4. Run the commands below in its PowerShell terminal.

```powershell
python -m unittest discover -s tests -v
```

Expected result is 46 passing tests.

The following commands create a new demo database and load two rows.
Choose a fresh directory name if m1_demo already has a table R.

```powershell
python storage.py create-table --db m1_demo --name R --schema "a:INT,b:VARCHAR(20),c:INT"
@'
42,bob,7
43,amy,9
'@ | Set-Content -Encoding ascii m1_sample.csv
python storage.py load --db m1_demo --table R --file m1_sample.csv
python storage.py scan --db m1_demo --table R --frames 2 --json
python storage.py layout --db m1_demo --table R --page 1
```

The scan rows must be [[42,"bob",7],[43,"amy",9]].
Catalog reads are included in the scan's stats.

Test the policy example through the CLI.

```powershell
@'
1
2
3
1
4
2
'@ | Set-Content -Encoding ascii m1_trace.txt
python storage.py trace --db m1_demo --file m1_trace.txt --frames 3 --policy lru --json
python storage.py trace --db m1_demo --file m1_trace.txt --frames 3 --policy clock --json
```

LRU should report 1 hit, 5 misses, 2 evictions, 5 reads, 0 writes.
CLOCK should report 2 hits, 4 misses, 1 eviction, 4 reads, 0 writes.

On Linux, the executable ./storage invokes the same command-line entry
point. Python storage.py works on Windows and Linux.

## Current completion boundary

All five storage commands are implemented and tested. REPORT_M1.md includes
the five self-generated traces authorized by the instructor and the 40
policy measurements. The trace generator can reproduce the files.
