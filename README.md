# miniquery

A small in-memory query engine written in Python. It reads a CSV file, answers a subset of SQL, and chooses between a full scan and an index for each query.

I built it to understand how a database decides *how* to answer a query, not just what the answer is.

## What it does

- Loads a CSV into memory and detects whether each column is integer, decimal, or text.
- Parses queries of this form:

  ```
  SELECT col1, col2 | *
  FROM table
  [WHERE col op value [AND col op value ...]]
  [ORDER BY col [ASC | DESC]]
  [LIMIT n]
  ```

  Supported operators: `=`, `!=`, `<`, `<=`, `>`, `>=`.
- Supports two kinds of index:
  - **Hash index** for equality lookups (`id = 5`).
  - **Sorted index** with binary search for equality and range lookups (`age > 30`, `id >= 100 AND id < 200`).
- Uses a simple rule-based **planner**: hash lookup first, then sorted index, then full scan. Any conditions not covered by the index are checked on the rows it returns.
- `EXPLAIN` shows which plan was chosen and how many rows were examined.

## Run it

Requires Python 3.8 or newer. No other packages.

```
python3 bench.py                    # builds a 500,000-row CSV and times queries with and without indexes
python3 miniquery.py people.csv     # interactive prompt
```

Commands at the prompt:

```
.index <column> hash|sorted    create an index
.noindex                       drop all indexes
.quit                          exit
EXPLAIN SELECT ...             show the plan without printing rows
```

## Benchmark (500,000 rows, one run on a MacBook Pro)

| Query | No index | With index |
|---|---|---|
| `id = 123456` | ~101 ms (scans 500,000 rows) | ~0.04 ms (examines 1 row) |
| `id >= 250000 AND id < 250050` | ~106 ms | ~0.06 ms (examines 50 rows) |
| `age = 40 AND city = 'Roanoke'` | ~113 ms | ~11 ms (examines 7,007 rows) |

Timings vary by machine. The third query is slower than the others because the index only narrows the search by `age`. The `city` condition is still checked on each of those rows.

## What it does not do

- Data lives in memory only. Nothing is written to disk, and indexes are rebuilt each run.
- No joins, `OR`, `GROUP BY`, aggregates (`COUNT`, `SUM`), or `INSERT`/`UPDATE`/`DELETE`.
- The planner is rule-based. It does not estimate costs, so it can pick a poor index (for example, a very common value) when a scan would be faster.
- `ORDER BY` always sorts the result. It doesn't use a sorted index to skip the sort.
- Comparing a number column to a text value raises an error instead of being handled.
- This is a learning project, not a database for real use.

## Files

- `miniquery.py`: the table, parser, planner, executor, and interactive prompt.
- `bench.py`: the benchmark.
