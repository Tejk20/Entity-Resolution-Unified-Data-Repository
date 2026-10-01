import io
from app.services.parsers import (
    iter_csv_chunks,
    iter_sql_chunks,
    parse_insert_values,
    read_sql_sample,
    sample_for_mapping,
)

CSV = "Full Name,Email,Mobile Number\nJohn Smith,john@example.com,+1-555-010-0100\nJane Doe,jane.doe@example.com,555-010-0101\n"
csv_path = "data/uploads/_test.csv"
with open(csv_path, "w", encoding="utf-8") as f:
    f.write(CSV)

chunks = list(iter_csv_chunks(csv_path, "batch1", chunk_size=2))
assert chunks[0].table_name == "_test"
assert chunks[0].rows[0]["Full Name"] == "John Smith", chunks[0].rows
assert len(chunks) == 1 and chunks[0].size == 2
print("csv OK:", chunks[0].columns, chunks[0].size)

tab = [
    "name\temail\tphone",
    "John Smith\tjohn@example.com\t555-010-0100",
    "O'Brien\tobrien@example.com\t555-010-0102",
]
tsv_path = "data/uploads/_test.tsv"
with open(tsv_path, "w", encoding="utf-8") as f:
    f.write("\n".join(tab))
chunks = list(iter_csv_chunks(tsv_path, "b2", chunk_size=1))
tsv_rows = [r for ch in chunks for r in ch.rows]
assert tsv_rows[1]["name"] == "O'Brien"
print("tsv OK:", tsv_rows[1])

# SQL dump with multi-row INSERT + escaped strings + COPY
sql = r"""
-- demo dump
DROP TABLE IF EXISTS `hr_employees`;
CREATE TABLE `hr_employees` (
  `id` INT,
  `email` VARCHAR(255),
  `full_name` VARCHAR(255),
  `mobile_number` VARCHAR(64)
) ENGINE=InnoDB;
BEGIN;
INSERT INTO `hr_employees` (`id`, `email`, `full_name`, `mobile_number`) VALUES
(1, 'john@example.com', 'John Smith', '+1-555-010-0100'),
(2, 'jane.doe@example.com', 'O''Brien', '555-010-0101'),
(3, 'x@y.com', E'It\'s tricky', NULL);
COMMIT;
COPY start_team (username, phone) FROM stdin;
jdoe_startup\t555-010-0100
skovac\t020 7946 0104
\.
"""
sql_path = "data/uploads/_test.sql"
with open(sql_path, "w", encoding="utf-8") as f:
    f.write(sql)

tables = {}
for ch in iter_sql_chunks(sql_path, "b3", chunk_size=2):
    tables.setdefault(ch.table_name, []).append(ch)

names = list(tables.keys())
print("sql tables found:", names)
hr = tables["hr_employees"]
flat = [r for ch in hr for r in ch.rows]
assert len(flat) == 3, len(flat)
assert flat[0]["email"] == "john@example.com"
assert flat[1]["full_name"] == "O'Brien", flat[1]
assert flat[2]["full_name"] == "It's tricky", flat[2]
assert flat[2]["mobile_number"] is None
print("sql multi-row insert OK:", flat[1]["full_name"], "|", flat[2]["full_name"])

copys = tables["start_team"]
crecs = [r for ch in copys for r in ch.rows]
assert crecs[0]["username"] == "jdoe_startup" and crecs[0]["phone"] == "555-010-0100"
assert crecs[1]["phone"] == "020 7946 0104"
print("sql COPY OK:", crecs)

pairs = list(parse_insert_values(
    "INTO t (a,b) VALUES (1, 'x'), (2, 'y,z'), (3, NULL)"
))
assert pairs == [[1, "x"], [2, "y,z"], [3, None]], pairs
print("parse_insert_values OK:", pairs)

# sample_for_mapping end-to-end
import pathlib
samples = sample_for_mapping(sql_path, "sql", limit=2)
print("sql sample:", [(t, cols[:2]) for t, cols, _ in samples])
samples_csv = sample_for_mapping(csv_path, "csv", limit=2)
print("csv sample:", [(t, cols) for t, cols, _ in samples_csv])

import os
for f in ("data/uploads/_test.csv", "data/uploads/_test.tsv", "data/uploads/_test.sql"):
    try:
        os.remove(f)
    except OSError:
        pass
print("ALL PARSER CHECKS PASSED")