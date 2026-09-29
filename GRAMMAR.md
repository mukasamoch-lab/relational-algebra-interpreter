# Relational algebra language — grammar

This document defines the ASCII syntax accepted by the interpreter.

## EBNF notation

`X = ... ;` defines a rule; `,` means *followed by*; `|` means *either*; `{ ... }` means zero or more repetitions; `[ ... ]` means optional; parentheses group alternatives. Quoted terminals such as `"("`, `","`, and `"union"` are characters or words in the input. `EOF`, `NEWLINE`, and `TAB` denote end of input, a line ending, and a tab. Ordinary spaces and tabs between query tokens are ignored. In relation files, line endings remain tokens because they separate tuples.

## Queries

```ebnf
query             = expression, EOF ;
expression        = intersect_expr, { ("union" | "minus"), intersect_expr } ;
intersect_expr    = product_expr, { "intersect", product_expr } ;
product_expr      = primary,
                    { ("times", primary)
                    | ("join", "[", condition, "]", primary) } ;
primary           = identifier
                  | "(", expression, ")"
                  | "select", "[", condition, "]", "(", expression, ")"
                  | "project", "[", attribute_list, "]", "(", expression, ")"
                  | "rename", "[", identifier, "]", "(", expression, ")" ;
attribute_list    = attribute_ref, { ",", attribute_ref } ;
attribute_ref     = identifier, [ ".", identifier ] ;

condition         = or_condition ;
or_condition      = and_condition, { "or", and_condition } ;
and_condition     = not_condition, { "and", not_condition } ;
not_condition     = ("not", not_condition) | condition_atom ;
condition_atom    = comparison | "(", condition, ")" ;
comparison        = operand, comparison_op, operand ;
comparison_op     = "=" | "!=" | "<" | "<=" | ">" | ">=" ;
operand           = number | quoted_string | attribute_ref ;
```

An unquoted word in a condition is an attribute reference, not a string constant. For example, `A=B` compares columns, while `A='B'` compares a column with text. A keyword such as `union` may serve as an attribute identifier in `select[union=3](R)`; words are scanned as identifiers and interpreted by the parser in context. Even `not` is an attribute if followed by a comparison operator (for example, `not=3`); otherwise it introduces negation. In expression operator positions, `union`, `intersect`, `minus`, `times`, and `join` are operators. The prefix words `select`, `project`, and `rename` introduce unary expressions when followed by `[`. A qualified reference has exactly one dot. A reference with a short name shared by two input attributes is a name error unless qualified.

## Relation definitions

The following rules describe a relation file containing definitions. A comment occupies a whole line (possibly preceded by spaces or tabs). A nonempty tuple occupies one line; an empty relation may use `{}`. Spaces and tabs may surround tokens in a definition but never split a bare value.

```ebnf
relation_file     = { outside_line }, EOF ;
outside_line      = blank_line | comment_line | definition ;
definition        = hspace, identifier, hspace, "(", hspace,
                    definition_attrs, hspace, ")", hspace, "=", hspace,
                    ( ("{", hspace, "}", hspace, [ NEWLINE ])
                    | ("{", hspace, NEWLINE, { body_line },
                       hspace, "}", hspace, [ NEWLINE ]) ) ;
definition_attrs  = identifier, { hspace, ",", hspace, identifier } ;
body_line         = tuple_line | comment_line | blank_line ;
tuple_line        = hspace, value, { hspace, ",", hspace, value },
                    hspace, NEWLINE ;
comment_line      = hspace, "//", { comment_char }, NEWLINE ;
blank_line        = hspace, NEWLINE ;
hspace            = { " " | TAB } ;
value             = number | quoted_string | bare_string ;
```

`comment_char` is any character other than a line ending. `NEWLINE` accepts LF or CRLF. The final line of a file may end at `EOF` instead of a newline. A line beginning `//` inside a quoted string is data; comments are recognized only at the start of a line outside strings. A closing brace ends the definition and is not a tuple. Each tuple must have exactly as many values as the definition has attribute names. Duplicate attribute names and duplicate names in a projection list cause a schema error. Duplicate tuples in input or output collapse according to the engine's tuple-equality rule. These are semantic constraints, beyond what this grammar alone can express.

## Lexical rules

```ebnf
identifier        = letter, { letter | digit | "_" } ;
number            = [ "-" ], digit, { digit }, [ ".", digit, { digit } ] ;
quoted_string     = "'", { non_quote | "''" }, "'" ;
bare_string       = bare_char, { bare_char } ;
digit             = "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" ;
letter            = "A" | ... | "Z" | "a" | ... | "z" ;
```

