#!/usr/bin/env python3
"""Check Hub's reviewed public contract pins without importing component sources."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from jsonschema import Draft202012Validator
from services.process_composition import validate_config, verify_contracts


def check(path):
    path=Path(path).resolve()
    config=validate_config(json.loads(path.read_text()))
    paths={name:path.parent/'process-contracts'/f'{name}.json' for name in config['components']}
    verify_contracts(config,paths)
    checks={}
    identities=set()
    for name,contract_path in paths.items():
        value=json.loads(contract_path.read_text())
        Draft202012Validator.check_schema(value)
        identity=value.get('$id')
        if not isinstance(identity,str) or not identity or identity in identities:
            raise ValueError('Contract identity must be explicit and unique: '+name)
        identities.add(identity)
        checks[name]={'contract_sha256':hashlib.sha256(contract_path.read_bytes()).hexdigest(),'schema_id':identity}
    return {'schema':'hub.process-contract-check.v1','status':'passed','contracts':checks,'production_ready':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=ROOT/'config/process-execution.components.json')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    try:
        report=check(args.config)
    except Exception as error:
        report={'schema':'hub.process-contract-check.v1','status':'failed','error_type':type(error).__name__,'detail':str(error),'production_ready':False}
    if args.output:args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps(report,sort_keys=True))
    return 0 if report['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
