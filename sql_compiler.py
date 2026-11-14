"""Resolve SQL names and types, then emit canonical logical plans."""

from catalog import validate_schema
from record_codec import encode_record
from sql_parser import Expr, Parser, SQLError


NUMERIC = {"INT", "BIGINT", "REAL"}
SET_OPERATORS = {"union", "union_all", "intersect", "except"}


def resolve(expression, schema):
    name = expression.value
    if "." in name:
        qualifier, _ = name.split(".", 1)
        if not any(column[0].partition(".")[0] == qualifier for column in schema):
            raise SQLError("name", f"unknown table or alias {qualifier!r}", expression.token)
        candidates = [column for column in schema if column[0] == name]
    else:
        candidates = [column for column in schema if column[0].rsplit(".", 1)[-1] == name]
    if not candidates:
        raise SQLError("name", f"unknown column {name!r}", expression.token)
    if len(candidates) > 1:
        choices = ", ".join(column[0] for column in candidates)
        raise SQLError("ambiguity", f"column {name!r} matches {choices}", expression.token)
    return candidates[0]


def project(plan, columns, names=None):
    node = {"op": "project", "columns": columns, "child": plan}
    if names is not None and names != columns:
        node["aliases"] = names
    return node


def literal(expression):
    value = expression.value
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'", "VARCHAR"
    if not -(2**31) <= value < 2**31:
        raise SQLError("type", "integer literal outside signed 32-bit range", expression.token)
    return str(value), "INT"


def condition_text(expression, operand, parent=0):
    precedence = {"or": 1, "and": 2, "not": 3, "comparison": 4}[expression.kind]
    if expression.kind == "comparison":
        left, right = map(operand, expression.args)
        if left[1] != right[1] and not {left[1], right[1]} <= NUMERIC:
            raise SQLError("type", f"cannot compare {left[1]} and {right[1]}", expression.token)
        comparator = "!=" if expression.value == "<>" else expression.value
        result = f"{left[0]} {comparator} {right[0]}"
    elif expression.kind == "not":
        result = "not " + condition_text(expression.args[0], operand, precedence)
    else:
        left, right = (condition_text(item, operand, precedence) for item in expression.args)
        result = f"{left} {expression.kind} {right}"
    return f"({result})" if precedence < parent else result


def plain_operand(expression, schema):
    if expression.kind == "literal":
        return literal(expression)
    if expression.kind == "aggregate":
        raise SQLError("aggregate", "aggregate is unavailable before grouping", expression.token)
    return resolve(expression, schema)[:2]


