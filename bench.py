"""Compare query speed with and without an index on 500,000 rows."""
import csv, random, time
from miniquery import Table, run

N = 500_000
random.seed(1)
cities = ["Roanoke", "Blacksburg", "Richmond", "Norfolk", "Arlington", "Fairfax", "Reston", "Danville"]
with open("people.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["id", "name", "age", "city", "score"])
    for i in range(N):
        w.writerow([i, f"user{i}", random.randint(18, 90), random.choice(cities), round(random.random() * 100, 2)])

t = Table("people", "people.csv")
queries = [
    ("id = 123456",            "SELECT * FROM people WHERE id = 123456"),
    ("id range (about 50 rows)", "SELECT * FROM people WHERE id >= 250000 AND id < 250050"),
    ("age = 40 AND city",      "SELECT name FROM people WHERE age = 40 AND city = 'Roanoke'"),
]

def timed(sql):
    start = time.perf_counter()
    _, rows, info, _ = run(t, sql)
    return (time.perf_counter() - start) * 1000, len(rows), info

print(f"{N:,} rows\n")
print("--- WITHOUT indexes ---")
before = {}
for label, sql in queries:
    ms, n, info = timed(sql); before[label] = ms
    print(f"{label:28} {ms:9.2f} ms  {n:6} rows  | {info}")

t.create_hash_index("id"); t.create_sorted_index("id"); t.create_hash_index("age")
print("\n--- WITH indexes (id: hash + sorted, age: hash) ---")
for label, sql in queries:
    ms, n, info = timed(sql)
    print(f"{label:28} {ms:9.2f} ms  {n:6} rows  | {info}   ({before[label]/max(ms,0.001):,.0f}x faster)")
