"""Convert the supplied Wind matrix to one row per observed market close.

Usage: python import_wind_closes.py SOURCE.csv OUTPUT.csv --through YYYY-MM-DD
The --through cutoff must be the latest fully settled trading date.
"""

import argparse
import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

SYMBOLS = ('^DJI', '^GSPC', '^IXIC', '^FTSE', '^FCHI', '^GDAXI')
WIND_CODES = ('DJI.GI', 'SPX.GI', 'IXIC.GI', 'FTSE.GI', 'FCHI.GI', 'GDAXI.GI')
US = set(SYMBOLS[:3])
US_HOLIDAYS_2026 = {date(2026, 9, 7)}


def convert(source: Path, through: date):
    table = list(csv.reader(io.StringIO(source.read_bytes().decode('gbk'), newline='')))
    if len(table) < 3 or any(len(row) != 13 for row in table):
        raise ValueError('Expected Wind 13-column previous/close export')
    for offset, code in enumerate(WIND_CODES):
        if code not in table[1][offset + 1] or code not in table[1][offset + 7]:
            raise ValueError(f'Unexpected symbol or column order at {code}')
    result = []
    previous = None
    dates = set()
    for row in table[2:]:
        day = datetime.strptime(row[0], '%m/%d/%Y').date()
        if day in dates or (previous and day <= previous):
            raise ValueError(f'Duplicate or out-of-order date: {day}')
        dates.add(day)
        if previous:
            for index in range(6):
                if Decimal(row[index + 1]) != Decimal(prior[index + 7]):
                    raise ValueError(f'Previous-close chain breaks at {day} {SYMBOLS[index]}')
        previous, prior = day, row
        if day > through or day.weekday() >= 5:
            continue
        for index, symbol in enumerate(SYMBOLS):
            if symbol in US and day in US_HOLIDAYS_2026:
                continue
            try:
                close = Decimal(row[index + 7])
            except InvalidOperation as error:
                raise ValueError(f'Invalid close at {day} {symbol}') from error
            if not close.is_finite() or close <= 0:
                raise ValueError(f'Invalid close at {day} {symbol}')
            result.append((day.isoformat(), symbol, str(close), 'wind_export'))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--through', type=date.fromisoformat, required=True)
    parser.add_argument('--merge-existing', action='store_true',
                        help='Keep later saved dates; Wind replaces overlapping Yahoo values')
    args = parser.parse_args()
    rows = convert(args.source, args.through)
    if args.merge_existing and args.output.exists():
        with args.output.open(newline='', encoding='utf-8-sig') as file:
            previous = {(row['date'], row['symbol']):
                        (row['date'], row['symbol'], row['close'], row['source'])
                        for row in csv.DictReader(file)}
        previous.update({(day, symbol): (day, symbol, close, source)
                         for day, symbol, close, source in rows})
        rows = sorted(previous.values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(('date', 'symbol', 'close', 'source'))
        writer.writerows(rows)
    print(f'Exported {len(rows)} market closes through {args.through}')


if __name__ == '__main__':
    main()
