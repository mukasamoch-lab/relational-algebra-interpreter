# Module 3 SQL grammar

Keywords are case insensitive. Identifiers preserve case and use ASCII
letters, digits, and underscores, beginning with a letter or underscore.
Integer tokens may have a leading sign. Single-quoted strings escape
a single quote by doubling it. A double hyphen outside a string begins
a comment through the end of the line. The scanner recognizes two-byte
comparators before their one-byte prefixes. Errors carry one-based
line and column positions.

```ebnf
file          = statement, { ";", statement }, [ ";" ] ;
statement     = create | insert | query ;
create        = "CREATE", "TABLE", ident, "(", coldef, { ",", coldef }, ")" ;
coldef        = ident, ( "INT" | "VARCHAR", "(", integer, ")" ) ;
insert        = "INSERT", "INTO", ident, "VALUES", "(", literal, { ",", literal }, ")" ;

query         = select_block, { setop, select_block } ;
setop         = "UNION", [ "ALL" ] | "INTERSECT" | "EXCEPT" ;
select_block  = "(", query, ")" | "SELECT", [ "DISTINCT" ], select_list,
                "FROM", from_list, [ "WHERE", condition ],
                [ "GROUP", "BY", column, { ",", column }, [ "HAVING", condition ] ],
                [ "ORDER", "BY", sort_item, { ",", sort_item } ],
                [ "LIMIT", integer ] ;
select_list   = "*" | select_item, { ",", select_item } ;
select_item   = ( column | aggregate ), [ "AS", ident ] ;
aggregate     = ( "COUNT" | "SUM" | "AVG" | "MIN" | "MAX" ), "(", ( column | "*" ), ")" ;
from_list     = table_ref, ( { ",", table_ref } | { "JOIN", table_ref, "ON", condition } ) ;
table_ref     = ident, [ [ "AS" ], ident ] | "(", query, ")", "AS", ident ;
sort_item     = column, [ "ASC" | "DESC" ] ;
column        = ident, [ ".", ident ] ;
condition     = conjunction, { "OR", conjunction } ;
conjunction   = negation, { "AND", negation } ;
negation      = "NOT", negation | "(", condition, ")" | comparison ;
comparison    = operand, ( "=" | "<>" | "<" | "<=" | ">" | ">=" ), operand ;
operand       = column | literal | aggregate ;
literal       = integer | string ;
```

| Level | Association |
|---|---|
| Parenthesized condition or query | Explicit nesting |
| Comparison | One comparator per comparison |
| NOT | Right associative |
| AND | Left associative |
| OR | Left associative |
| UNION, UNION ALL, INTERSECT, EXCEPT | Equal precedence, left associative |

The set production folds left, so A UNION B EXCEPT C parses as
(A UNION B) EXCEPT C. The WHERE, ON, and HAVING productions share
condition syntax. Allowing aggregate operands in the grammar makes
it possible to diagnose an aggregate in WHERE or ON as a semantic
aggregate error. Only COUNT accepts a star. Bare aliases on base
tables follow the module's worked examples. Derived tables require
AS. Identifier and aggregate output collisions are detected by the
compiler. A projected sort column may be hidden from the final output.

The recursive descent parser follows the precedence productions
directly. The stratified expression grammar follows the technique
explained by Nystrom. [Crafting Interpreters, Parsing Expressions](https://craftinginterpreters.com/parsing-expressions.html)
