#!/usr/bin/env python3
"""Inspect Synthea test data to find gender issue."""

import csv

with open('tests/data/synthea_sample/patients.csv', 'r') as f:
    reader = csv.DictReader(f)
    rows = list(reader)
    print(f'Total patients in test data: {len(rows)}')
    print()
    print('First 5 patients:')
    print('=' * 80)
    for i, row in enumerate(rows[:5], 1):
        first = row.get("FIRST", "")
        last = row.get("LAST", "")
        gender = row.get("GENDER", "")
        birthdate = row.get("BIRTHDATE", "")
        marital = row.get("MARITAL", "")
        print(f'{i}. {first} {last}')
        print(f'   GENDER = {repr(gender)}')
        print(f'   BIRTHDATE = {birthdate}')
        print(f'   MARITAL = {repr(marital)}')
        print()
