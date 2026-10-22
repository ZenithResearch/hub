#!/usr/bin/env python3
"""Reject incompatible installed tuples using the same pre-start verifier."""
from copy import deepcopy
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

HUB=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HUB))
from services.process_composition import CompositionError
from services.process_composition.artifacts import canonical, sha, verify_manifest


def challenge(manifest,config):
    manifest=Path(manifest).resolve()
    baseline=verify_manifest(manifest)
    original=json.loads(manifest.read_text())
    checks=[]
    with tempfile.TemporaryDirectory(prefix='process-artifact-challenges-') as folder:
        directory=Path(folder)
        for name,mutate in (
            ('wheel-digest',lambda value:value['components']['process-runtime'].update(wheel_sha256='0'*64)),
            ('package-version',lambda value:value['components']['service-management'].update(version='99.0.0')),
            ('missing-component',lambda value:value['components'].pop('frank')),
            ('dependency-content',lambda value:value.update(dependency_inventory_sha256='0'*64)),
            ('production-claim',lambda value:value.update(production_ready=True)),
            ('unknown-tuple-field',lambda value:value.update(authority_qualified=True))):
            value=deepcopy(original);mutate(value)
            candidate=directory/(name+'.json');candidate.write_text(json.dumps(value))
            try:verify_manifest(candidate)
            except CompositionError:checks.append({'name':name,'status':'rejected-before-start'})
            else:raise AssertionError('Accepted incompatible tuple: '+name)
        if original['native_helper'] is not None:
            value=deepcopy(original);value['native_helper']['sha256']='0'*64
            candidate=directory/'native-digest.json';candidate.write_text(json.dumps(value))
            try:verify_manifest(candidate)
            except CompositionError:checks.append({'name':'native-verifier-digest','status':'rejected-before-start'})
            else:raise AssertionError('Accepted native verifier mismatch')
        # Exercise a real SDK source overlay in a fresh interpreter, even when the
        # runtime itself still resolves to the reviewed installed distribution.
        overlay=directory/'overlay';package=overlay/'process_runtime_client';package.mkdir(parents=True)
        (package/'__init__.py').write_text('# deliberate incompatible source overlay\n')
        code='''import sys
sys.path.insert(0,sys.argv[1]);sys.path.insert(0,sys.argv[2])
from services.process_composition.artifacts import verify_manifest
from services.process_composition import CompositionError
try: verify_manifest(sys.argv[3])
except CompositionError: raise SystemExit(0)
raise SystemExit(1)
'''
        result=subprocess.run([sys.executable,'-I','-c',code,str(HUB),str(overlay),str(manifest)],cwd=directory,capture_output=True,timeout=30)
        if result.returncode:raise AssertionError('SDK source overlay was not rejected')
        checks.append({'name':'sdk-source-overlay','status':'rejected-before-start'})
        spec=importlib.util.spec_from_file_location('artifact_drill',HUB/'scripts/verify_process_execution.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        try:module.verify(runtime_repo=directory,config_path=config,artifact_manifest=manifest)
        except ValueError as error:
            if 'source checkout overrides' not in str(error):raise
            checks.append({'name':'explicit-checkout-override','status':'rejected-before-start'})
        else:raise AssertionError('Installed drill accepted a checkout override')
        # Alter a dependency file in this disposable installed prefix, then restore
        # the exact bytes before leaving, to test the content lock rather than just
        # a forged inventory digest. No shared package cache or source is edited.
        import importlib.metadata as metadata
        dist=metadata.distribution('attrs')
        target=Path(dist.locate_file('attrs/__init__.py')).resolve()
        if not target.is_relative_to(Path(sys.prefix).resolve()):raise AssertionError('Challenge prefix is not isolated')
        saved=target.read_bytes()
        try:
            target.write_bytes(saved+b'\n# deliberate dependency drift\n')
            try:verify_manifest(manifest)
            except CompositionError:checks.append({'name':'installed-dependency-byte-drift','status':'rejected-before-start'})
            else:raise AssertionError('Installed dependency byte drift was accepted')
        finally:target.write_bytes(saved)
    if verify_manifest(manifest)!=baseline:raise AssertionError('Challenge did not preserve original tuple')
    return {'schema':'hub.process-artifact-challenges.v1','status':'passed','checks':checks,'artifact_tuple':baseline,'production_ready':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-manifest',type=Path,required=True)
    parser.add_argument('--config',type=Path,default=HUB/'config/process-execution.components.json')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:report=challenge(args.artifact_manifest,args.config)
    except Exception as error:report={'schema':'hub.process-artifact-challenges.v1','status':'failed','error_type':type(error).__name__,'detail':str(error),'production_ready':False}
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'status':report['status'],'check_count':len(report.get('checks',[])),'report_sha256':hashlib.sha256(canonical(report)).hexdigest(),'production_ready':False}))
    return 0 if report['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
