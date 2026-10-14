import argparse
import importlib.metadata
import json
from pathlib import Path
from . import CompositionError, admission, apply_new_run_admission, health, validate_config, verify_contracts


def main():
    parser=argparse.ArgumentParser(description='Hub process composition; no business state or credentials')
    parser.add_argument('action',choices=('check','health','route','check-installed','apply-admission'))
    parser.add_argument('--config',required=True)
    parser.add_argument('--policy')
    parser.add_argument('--expected-composition-digest')
    parser.add_argument('--expected-policy-digest', help='Use absent only to initialize a missing policy')
    parser.add_argument('--frank-config', help='Operator-selected owner-private Frank configuration; health only')
    parser.add_argument('--frank-python', help='Reviewed installed Python executable; health only')
    parser.add_argument('--frank-python-sha256', help='Reviewed Python executable digest; health only')
    parser.add_argument('--frank-wheel', help='Reviewed Frank wheel; health only')
    parser.add_argument('--frank-wheel-sha256', help='Reviewed Frank wheel digest; health only')
    parser.add_argument('--runtime-wheel', help='Reviewed Runtime/SDK wheel; health only')
    parser.add_argument('--runtime-wheel-sha256', help='Reviewed Runtime/SDK wheel digest; health only')
    parser.add_argument('--health-timeout', type=float, default=2, help='Bounded per-probe timeout, at most ten seconds')
    args=parser.parse_args()
    config_path=Path(args.config).resolve()
    try:
        value=validate_config(json.loads(config_path.read_text()))
        verify_contracts(value,{name:config_path.parent/'process-contracts'/f'{name}.json' for name in value['components']})
        if args.action=='check':
            result={'compatible':True,'production_authority_qualified':value['authority_qualified'],'external_effects':value['external_effects']}
        elif args.action=='route':result=admission(value)
        elif args.action=='health':
            from .executor_health import FrankHealthInvocation
            options=(args.frank_python,args.frank_python_sha256,args.frank_config,args.frank_wheel,
                     args.frank_wheel_sha256,args.runtime_wheel,args.runtime_wheel_sha256)
            if any(options) and not all(options):
                raise CompositionError('Frank health requires complete reviewed operator invocation')
            result=health(value,timeout=args.health_timeout,
                frank_invocation=FrankHealthInvocation(*options) if all(options) else None)
        elif args.action=='apply-admission':
            if not args.policy or not args.expected_composition_digest or not args.expected_policy_digest:
                raise CompositionError('Apply requires policy path and both reviewed digest preconditions')
            result=apply_new_run_admission(value,policy_path=args.policy,
                expected_composition_digest=args.expected_composition_digest,
                expected_policy_digest=None if args.expected_policy_digest=='absent' else args.expected_policy_digest)
        else:
            result={}
            for name,component in value['components'].items():
                actual=importlib.metadata.version(component['package'])
                if actual!=component['version']:raise CompositionError('Installed version mismatch for '+name)
                result[name]={'version':actual,'compatible':True}
        print(json.dumps(result,sort_keys=True))
    except (ValueError,OSError,importlib.metadata.PackageNotFoundError) as exc:
        if args.action=='health':
            parser.exit(2,'Composition health check failed\n')
        parser.exit(2,'Composition check failed: '+str(exc)+'\n')

if __name__=='__main__':main()
