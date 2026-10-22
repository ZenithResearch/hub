"""Hub composition only: contract pins, component health and admission routing.

No Cases, runs, binding records, execution state or component credentials live here.
Existing runs always remain owned by process-runtime during admission rollback.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from urllib import parse, request, error


class CompositionError(ValueError):
    pass


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def validate_config(value):
    required={'schema','environment','admission_mode','external_effects','authority_qualified','components','secret_references'}
    if type(value) is not dict or set(value)!=required or value['schema']!='hub.process-composition.v1':
        raise CompositionError('Unsupported composition contract')
    if value['environment'] not in ('test','development','production') or value['admission_mode'] not in ('legacy_read_only','shadow','greenfield'):
        raise CompositionError('Unknown environment or admission flag')
    if type(value['external_effects']) is not bool or type(value['authority_qualified']) is not bool:
        raise CompositionError('Composition gates must be explicit booleans')
    if value['external_effects'] and (value['admission_mode']!='greenfield' or not value['authority_qualified']):
        raise CompositionError('External effects require qualified greenfield composition')
    if value['environment']=='production' and value['admission_mode']=='greenfield' and not value['authority_qualified']:
        raise CompositionError('Production admission requires independently qualified native authority')
    components=value['components']
    if type(components) is not dict or set(components)!={'process-runtime','service-management','frank'}:
        raise CompositionError('Independent component owners must all be configured')
    for name,item in components.items():
        if type(item) is not dict or set(item)!={'package','version','endpoint','contract_schema','contract_sha256'}:
            raise CompositionError('Unexpected component configuration')
        if type(item['version']) is not str or not item['version'] or '*' in item['version']:
            raise CompositionError('Component versions must be exact')
        if not isinstance(item['contract_sha256'],str) or len(item['contract_sha256'])!=64 or any(c not in '0123456789abcdef' for c in item['contract_sha256']):
            raise CompositionError('Contract content must be pinned')
        endpoint=parse.urlsplit(item['endpoint'])
        if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or endpoint.path not in ('','/'):
            raise CompositionError('Endpoints cannot contain secrets or selected paths')
        if endpoint.scheme!='https' and not (value['environment']!='production' and endpoint.scheme=='http' and endpoint.hostname in ('127.0.0.1','localhost','::1')):
            raise CompositionError('Components need TLS or explicit loopback development')
    if type(value['secret_references']) is not dict or any(type(v) is not str or not v.startswith(('secret:env:','secret:file:')) for v in value['secret_references'].values()):
        raise CompositionError('Hub stores secret references only')
    return json.loads(canonical(value))


def verify_contracts(config, paths):
    value=validate_config(config)
    if set(paths)!=set(value['components']):
        raise CompositionError('Supply every independent component contract')
    for name,path in paths.items():
        raw=Path(path).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=value['components'][name]['contract_sha256']:
            raise CompositionError('Incompatible contract for '+name)
        json.loads(raw)
    return True


def health(config, timeout=2, *, frank_invocation=None):
    from .executor_health import (bounded_timeout, decode_health, diagnostic, frank_health,
                                  http_readiness, require_runtime_dependency)
    value=validate_config(config)
    bounded_timeout(timeout)
    results={}
    class NoRedirect(request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    opener=request.build_opener(request.ProxyHandler({}),NoRedirect())
    for name,component in value['components'].items():
        if name=='frank':
            results[name]=frank_health(frank_invocation,
                frank_version=component['version'],runtime_version=value['components']['process-runtime']['version'],
                environment=value['environment'],timeout=timeout)
            continue
        result={'live':False,'ready':False,'owner':name,'diagnostics':[]}
        for route in ('live','ready'):
            try:
                try:
                    response=opener.open(component['endpoint'].rstrip('/')+'/'+route,timeout=timeout)
                except error.HTTPError as exc:
                    response=exc
                with response:
                    raw=response.read(16385)
                    if len(raw)>16384:raise ValueError('oversized health response')
                    body=decode_health(raw)
                    if type(body.get(route)) is not bool:
                        raise ValueError('Invalid health contract')
                    result[route]=response.status==200 and body[route] is True
                    if route=='ready':
                        http_readiness(name,result,body)
                        if value['environment']=='production' and result.get('checks',{}).get('authority')=='local_dev_untrusted':
                            result.update(ready=False,reason='native_authority_unqualified',
                                diagnostics=[diagnostic(name,'native_authority_unqualified')])
            except (ValueError,TypeError,RecursionError,UnicodeError):
                result[route]=False
                result.update(reason='health_response_invalid',diagnostics=[diagnostic(name,'health_response_invalid')])
            except Exception:
                result[route]=False
                reason='endpoint_unavailable' if route=='live' else 'readiness_unavailable'
                result.update(reason=reason,diagnostics=[diagnostic(name,reason)])
        if result['live'] is not True:
            result['ready']=False
            if not result['diagnostics']:
                result.update(reason='endpoint_unavailable',diagnostics=[diagnostic(name,'endpoint_unavailable')])
        results[name]=result
    return require_runtime_dependency(results)


def admission(config):
    value=validate_config(config)
    return {'target': 'process-runtime' if value['admission_mode']=='greenfield' else value['admission_mode'],
            'external_effects':value['external_effects'], 'existing_run_owner':'process-runtime'}


def change_admission(config, *, expected_digest, mode):
    value=validate_config(config)
    if hashlib.sha256(canonical(value)).hexdigest()!=expected_digest:
        raise CompositionError('Composition changed during review')
    if mode not in ('legacy_read_only','shadow','greenfield'):
        raise CompositionError('Unknown admission mode')
    value['admission_mode']=mode
    if mode!='greenfield':
        value['external_effects']=False
    # This changes new-work routing only. There is no store/lease/output mutation.
    return validate_config(value)


def legacy_eligibility(snapshot, *, expected_evidence_sha256=None):
    """Compatibility summary of the closed, read-only evidence assessment.

    Historical label/hash-only snapshots fail closed. This is an inventory
    candidate classification, never native authority or migration approval.
    """
    from .migration import assess_legacy_evidence
    assessment = assess_legacy_evidence(snapshot, expected_evidence_sha256=expected_evidence_sha256)
    return {key: assessment[key] for key in ("eligible", "reason", "mode")}


def apply_new_run_admission(config, *, policy_path, expected_composition_digest, expected_policy_digest,
                            operator_python=None, operator_environment=None):
    """Apply exact reviewed composition through the runtime-owned operator CLI.

    This configuration operation stores no Case/run/binding and grants no
    authority. The receiver enforces the flag through its transaction boundary.
    """
    import os
    import subprocess
    import sys
    import tempfile
    value = validate_config(config)
    digest = hashlib.sha256(canonical(value)).hexdigest()
    if digest != expected_composition_digest:
        raise CompositionError('Composition changed during review')
    policy = {'schema': 'runtime.new-run-admission.v1',
              'enabled': value['admission_mode'] == 'greenfield', 'composition_digest': digest}
    payload = {'schema': 'runtime.new-run-admission-apply.v1', 'policy': policy,
               'expected_digest': expected_policy_digest}
    with tempfile.TemporaryDirectory(prefix='process-admission-operator-') as directory:
        file = Path(directory) / 'request.json'
        fd = os.open(file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as target:
            target.write(canonical(payload))
        try:
            result = subprocess.run([operator_python or sys.executable, '-m', 'process_runtime.admission', 'apply',
                '--policy', str(policy_path), '--request-file', str(file)], cwd=directory,
                env=operator_environment, capture_output=True, timeout=30)
            if result.returncode or len(result.stdout) > 4096:
                raise CompositionError('Runtime admission operator rejected the update')
            response = json.loads(result.stdout)
            expected = {'policy_digest': hashlib.sha256(canonical(policy)).hexdigest(),
                        'enabled': policy['enabled'], 'composition_digest': digest}
            if response != {'schema': 'runtime.new-run-admission-result.v1', 'data': expected}:
                raise CompositionError('Runtime admission operator response mismatch; inspect policy before retry')
            return expected
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            if isinstance(exc, CompositionError):
                raise
            raise CompositionError('Admission update outcome requires policy inspection') from None
