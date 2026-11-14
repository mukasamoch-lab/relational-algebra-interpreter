# Module 3 design

The scanner and parser produce a syntax tree with source locations.
The compiler walks that tree against the Module 1 catalog, then emits
JSON. Parse requires no catalog. Check produces only errors. Compile
returns a single plan object or an array for a multi-statement file.

## Scope and types

Each FROM entry is keyed by its exposed name, which is its alias if
present. A derived table compiles recursively before its exposed
columns are qualified with its mandatory alias. A JOIN ON resolves
against the left prefix and the new right input. WHERE resolves
against the complete FROM scope. A qualified reference matches one
exact name. An unqualified reference searches short names and reports
all candidates when ambiguous. Star expands in FROM and schema order.

Base types are INT and VARCHAR. VARCHAR comparisons and set
compatibility ignore the declared length. Set output takes the left
names and the larger VARCHAR length. COUNT and SUM use BIGINT, and
AVG uses REAL, matching Module 2. Numeric comparisons allow these
derived types to compare with integer literals. Aggregate arguments
are resolved before grouping, and bare SELECT or HAVING columns
must belong to the grouping keys.

CREATE TABLE and INSERT compile to create_table and insert command
objects. They validate names, schema, and encoded values. The compiler
remembers declarations within the current file, so later statements
can refer to a newly declared table. Compilation does not apply those
commands to the catalog.

## Logical plan conventions

FROM emits scan, rename, cross, or join. WHERE emits select without
pushdown. Grouping precedes HAVING, which uses another select.
Projection and distinct follow, then sort and limit. Set nodes
combine entire SELECT blocks. Every input column reference is
resolved to its plan schema name. Aggregate result names follow
the examples and may be unqualified. The renderer preserves
predicate structure and spells SQL <> as != for the shared
condition parser.

An aggregate is represented by [function, input, output]. An explicit
AS alias determines the output name when the aggregate first appears.
Default names include count_star and avg_Emp_salary. Repeated aggregate
expressions share one calculation. A project uses an aligned aliases
array when output names differ from input names. The executor accepts
these project aliases, aggregate triples, and relation renames.

ORDER BY resolves explicit output aliases first, then FROM columns.
If a sort column is absent from the output, the compiler keeps it in
the first project and drops it after sort. For DISTINCT, a hidden
sort key is rejected because a deduplicated output could correspond
to several sort values. This choice follows PostgreSQL's DISTINCT
restriction. The course's own set precedence still controls parsing.

## Bag semantics

A scan and a project preserve multiplicity. UNION ALL concatenates.
UNION, INTERSECT, and EXCEPT are set operations. SELECT DISTINCT
adds a distinct node over projection. When a grouped projection
keeps every group key, the existing groups are already unique and
the compiler omits the extra node. When projection drops a group
key, distinct is still needed to remove equal projected rows
originating from different groups. The module's redundancy example
applies to the first case.

## Verification and sources

The tests cover all fifteen numbered cases, plus case-insensitive
keywords, signed values, escaped quotes, associativity, aliases,
statement files, and error locations. A test-only mapping executes
representative logical plans through Module 2. Module 4 will provide
the production mapping and physical choices.

Nystrom's expression chapter informed separating scanner state from
parser state and splitting conditions into NOT, AND, and OR levels.
[Crafting Interpreters, Parsing Expressions](https://craftinginterpreters.com/parsing-expressions.html)

PostgreSQL's SELECT reference informed keeping ORDER BY aliases visible,
preserving bag projection, and resolving the hidden DISTINCT sort-key
case. The course specifies case-sensitive identifiers and its own set
precedence. [PostgreSQL SELECT](https://www.postgresql.org/docs/current/sql-select.html)
