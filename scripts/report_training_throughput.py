"""Report measured global training tokens/s without changing any producer."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve, atomic_json


def report(root, window_steps=100):
    root = resolve(root)
    config = json.loads((root/'main.json').read_text())
    interval, tokens = config['log_every'], config['global_tokens']
    if window_steps < interval or window_steps % interval:
        raise ValueError('Window must be a positive multiple of log_every')
    rows = []
    for line in (root/'training/log.jsonl').read_text().splitlines():
        row = json.loads(line)
        if 'tok_s' not in row:
            continue
        if (type(row['step']) is not int or row['step'] % interval or
                not isinstance(row['tok_s'], (int, float)) or
                not math.isfinite(row['tok_s']) or row['tok_s'] <= 0):
            raise ValueError('Invalid throughput sample')
        # Never average a duplicated or skipped step as a continuous interval.
        if rows and row['step'] != rows[-1]['step']+interval:
            rows = []
        rows.append(row)
    if not rows:
        raise ValueError('No finite throughput samples')
    selected = rows[-(window_steps//interval):]
    steps = len(selected)*interval
    seconds = sum(tokens*interval/r['tok_s'] for r in selected)
    start, end = selected[0]['step']-interval, selected[-1]['step']
    boundaries = {}
    for field in ('eval_every', 'ckpt_every'):
        cadence = config.get(field, 0)
        boundaries[field] = [s for s in range(start, end) if s > 0 and cadence > 0 and s % cadence == 0]
    return dict(utc=datetime.now(timezone.utc).isoformat(), run=str(root),
                latest_step=end, target_step=config['max_steps'],
                latest_global_tokens_per_second=rows[-1]['tok_s'],
                window_start_exclusive=start, window_end_inclusive=end, window_steps=steps,
                requested_window_steps=window_steps, window_complete=steps==window_steps,
                window_global_tokens_per_second=steps*tokens/seconds,
                window_seconds_per_step=seconds/steps, window_steps_per_hour=steps/seconds*3600,
                global_tokens_per_step=tokens, possible_eval_save_boundaries=boundaries,
                scope='Global tokens/s from log timing (all ranks combined). Time-weighted window, not arithmetic mean. In-loop eval/save costs appear in the following log interval; process startup, restore, downtime and final work are excluded. Not end-to-end wall-clock throughput or an ETA.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--window-steps', type=int, default=100)
    parser.add_argument('--output')
    args = parser.parse_args()
    result = report(args.run_dir, args.window_steps)
    if args.output:
        atomic_json(resolve(args.output), result)
    print(json.dumps(result, ensure_ascii=False))