class Compiler:
    def __init__(self, catalog):
        self.catalog = catalog
        self.declared = {}

    def table_schema(self, table):
        name = table.value
        if name in self.declared:
            return self.declared[name]
        try:
            return self.catalog.get_table(name)["schema"]
        except ValueError as error:
            if not str(error).startswith("unknown table"):
                raise
            raise SQLError("name", str(error), table) from None

    def statement(self, statement):
        kind = statement["kind"]
        if kind == "create_table":
            table = statement["table"]
            if table.value == "catalog":
                raise SQLError("schema", "table name 'catalog' is reserved", table)
            if table.value in self.declared or table.value in self.catalog.list_tables():
                raise SQLError("schema", f"table {table.value!r} already exists", table)
            schema = []
            for column, column_type, *options in statement["schema"]:
                schema.append([column.value, column_type, *(token.value for token in options)])
            try:
                schema = validate_schema(schema)
            except ValueError as error:
                raise SQLError("schema", str(error), table) from None
            self.declared[table.value] = schema
            return {"op": kind, "table": table.value, "schema": schema}
        if kind == "insert":
            table = statement["table"]
            schema = self.table_schema(table)
            values = [expression.value for expression in statement["values"]]
            try:
                encode_record(schema, values)
            except ValueError as error:
                raise SQLError("type", str(error), table) from None
            return {"op": kind, "table": table.value, "values": values}
        return self.query(statement)[0]

    def query(self, query):
        kind = query["kind"]
        if kind not in SET_OPERATORS:
            return self.select(query)
        left, left_schema = self.query(query["left"])
        right, right_schema = self.query(query["right"])
        if [c[1] for c in left_schema] != [c[1] for c in right_schema]:
            raise SQLError("schema", "set inputs must have matching column counts and types", query["token"])
        schema = [
            [a[0], a[1], max(a[2], b[2])] if a[1] == "VARCHAR" else a
            for a, b in zip(left_schema, right_schema)
        ]
        return {"op": kind, "left": left, "right": right}, schema

    def sources(self, query):
        scope = []
        qualifiers = set()
        plan = None
        for (table, subquery, alias), on in query["sources"]:
            name = (alias or table).value
            if name in qualifiers:
                raise SQLError("ambiguity", f"duplicate alias {name!r}", alias or table)
            qualifiers.add(name)
            if subquery is None:
                child = {"op": "scan", "table": table.value}
                schema = self.table_schema(table)
            else:
                child, schema = self.query(subquery)
            qualified = [[f"{name}.{c[0].rsplit('.', 1)[-1]}", *c[1:]] for c in schema]
            if len({c[0] for c in qualified}) != len(qualified):
                raise SQLError("ambiguity", f"duplicate exposed columns for {name!r}", alias or table)
            if alias is not None:
                child = {"op": "rename", "to": alias.value, "child": child}
            scope.extend(qualified)
            if plan is None:
                plan = child
            elif on is None:
                plan = {"op": "cross", "left": plan, "right": child}
            else:
                predicate = condition_text(on, lambda expr: plain_operand(expr, scope))
                plan = {"op": "join", "left": plan, "right": child, "condition": predicate}
        return plan, scope

    def select(self, query):
        plan, scope = self.sources(query)
        if query["where"] is not None:
            predicate = condition_text(query["where"], lambda expr: plain_operand(expr, scope))
            plan = {"op": "select", "condition": predicate, "child": plan}
        items = query["items"]
        if items is None:
            items = [(Expr("column", c[0], query["token"]), None) for c in scope]
        grouped = query["keys"] is not None or any(e.kind == "aggregate" for e, _ in items)
        keys = [resolve(key, scope)[0] for key in query["keys"] or []]
        if len(set(keys)) != len(keys):
            raise SQLError("aggregate", "duplicate grouping column", query["token"])
        aggregates = {}
        aggregate_names = set(keys)

        def bind_aggregate(expression, alias=None):
            function = expression.value
            argument = expression.args[0]
            if argument.kind == "star":
                if function != "count":
                    raise SQLError("aggregate", "only COUNT accepts *", argument.token)
                column = ["*"]
            else:
                column = resolve(argument, scope)
            if function in ("sum", "avg") and column[1] not in ("INT", "BIGINT"):
                raise SQLError("type", f"{function.upper()} requires an integer column", argument.token)
            signature = function, column[0]
            if signature not in aggregates:
                name = alias.value if alias else f"{function}_{'star' if column[0] == '*' else column[0].replace('.', '_')}"
                if name in aggregate_names:
                    raise SQLError("aggregate", f"duplicate aggregate output {name!r}", expression.token)
                aggregate_names.add(name)
                data_type = (
                    ["BIGINT"] if function in ("count", "sum")
                    else ["REAL"] if function == "avg" else column[1:]
                )
                aggregates[signature] = [name, *data_type]
            return aggregates[signature]

        def group_column(expression):
            column = resolve(expression, scope)
            if grouped and column[0] not in keys:
                raise SQLError("aggregate", f"column {column[0]!r} is not grouped", expression.token)
            return column

        columns = []
        aliases = {}
        output = []
        for expression, alias in items:
            column = (bind_aggregate(expression, alias) if expression.kind == "aggregate"
                      else group_column(expression))
            name = alias.value if alias else column[0]
            columns.append(column[0])
            output.append([name, *column[1:]])
            if alias is not None:
                if name in aliases:
                    raise SQLError("ambiguity", f"duplicate output alias {name!r}", alias)
                aliases[name] = name

        def having_operand(expression):
            if expression.kind == "literal":
                return literal(expression)
            if expression.kind == "aggregate":
                return bind_aggregate(expression)[:2]
            return group_column(expression)[:2]

        having = None
        if query["having"] is not None:
            having = condition_text(query["having"], having_operand)
        if grouped:
            plan = {
                "op": "group_by", "keys": keys,
                "aggregates": [[function, column, result[0]]
                               for (function, column), result in aggregates.items()],
                "child": plan,
            }
        if having is not None:
            plan = {"op": "select", "condition": having, "child": plan}

        names = [column[0] for column in output]
        if len(set(names)) != len(names):
            raise SQLError("ambiguity", "duplicate output columns require distinct aliases", query["token"])
        sort_keys = []
        hidden = []
        for expression, direction in query["order"]:
            if expression.value in aliases:
                name = aliases[expression.value]
            else:
                column = group_column(expression)
                if column[0] in columns:
                    name = names[columns.index(column[0])]
                else:
                    name = column[0]
                    if name not in hidden:
                        hidden.append(name)
            sort_keys.append([name, direction])
        distinct = query["distinct"] and not (grouped and set(keys) <= set(columns))
        if distinct and hidden:
            raise SQLError("schema", "DISTINCT requires ORDER BY columns in the output", query["order"][0][0].token)
        plan = project(plan, columns + hidden, names + hidden)
        if distinct:
            plan = {"op": "distinct", "child": plan}
        if sort_keys:
            plan = {"op": "sort", "keys": sort_keys, "child": plan}
        if hidden:
            plan = project(plan, names)
        if (limit := query["limit"]) is not None:
            if limit.value < 0:
                raise SQLError("syntax", "LIMIT must be nonnegative", limit)
            plan = {"op": "limit", "n": limit.value, "child": plan}
        return plan, output


def compile_sql(text, catalog):
    compiler = Compiler(catalog)
    plans = [compiler.statement(statement) for statement in Parser(text).statements()]
    return plans[0] if len(plans) == 1 else plans
