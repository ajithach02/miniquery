"""miniquery: a tiny in-memory query engine.

Reads a CSV file and answers queries like:
    SELECT name, age FROM people WHERE age > 30 AND city = 'Roanoke' ORDER BY age DESC LIMIT 5
It has two ways to find rows: scan every row, or use an index. A small
"planner" picks one and EXPLAIN shows its choice.
"""
import bisect
import csv
import operator
import re
import sys

# ---------------------------------------------------------------- 1. TABLE

def infer_column(values):
    """Turn a list of strings into ints, floats, or leave as text."""
    try:
        return [int(v) for v in values]
    except ValueError:
        pass
    try:
        return [float(v) for v in values]
    except ValueError:
        return values


class Table:
    def __init__(self, name, path):
        self.name = name
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            self.columns = reader.fieldnames
            raw = list(reader)
        converted = {c: infer_column([r[c] for r in raw]) for c in self.columns}
        self.rows = [{c: converted[c][i] for c in self.columns} for i in range(len(raw))]
        self.hash_indexes = {}    # column -> {value: [row ids]}
        self.sorted_indexes = {}  # column -> (sorted values, row ids in that order)

    def create_hash_index(self, col):
        idx = {}
        for rid, row in enumerate(self.rows):
            idx.setdefault(row[col], []).append(rid)
        self.hash_indexes[col] = idx

    def create_sorted_index(self, col):
        pairs = sorted((row[col], rid) for rid, row in enumerate(self.rows))
        self.sorted_indexes[col] = ([p[0] for p in pairs], [p[1] for p in pairs])

    def drop_indexes(self):
        self.hash_indexes.clear()
        self.sorted_indexes.clear()

# ---------------------------------------------------------------- 2. PARSER

TOKEN = re.compile(r"\s*(<=|>=|!=|=|<|>|,|\*|'[^']*'|[A-Za-z_][A-Za-z_0-9]*|-?\d+\.?\d*)")
OPS = {"=": operator.eq, "!=": operator.ne, "<": operator.lt,
       "<=": operator.le, ">": operator.gt, ">=": operator.ge}


def tokenize(sql):
    sql = sql.strip().rstrip(";").strip()
    tokens, pos = [], 0
    while pos < len(sql):
        m = TOKEN.match(sql, pos)
        if not m:
            raise SyntaxError(f"Can't read the query near: {sql[pos:pos+15]!r}")
        tokens.append(m.group(1))
        pos = m.end()
    return tokens


def to_value(tok):
    if tok.startswith("'"):
        return tok[1:-1]
    return float(tok) if "." in tok else int(tok)


def parse(sql):
    t = tokenize(sql)
    i = 0

    def peek():
        return t[i].upper() if i < len(t) else None

    def take(expected=None):
        nonlocal i
        if i >= len(t):
            raise SyntaxError("Query ended too early")
        tok = t[i]
        if expected and tok.upper() != expected:
            raise SyntaxError(f"Expected {expected}, got {tok}")
        i += 1
        return tok

    q = {"explain": False, "cols": [], "conds": [], "order": None, "limit": None}
    if peek() == "EXPLAIN":
        take(); q["explain"] = True
    take("SELECT")
    if peek() == "*":
        take(); q["cols"] = ["*"]
    else:
        q["cols"].append(take())
        while peek() == ",":
            take(); q["cols"].append(take())
    take("FROM")
    q["table"] = take()
    if peek() == "WHERE":
        take()
        while True:
            col, op, val = take(), take(), to_value(take())
            if op not in OPS:
                raise SyntaxError(f"Unknown operator {op}")
            q["conds"].append((col, op, val))
            if peek() == "AND":
                take()
            else:
                break
    if peek() == "ORDER":
        take(); take("BY")
        col, desc = take(), False
        if peek() in ("ASC", "DESC"):
            desc = take().upper() == "DESC"
        q["order"] = (col, desc)
    if peek() == "LIMIT":
        take(); q["limit"] = int(take())
    if i != len(t):
        raise SyntaxError(f"Unexpected: {t[i]}")
    return q

# ---------------------------------------------------------------- 3. PLANNER

