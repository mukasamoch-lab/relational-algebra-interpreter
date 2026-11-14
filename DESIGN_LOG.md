# Design log

## 2026-09-24

I worked through EBNF, precedence, and the two groupings of `A union B minus C`. We chose left associativity for `union` and `minus`, contextual keyword attribute names, and errors for repeated projection attributes. AI claimed that an earlier compact `times`/`join` EBNF rule required brackets after `times`; when we read the alternatives against `A times B`, we found that claim was false and rewrote the rule with clearer grouping.

## 2026-09-25

I drafted `GRAMMAR.md` before starting the parser and checked it against nested queries, comments, quoted strings, and tuple lines. The definition format needed newlines to separate tuples while allowing spaces around values. I left the final-line and scanner details marked for verification against code.

## 2026-09-28

With AI assistance, I built the scanner, recursive descent parser, tree printer, relation loader, evaluator, generator, and tests. AI generated substantial code, and I ran the tests and benchmarks and worked through explanations of the implementation. An AI-written whitespace-equivalence test compared entire AST nodes, including character offsets, and failed even though both queries printed the same tree; the failing test revealed that positions should differ, so I changed the assertion to compare the rendered trees. The 25 required cases and additional checks then passed, and a 1,000-by-1,000 nested-loop join recorded one million comparisons.

The first AI-written loader and projection implementation checked every new tuple against every previous tuple, which would make the 64,000-row performance study impractical. The one-million-pair timing and a review of the loops exposed this scaling problem. I changed deduplication to use hash buckets only for candidate lookup, while my explicit `equal_tuple` still decides equality; I also resolved comparison attributes once per operator rather than once per pair. On the same environment, the 1,000-row zero-match join fell from roughly 2.53 seconds to 0.29 seconds. These are local trial measurements, not the submitted benchmark data.

I ran the full seven-size study on my Windows i7-1355U with Python 3.12.10 and used its CSV for the report. The 32,000-row join time was much higher than a smooth continuation of earlier runs, so I kept the measured value and noted the unexplained variation. A second 4,000-row run with match rate 1 produced 4,000 joined rows, yet was slightly faster than the zero-match run; I treated that as insufficient to infer a causal speedup.

## Project 2 library refactor

The Bonus 1 baseline passed all 32 tests before the split. Case 23 changed
because projection now preserves duplicate rows. It expects D1, D2, D1 from
the original fixture and checks that explicit distinct returns D1, D2.
The other original test expectations stayed the same.

A new test found that the parser treated a trailing identifier spelled EOF
as the end-of-input token. End of input now matches only the token kind.
A join test passes one-shot tuple iterators from a source function. The
evaluator reads the right input once so it can revisit those tuples for
each left tuple. This is the library refactor's evaluation behavior, with
the page-budgeted join deferred to Module 2.

## Module 1 storage integration

AI assistance extended the locally written storage classes with a shared
buffer budget, paged catalog snapshots, a CSV loader, CLI commands, and
regression tests. The original 35 tests passed before these additions.

Two problems in the earlier AI-assisted design required corrections.
The original pool keyed pages only by integer page ID. Two table files
can both have page 0, so a shared budget also needs file identity in the
key. A regression test now stores different bytes in page 0 of two files
through one pool and verifies both files after flushing.

The original fetch allocated an incoming full-page bytearray while the
victim's full-page bytearray was still resident. That made page-buffer
allocation exceed B during replacement. The disk manager now supports
read_page_into and replacement reuses the victim's bytearray. New-page
replacement clears the same buffer in place. The earlier heap insert
also allocated a separate full page just to initialize a header. It now
initializes the new pinned frame directly.

Tests cover partial CSV failure, duplicate rows, UTF-8 byte limits,
catalog snapshots spanning multiple pages, early scan closure, dirty
eviction, repeated flushes, and directory overflow past 681 data pages.
The storage examples and capacity/scan measurements are reproducible.
Published trace measurements remain pending receipt of the five files.

## Module 2 first pipeline

AI assistance added the iterator interface and seqscan/filter/project/limit
operators. Conditions reuse the extracted Bonus 1 parser and compiler.
Plan shape and semantic checks happen before opening the iterator chain.
The new tests verify duplicate preservation, one filter comparison per
examined tuple, and pin cleanup when LIMIT stops early.

