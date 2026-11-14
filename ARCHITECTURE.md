# System architecture

The original relational algebra CLI in ra.py still reads named relations
and evaluates an expression tree. Its lexer scans tokens, its recursive
descent parser builds nodes, and ra_engine.py validates schemas and
implements operations. The execution module reuses its condition parsing
and predicate compilation so that row comparisons share an implementation
with the original project.

| Interface | What crosses it |
| --- | --- |
| Shell to SQL frontend | Statement text, with the active catalog for name and type resolution. |
| SQL frontend to optimizer | Logical operator dictionaries with qualified column names. DDL and INSERT use catalog operations directly. |
| Optimizer to execution | Physical operator dictionaries with algorithms, predicates, group modes and sort keys. Estimates are kept alongside that plan. |
| Execution to storage | Iterators request heap tuples and temporary run pages. The shared pool handles pinning and page replacement. |
| Storage to optimizer | Catalog table schema, tuple and page counts, width and column histograms. ANALYZE writes a new snapshot when stats change. |

A query in the shell compiles, rewrites and estimates the logical tree,
then builds the physical operators. Calling next at the root pulls rows
through its inputs. A blocking operator such as sort prepares its
temporary runs before yielding the first row. Every operator measures
its own page reads, writes, comparisons and rows returned. EXPLAIN
predicts the plan without running it. EXPLAIN ANALYZE executes it and
prints the estimation and measurement side by side. SET frames changes
the pool for later statements. LOAD consumes a CSV with columns in schema
order and no header. It can leave already loaded rows in place if a later
row fails.

## What I would change on a restart

I would define a shared expression and schema representation first.
Currently the SQL compiler emits predicate strings and the optimizer
parses them back into trees. Binding a predicate once to column identities
would simplify qualifier translation and avoid interpreting generated text
at every stage. The serialized plan could carry the same structured
expression for the command-line interface.

I would also share the costing interface with the executor. The external
sort and joins can write preparatory spools. The optimizer currently
starts from formulas that simplify those operations. A cost function per
operator that explicitly charges those reads and writes would make
predictions easier to explain. Measured fallback behavior under skew
could then be modeled separately from expected partition costs.
I would persist actual encoded widths per column to improve projection
costing instead of apportioning a table average.

Finally, I would collect joint frequencies for chosen pairs of columns
and test the estimator against correlated inputs from the start.
The marginal histograms are exact summaries of individual columns,
but their product can describe a different population from the one
the storage engine actually scans. I would establish a repeatable
comparison against PostgreSQL early and run it for each new operator,
including duplicates, empty inputs, and hidden sort columns.
