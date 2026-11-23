#!/usr/bin/env python3
"""Build a human-readable markdown index from actual E2E manifest.json files."""
import argparse
import json
import re
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    args = parser.parse_args()
    root = args.run_dir
    if not root.is_dir():
        print(f'ERROR: run directory missing: {root}', file=sys.stderr)
        return 1
    manifests = sorted(root.glob('TC-*/manifest.json'))
    if not manifests:
        print(f'ERROR: no testcase manifests in {root}', file=sys.stderr)
        return 1
    lines = [f'# E2E evidence index: {root.parent.name} / {root.name}', '',
             'This index lists generated Playwright evidence. Actual pass/fail is established by test assertions and JUnit XML.', '',
             '| TC | Result | Screenshots | Trace |', '|---|---|---|---|']
    for file in manifests:
        record = json.loads(file.read_text(encoding='utf-8'))
        tc_id = record.get('tc_id', '')
        if not re.fullmatch(r'TC-\d+', tc_id):
            print(f'ERROR: invalid TC in {file}', file=sys.stderr)
            return 1
        result = record.get('result', 'UNKNOWN')
        pics = []
        for shot in record.get('screenshots', []):
            name = shot.get('file', '')
            if re.fullmatch(r'[0-9]{2}-[A-Za-z0-9_-]+\.png', name):
                pics.append(f'[{name}]({tc_id}/{name})')
        trace = '[trace.zip](' + tc_id + '/trace.zip)' if record.get('trace_file') == 'trace.zip' else '—'
        lines.append(f'| {tc_id} | {result} | {", ".join(pics) or "—"} | {trace} |')
    (root/'index.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'Wrote {root / "index.md"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
