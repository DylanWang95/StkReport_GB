"""Small, auditable close-price store shared by Wind seed and daily reports."""

import csv
import math
import os
from datetime import date
from pathlib import Path

FIELDS = ('date', 'symbol', 'close', 'source')


class CloseStore:
    def __init__(self, path):
        self.path = Path(path)
        self.rows = {}
        if not self.path.exists():
            raise FileNotFoundError(f'历史收盘底库缺失: {self.path}')
        with self.path.open(newline='', encoding='utf-8-sig') as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(FIELDS):
                raise ValueError(f'底库列名不匹配: {reader.fieldnames}')
            for row in reader:
                day = date.fromisoformat(row['date'])
                close = float(row['close'])
                if not math.isfinite(close) or close <= 0:
                    raise ValueError(f'底库价格无效: {row}')
                key = (row['symbol'], day)
                if key in self.rows:
                    raise ValueError(f'底库重复日期: {key}')
                self.rows[key] = {'date': day, 'symbol': row['symbol'],
                                  'close': close, 'source': row['source']}
        self.changed = False

    def previous(self, symbol, target_date):
        candidates = [row for (ticker, day), row in self.rows.items()
                      if ticker == symbol and day < target_date]
        return max(candidates, key=lambda row: row['date']) if candidates else None

    def add_if_missing(self, symbol, day, close, source):
        if close is None or not math.isfinite(close) or close <= 0:
            return
        key = (symbol, day)
        existing = self.rows.get(key)
        if existing:
            if abs(existing['close'] - close) / existing['close'] > 0.0001:
                print(f'⚠️ 底库冲突，保留原值: {symbol} {day} '
                      f"{existing['close']} ({existing['source']}) vs {close} ({source})")
            return
        self.rows[key] = {'date': day, 'symbol': symbol, 'close': close, 'source': source}
        self.changed = True

    def save(self):
        if not self.changed:
            return
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.writer(handle)
            writer.writerow(FIELDS)
            for row in sorted(self.rows.values(), key=lambda item: (item['date'], item['symbol'])):
                writer.writerow((row['date'].isoformat(), row['symbol'],
                                 f"{row['close']:.10g}", row['source']))
        os.replace(temporary, self.path)
        self.changed = False