The design draft explicitly records directory-page scan overhead and the
choice to exclude catalog preparation reads from per-operator execution
I/O. The separate full physical-plan JSON schema was not supplied, so
the limit field n is an interface choice pending comparison with it.
The complete Module 2 report and its required AI-error investigations
remain open while the other operators are built.

## Module 2 external sort

AI assistance added temporary page files, bounded run generation, and
B-1-way merging. The first implementation failed three VARCHAR tests
because decode_record called .decode on a memoryview slice. The regression
traceback identified that the decoder had assumed bytes. It now converts
only the individual string field slice to bytes before decoding.

A second memory issue appeared during design review. Keeping B run pages
pinned while also creating decoded rows or an encoded output-page list
would exceed the budget. The sorter now sorts references into page buffers.
The storage layer streams temporary page fragments directly, and ordinary
iterator pulls reserve a tuple workspace against the shared frame budget.
The N=B boundary test records the resulting extra streaming pass explicitly.

The tests include 80 data pages at B=4, duplicate keys across runs, mixed
directions, UTF-8 ordering, empty input, early LIMIT, nested sorts, and an
injected comparison failure that must clean up all pins and temporary files.
The codec fix, page-view scan option, and pool changes were also checked
against the existing M1 and Bonus 1 tests.

## Module 1 trace completion and Module 2 joins

The instructor authorized self-generated report inputs. The M1 report
now includes the deterministic trace generator and its 40 policy results.
All three joins use the physical plan fields in the module examples.
The four-frame tests compare their output bags and check actual shared
disk counters, early close, and overflow behavior. Serial child
materialization simplifies the frame accounting but adds I/O and makes
block nested-loop blocking during preparation. The design records this
difference for later comparison with the textbook I/O formulas.

## Module 2 grouping and set operators

Both group modes now share aggregate validation and result formatting.
The external sorter is reused for adjacent-group aggregation, oversized
hash partitions, and set merges. An integration check required adding
temporary encodings for derived numeric results, so a sort above AVG
can spool its input and preserve the result type. Four-frame regression
tests exercise both grouping modes, empty inputs, duplicate elimination,
set operations over disk-sized inputs, and early UNION ALL termination.

## Module 2 join benchmarks

The instructor authorized generated fixtures. We measured all 36
size and budget combinations and two skew cases. The wide fixture
gives an exact mapping of one row per page. An eight-kilobyte join
result needs two workspace pages, which accounts for part of the
block nested-loop cost difference from the one-page formula.

## Module 3 compiler

The module examples expose two interface gaps. The executor originally
accepted only two-element aggregate definitions and had no rename
operator. The grouped alias and self-join plans required aggregate
output names and a qualifier change, so the executor now accepts
aggregate triples and rename. A hidden ORDER BY key also cannot be
read from a projection that dropped it. The compiler keeps that key
until after sorting, and the test executes the resulting plan.

The grammar and compiler are handwritten. Tests cover the fifteen
required cases and report compiler errors with source locations.

## Module 4 execution baseline

The Module 3 tests translated plans locally. Moving the translation into
the SQL runner makes the same path available from the CLI and gives Module 4
a fixed baseline for I/O comparisons. The demo includes a player with a
missing team and a matched player below the score threshold. With four
frames, the join returns three matches and the filter and sort return two
rows in descending score order. Timings include physical operator
construction and executor result formatting.

## Module 4 optimizer and integration

The first optimizer draft assumed that every ON conjunct would cross a
join boundary after flattening. A single-sided predicate can instead stay
on the ON clause when pushdown is disabled. That draft could omit the
condition. The regression disables the rules one by one for the combined
key and points condition and checks the returned rows. The optimizer now
keeps written order for such inputs if the predicate has not moved.

Another integration mismatch was multiline SQL strings. The SQL scanner
accepted them while the shared predicate lexer rejected a newline before
the closing quote. A filter against a stored multiline value exposed the
mismatch. The standalone predicate parser now opts into multiline values.
The relational algebra query lexer retains its original behavior.

The first workload fixture used VARCHAR(1200) to make wide rows, which
the existing schema validator immediately rejected. Five VARCHAR(240)
columns make the workload wide within the supported schema instead.
The first workload also omitted AS on subqueries. The compiler stopped
at Q21, which prompted correcting the generated SQL and rerunning the
full workload before recording measurements.

The OpenStack CSV reader initially used the default CSV field limit.
The wide correlated query can put more than 128 KiB of JSON in one
cell. A verifier round trip with a realistically sized CSV exposed
the limit, so the reader now accepts the full exported cell.