`non_quote` is any character except `'`, CR, or LF. `bare_char` is any character except whitespace, comma, quote, parentheses, or braces. Bare strings occur only in relation tuple data. A data value matching `number` is numeric; otherwise an unquoted token is a string. `E1` and `John` are strings, `32` and `-30` are numbers. A quote, space, comma, or parenthesis within a string requires quotes. Inside a quoted string `''` represents one literal quote; thus `'O''Brien'` represents `O'Brien`. This design accepts decimal numbers such as `3.14`, but requires digits on both sides of the decimal point. It does not accept `.5`, `3.`, exponent notation, or a leading `+`.

The handwritten scanner consumes a complete quoted string before recognizing any punctuation inside it. For `>=`, `<=`, and `!=`, it looks ahead and emits the longest matching comparison token; in `Age>-30`, it emits `>`, then `-30`. Tokens retain their source offsets for error messages. A malformed or unterminated string is a lexical error.

## Precedence and associativity

| Strength (low to high) | Operators | Associativity | Enforcing rule |
| --- | --- | --- | --- |
| 1 | `union`, `minus` | Left | `expression` repetition, folded left |
| 2 | `intersect` | Left | `intersect_expr` repetition, folded left |
| 3 | `times`, `join[c]` | Left | `product_expr` repetition, folded left |
| 4 | Relation names, parentheses, `select`, `project`, `rename` | Explicit input parentheses | `primary` |

For conditions, `or` is weakest, `and` is next, and `not` is strongest among logical operators. Repeated `or` and `and` group left, while repeated `not` nests right. Comparisons form atoms; explicit condition parentheses override the order. Each repeated binary expression rule builds its next node with the accumulated tree as its left child. Thus `A union B minus C` means `(A union B) minus C`, and `A minus B minus C` means `(A minus B) minus C`.

## Ambiguity of a naive grammar

Consider the deliberately naive grammar from the assignment:

```ebnf
Expr = Expr, "union", Expr
     | Expr, "minus", Expr
     | "(", Expr, ")"
     | identifier ;
```

It allows both trees for the same unparenthesized input `A union B minus C`:

```text
      minus                 union
     /     \               /     \
  union     C             A      minus
  /   \                          /   \
 A     B                        B     C
```

Take three one-column relations with the same attribute name and numeric type: `A(x)={1}`, `B(x)={2}`, `C(x)={1}`. The left tree gives `({1} ∪ {2}) − {1} = {2}`. The right tree gives `{1} ∪ ({2} − {1}) = {1,2}`. Because the results differ, the grouping matters. The stratified `expression` / `intersect_expr` / `product_expr` / `primary` grammar above forces the left tree: `union` and `minus` have equal precedence and are folded left.

Associativity also matters for `A minus B minus C`. With `A(x)={1}`, `B(x)={1}`, and `C(x)={1}`, the chosen left grouping gives `({1}−{1})−{1}={}`, while the right grouping gives `{1}−({1}−{1})={1}`.

## Parsing approach

Use a handwritten, top-down recursive descent parser. Each precedence rule becomes a parsing function. It reads a tighter expression first, then loops while the next token is an operator at its own level, folding a new tree node around the accumulated left tree. Unary expressions and parenthesized expressions recurse to parse their contents. This matches the EBNF closely and makes source positions available for useful syntax errors. The naive left-recursive rule `Expr = Expr, "union", Expr | ...` would call `parse_expr` again before consuming any token and recurse indefinitely. Our `expression = intersect_expr, { ... }` first consumes a tighter expression and uses a loop for repetition, avoiding direct left recursion. The same pattern is used for `intersect_expr` and `product_expr`.

## Sources and AI review

- Robert Nystrom, [*Crafting Interpreters*: Scanning](https://craftinginterpreters.com/scanning.html) and [Parsing Expressions](https://craftinginterpreters.com/parsing-expressions.html). Used to check scanning, recursive descent, precedence levels, and the left-recursion issue.
- The assignment's Sections 4–7 supply the syntax, semantics, and required tests. Operator precedence, decimal spelling, empty-body form, and repeated projection handling are design choices documented here.
- AI initially claimed that a compact `times`/`join` rule required brackets after `times`. On reading its alternatives carefully, that claim was wrong: the rule was valid but unclear. We rewrote the alternatives as complete parenthesized branches. The dated `DESIGN_LOG.md` records other observed issues and how we found them.
