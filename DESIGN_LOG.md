# Design log

## 2026-09-24

I worked through EBNF, precedence, and the two groupings of `A union B minus C`. We chose left associativity for `union` and `minus`, contextual keyword attribute names, and errors for repeated projection attributes. AI claimed that an earlier compact `times`/`join` EBNF rule required brackets after `times`; when we read the alternatives against `A times B`, we found that claim was false and rewrote the rule with clearer grouping.

## 2026-09-25

I drafted `GRAMMAR.md` before starting the parser and checked it against nested queries, comments, quoted strings, and tuple lines. The definition format needed newlines to separate tuples while allowing spaces around values. I left the final-line and scanner details marked for verification against code.

## 2026-09-28

With AI assistance, I built the scanner, recursive descent parser, tree printer, relation loader, evaluator, generator, and tests. AI generated substantial code, and I ran the tests and benchmarks and worked through explanations of the implementation. An AI-written whitespace-equivalence test compared entire AST nodes, including character offsets, and failed even though both queries printed the same tree; the failing test revealed that positions should differ, so I changed the assertion to compare the rendered trees. The 25 required cases and additional checks then passed, and a 1,000-by-1,000 nested-loop join recorded one million comparisons.

The first AI-written loader and projection implementation checked every new tuple against every previous tuple, which would make the 64,000-row performance study impractical. The one-million-pair timing and a review of the loops exposed this scaling problem. I changed deduplication to use hash buckets only for candidate lookup, while my explicit `equal_tuple` still decides equality; I also resolved comparison attributes once per operator rather than once per pair. On the same environment, the 1,000-row zero-match join fell from roughly 2.53 seconds to 0.29 seconds. These are local trial measurements, not the submitted benchmark data.

I ran the full seven-size study on my Windows i7-1355U with Python 3.12.10 and used its CSV for the report. The 32,000-row join time was much higher than a smooth continuation of earlier runs, so I kept the measured value and noted the unexplained variation. A second 4,000-row run with match rate 1 produced 4,000 joined rows, yet was slightly faster than the zero-match run; I treated that as insufficient to infer a causal speedup.