def plan(table, conds):
    """Choose how to find rows. Returns (method, condition or None)."""
    for c in conds:                       # 1st choice: equality on a hash index
        if c[1] == "=" and c[0] in table.hash_indexes:
            return "hash", c
    for c in conds:                       # 2nd: anything but != on a sorted index
        if c[1] != "!=" and c[0] in table.sorted_indexes:
            return "sorted", c
    return "scan", None


def sorted_lookup(table, col, conds):
    """Use every condition on this column to narrow the range at once."""
    keys, rids = table.sorted_indexes[col]
    lo, hi = 0, len(keys)
    for _, op, val in conds:
        if op == "=":
            lo, hi = max(lo, bisect.bisect_left(keys, val)), min(hi, bisect.bisect_right(keys, val))
        elif op == "<":
            hi = min(hi, bisect.bisect_left(keys, val))
        elif op == "<=":
            hi = min(hi, bisect.bisect_right(keys, val))
        elif op == ">":
            lo = max(lo, bisect.bisect_right(keys, val))
        elif op == ">=":
            lo = max(lo, bisect.bisect_left(keys, val))
    return rids[lo:hi] if lo < hi else []

# ---------------------------------------------------------------- 4. EXECUTOR

def run(table, sql):
    q = parse(sql)
    if q["table"].lower() != table.name.lower():
        raise ValueError(f"No table named {q['table']} (loaded: {table.name})")
    for col in [c for c in q["cols"] if c != "*"] + [c[0] for c in q["conds"]]:
        if col not in table.columns:
            raise ValueError(f"No column named {col}")

    method, chosen = plan(table, q["conds"])

    if method == "hash":
        candidates = table.hash_indexes[chosen[0]].get(chosen[2], [])
        desc = f"HASH LOOKUP on {chosen[0]} {chosen[1]} {chosen[2]!r}"
    elif method == "sorted":
        used = [c for c in q["conds"] if c[0] == chosen[0] and c[1] != "!="]
        candidates = sorted_lookup(table, chosen[0], used)
        desc = f"SORTED INDEX RANGE on {chosen[0]} ({len(used)} condition(s))"
    else:
        candidates = range(len(table.rows))
        desc = "FULL SCAN of every row"

    if method == "sorted":
        rest = [c for c in q["conds"] if c not in used]
    else:
        rest = [c for c in q["conds"] if c != chosen]
    checked = 0
    out = []
    for rid in candidates:
        row = table.rows[rid]
        checked += 1
        if all(OPS[op](row[col], val) for col, op, val in rest):
            out.append(row)

    if q["order"]:
        col, desc_order = q["order"]
        out.sort(key=lambda r: r[col], reverse=desc_order)
    if q["limit"] is not None:
        out = out[: q["limit"]]

    cols = table.columns if q["cols"] == ["*"] else q["cols"]
    result = [[r[c] for c in cols] for r in out]
    info = f"Plan: {desc}; rows examined: {checked} of {len(table.rows)}"
    return cols, result, info, q["explain"]

# ---------------------------------------------------------------- 5. REPL

def main():
    if len(sys.argv) != 2:
        print("Usage: python miniquery.py data.csv")
        return
    path = sys.argv[1]
    name = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    table = Table(name, path)
    print(f"Loaded table '{name}': {len(table.rows)} rows, columns: {', '.join(table.columns)}")
    print("Commands: .index <col> hash|sorted   .noindex   .quit   (or type a query)")
    while True:
        try:
            line = input("miniquery> ").strip()
        except EOFError:
            break
        if not line:
            continue
        if line == ".quit":
            break
        if line == ".noindex":
            table.drop_indexes(); print("Indexes dropped."); continue
        if line.startswith(".index"):
            try:
                _, col, kind = line.split()
                (table.create_hash_index if kind == "hash" else table.create_sorted_index)(col)
                print(f"Created {kind} index on {col}.")
            except Exception as e:
                print("Usage: .index <column> hash|sorted  --", e)
            continue
        try:
            cols, rows, info, explain_only = run(table, line)
        except Exception as e:
            print("Error:", e); continue
        if not explain_only:
            print(" | ".join(cols))
            for r in rows[:20]:
                print(" | ".join(str(x) for x in r))
            if len(rows) > 20:
                print(f"... {len(rows)} rows total, showing 20")
        print(info)


if __name__ == "__main__":
    main()
