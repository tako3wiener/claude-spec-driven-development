#!/usr/bin/env python3
"""v5 R19 optional: inspect externally signed gateway routing receipt.

Cannot infer a provider's actual downstream model from Claude Code's local
--model label. Gateway operator must publish a signed invocation receipt.
"""
import argparse
import json
from pathlib import Path
import sys
from project_setup import SetupError
from v5_core import external_public_key, verified_signature


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--receipt',type=Path,required=True)
    ap.add_argument('--signature',type=Path,required=True)
    ap.add_argument('--expected-model',required=True)
    args=ap.parse_args()
    try:
        receipt=verified_signature(args.receipt,args.signature,external_public_key())
        if receipt.get('downstream_model')!=args.expected_model:
            raise SetupError('Gateway downstream model does not match the requested one')
        if not receipt.get('gateway_request_id') or not receipt.get('tool_use_verified'):
            raise SetupError('Gateway did not attest tool-use compatibility or request ID')
        print(json.dumps({'status':'VERIFIED_OPERATOR_RECEIPT',
             'downstream_model':receipt['downstream_model'],
             'gateway_request_id':receipt['gateway_request_id'],
             'cost_verified': isinstance(receipt.get('cost'),dict)},ensure_ascii=False))
        return 0
    except (SetupError,ValueError,OSError) as ex:
        print('MODEL UNVERIFIED:',ex,file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
