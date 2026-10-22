#!/usr/bin/env python3
"""Freeze an already installed immutable wheel tuple; no dependency installation."""
import argparse
import json
from pathlib import Path
import sys

HUB=Path(__file__).resolve().parents[1]
if str(HUB) not in sys.path:sys.path.insert(0,str(HUB))
from services.process_composition.artifacts import create_manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-wheel',required=True,type=Path)
    parser.add_argument('--service-wheel',required=True,type=Path)
    parser.add_argument('--frank-wheel',required=True,type=Path)
    parser.add_argument('--native-helper',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    try:
        value=create_manifest({'process-runtime':args.runtime_wheel,'service-management':args.service_wheel,'frank':args.frank_wheel},native_helper=args.native_helper)
        args.output.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
        print(json.dumps({'output':str(args.output.resolve()),'component_count':3,'dependency_count':len(value['dependencies']),'production_ready':False}))
        return 0
    except Exception as error:
        print(json.dumps({'status':'failed','error_type':type(error).__name__,'detail':str(error)}));return 1

if __name__=='__main__':raise SystemExit(main())
