#!/usr/bin/env python3
"""Isolated public-contract cross-component verification; never a live cutover."""
from __future__ import annotations
import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib import request
import uuid

HUB = Path(__file__).resolve().parents[1]

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

def require(condition, message):
    if not condition: raise AssertionError(message)

def wait_until(read, predicate, *, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = read()
        if predicate(value): return value
        time.sleep(.05)
    raise AssertionError('Timed out waiting for isolated component state')

class Provider:
    def __init__(self, module, directory, source_paths, *, production=False, admission_policy=None):
        self.module, self.directory, self.source_paths = module, directory, source_paths
        self.production = production
        self.admission_policy = admission_policy
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); self.port = sock.getsockname()[1]
        self.endpoint = f'http://127.0.0.1:{self.port}'
        self.process = None
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    def start(self):
        command = [sys.executable, '-m', self.module, '--database', str(self.directory / 'state.db'), '--port', str(self.port), '--environment', 'production' if self.production else 'test']
        if self.admission_policy is not None:
            command += ['--admission-policy', str(self.admission_policy)]
        if not self.production:
            command.append('--local-dev-untrusted')
            if self.module == 'process_runtime': command += ['--local-dev-subject', 'integration-fixture']
        self.log = (self.directory / 'provider.log').open('ab')
        self.process = subprocess.Popen(command, stdout=self.log, stderr=subprocess.STDOUT,
            env={'PATH': '/usr/bin:/bin', 'PYTHONUNBUFFERED': '1', **({'PYTHONPATH': ':'.join(map(str, self.source_paths))} if self.source_paths else {})})
        opener = request.build_opener(request.ProxyHandler({}))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            require(self.process.poll() is None, f'{self.module} failed startup')
            try:
                with opener.open(self.endpoint + '/live', timeout=.5) as response:
                    if json.loads(response.read())['live'] is True: return self
            except Exception: time.sleep(.05)
        self.stop(); raise AssertionError(f'{self.module} did not become live')
    def stop(self):
        if self.process is not None:
            self.process.terminate()
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(timeout=5)
            self.process = None
            self.log.close()
    def restart(self):
        self.stop(); return self.start()

class FixtureIntegration:
    """Conforming transport fixture with no network, subprocess or file effects."""
    contract_version = 'frank.integration.v1'
    provider_idempotent = False
    integration_id = 'fixture'
    def __init__(self):
        self.sent, self.events, self.cancelled = [], [], []
        self.ready = False; self.outcome = 'succeeded'; self.failure = False
    async def connect(self): self.ready = True
    async def close(self): self.ready = False
    async def capabilities(self): return frozenset({'send','receive','health','cancel','reconcile'})
    async def health(self): return {'ready': self.ready}
    def result(self, work, outcome=None):
        from frank_executor.model import digest
        outcome = outcome or self.outcome
        return {'schema':'frank.result.v1', 'event_id':'result-'+digest({'effect':work['effect_id'],'outcome':outcome})[:48],
            **{key:work[key] for key in ('binding','actor_id','integration_id','route','effect_id')},
            'provider_reference':'fixture-'+work['effect_id'], 'outcome':outcome,
            'outputs':{'answer':work['inputs']['number']*2 if 'number' in work['inputs'] else work['inputs']['left']+work['inputs']['right']} if outcome=='succeeded' else {}}
    async def send(self, work):
        self.sent.append(deepcopy(work))
        if self.failure: raise TimeoutError('Synthetic lost acknowledgement')
        return self.result(work)
    async def receive(self, cursor):
        events, self.events = self.events, []
        return events, 'fixture-cursor'
    async def cancel(self, work): self.cancelled.append(work); return False
    async def reconcile(self, work, reference): return None

class CheckSession:
    def __init__(self, runtime, frank_path, directory):
        from process_runtime_client import RuntimeClient, LocalDevAuthority
        from frank_executor.authority import LocalDevAuthority as FrankAuthority, LocalDevResults
        from frank_executor.registry import Registry
        from frank_executor.runtime import RuntimeGateway, GeneratedClientAdapter, ContractValidator
        from frank_executor.executor import Executor
        from frank_executor.journal import Journal
        self.runtime_provider = runtime
        self.client = RuntimeClient(runtime.endpoint, authority=LocalDevAuthority(environment='test'))
        self.adapter = FixtureIntegration()
        self.registry = Registry({'schema':'frank.integrations.v1','integrations':[{'id':'fixture','plugin':'fixture.local','enabled':True,'config':{}}],
            'actors':[{'actor_id':'integration-worker','integration_id':'fixture','capabilities':['compute'],'resources':[],'route':'fixture-only'}]}, {'fixture.local':lambda *_:self.adapter})
        self.journal_path = directory / 'frank.db'
        self.journal = Journal(self.journal_path)
        signer = LocalDevAuthority(environment='test')
        authority = FrankAuthority(lambda raw: signer(json.loads(raw)), environment='test', enabled=True)
        from importlib.resources import files
        schema = json.loads(files('process_runtime_client').joinpath('runtime-v1.schema.json').read_text())
        self.gateway = RuntimeGateway(GeneratedClientAdapter(RuntimeClient(runtime.endpoint)), authority, self.journal, ContractValidator(schema))
        self.executor = Executor(self.gateway,self.registry,self.journal,LocalDevResults(environment='test',enabled=True),environment='test')
        asyncio.run(self.registry.connect()); asyncio.run(self.executor.register('integration-worker'))
    def command(self, op, payload, expected=None, *, request_id=None):
        from process_runtime_client import command_envelope
        return self.client.execute(command_envelope(op, request_id=request_id or uuid.uuid4().hex, expected_version=expected, payload=payload))
    def query(self, op, payload): return self.client.query({'schema':'runtime.query.v1','operation':op,'payload':payload})['data']
    def publish(self, process_id='integration-process'):
        draft={'schema':'process.draft.v1','process_id':process_id,'inputs':{'number':{'kind':'integer'}},'steps':[{'step_id':'compute','depends_on':[],'capabilities':['compute'],'resources':[],'inputs':{'number':{'kind':'integer'}},'bindings':{'number':{'source':'run_input','name':'number'}},'outputs':{'answer':{'kind':'integer'}},'max_attempts':3}]}
        self.command('process.draft.put',{'draft':draft,'provenance':{'source_id':'integration-fixture','media_type':'application/json','content_hash':hashlib.sha256(canonical(draft)).hexdigest(),'parser':'explicit-json-v1','uncertainties':[]}})
        return self.command('process.publish',{'process_id':process_id,'draft_revision':1,'version_id':process_id+'-v1'},1)['result']['pin']
    def create(self, run_id, pin): self.command('run.create',{'run_id':run_id,'pin':pin,'inputs':{'number':7}})
    def work(self, run_id): return next(item for item in asyncio.run(self.executor.work('integration-worker',limit=100))['work'] if item['run_id']==run_id)
    def claim(self, run_id, *, lease_ms=120000): return asyncio.run(self.executor.claim('integration-worker',self.work(run_id),lease_ms=lease_ms))
    def restart_frank(self):
        from frank_executor.journal import Journal
        from frank_executor.runtime import RuntimeGateway
        from frank_executor.executor import Executor
        from frank_executor.authority import LocalDevResults
        self.journal=Journal(self.journal_path)
        self.gateway=RuntimeGateway(self.gateway.client,self.gateway.authority,self.journal,self.gateway.validate)
        self.executor=Executor(self.gateway,self.registry,self.journal,LocalDevResults(environment='test',enabled=True),environment='test')
    def finish(self, attempt): return asyncio.run(self.executor.run(attempt,operation='compute',scope='answer',wait_seconds=5))


def parallel_checks(s, check):
    integer={'kind':'integer'}
    def root_step(name):
        return {'step_id':name,'depends_on':[],'capabilities':['compute'],'resources':[],
            'inputs':{'number':integer},'bindings':{'number':{'source':'run_input','name':'number'}},
            'outputs':{'answer':integer},'max_attempts':2}
    draft={'schema':'process.draft.v1','process_id':'parallel-process','inputs':{'number':integer},
        'steps':[root_step('alpha'),root_step('beta'),{'step_id':'join','depends_on':['alpha','beta'],
            'capabilities':['compute'],'resources':[],'inputs':{'left':integer,'right':integer},
            'bindings':{'left':{'source':'step_output','step_id':'alpha','name':'answer'},'right':{'source':'step_output','step_id':'beta','name':'answer'}},
            'outputs':{'answer':integer},'max_attempts':2}]}
    s.command('process.draft.put',{'draft':draft,'provenance':{'source_id':'parallel-fixture','media_type':'application/json','content_hash':hashlib.sha256(canonical(draft)).hexdigest(),'parser':'explicit-json-v1','uncertainties':[]}})
    pin=s.command('process.publish',{'process_id':'parallel-process','draft_revision':1,'version_id':'parallel-v1'},1)['result']['pin']
    s.create('parallel-run',pin)
    work=[row for row in asyncio.run(s.executor.work('integration-worker',limit=100))['work'] if row['run_id']=='parallel-run']
    require({row['step_id'] for row in work}=={'alpha','beta'},'Dependency join was incorrectly runnable')
    alpha=next(row for row in work if row['step_id']=='alpha'); beta=next(row for row in work if row['step_id']=='beta')
    async def race():return await asyncio.gather(*(s.executor.claim('integration-worker',alpha) for _ in range(6)),return_exceptions=True)
    outcomes=asyncio.run(race()); winners=[item for item in outcomes if not isinstance(item,Exception)]
    require(len(winners)==1,'Concurrent Frank claims had multiple winners')
    second=asyncio.run(s.executor.claim('integration-worker',beta))
    async def execute_roots():return await asyncio.gather(*(s.executor.run(attempt,operation='compute',scope='answer',wait_seconds=5) for attempt in (winners[0],second)))
    require(all(item['state']=='committed' for item in asyncio.run(execute_roots())),'Parallel root completion did not commit')
    joined=s.claim('parallel-run'); require(joined.binding.step_id=='join' and joined.inputs=={'left':14,'right':14},'Join did not consume both committed outputs')
    require(s.finish(joined)['state']=='committed','Join completion failed')
    output=s.query('output.get',{'run_id':'parallel-run','step_id':'join','attempt_id':joined.binding.attempt_id})
    receipt=s.query('receipt.get',{'receipt_id':output['receipt_id']})
    require(output['outputs']=={'answer':28} and receipt['output_digest']==output['output_digest'],'Parallel output/receipt chain mismatch')
    require(s.query('run.get',{'run_id':'parallel-run'})['state']=='succeeded','Parallel run did not finish')
    check('parallel-ready-claim-race-join',{'initial_ready':['alpha','beta'],'claim_contenders':6,'claim_winners':1,'join_output_digest':output['output_digest']})


def runtime_frank_checks(session, pin, check):
    from frank_executor.model import UnknownOutcome, effect_key
    from process_runtime_client import RuntimeClientError
    s=session
    s.create('restart-run',pin); attempt=s.claim('restart-run')
    s.runtime_provider.restart(); s.restart_frank()
    recovered=asyncio.run(s.executor.recover(attempt.binding,'integration-worker'))
    require(recovered.binding==attempt.binding,'Restart changed current Attempt')
    require(s.finish(recovered)['state']=='committed','Restarted executor did not commit')
    output=s.query('output.get',{'run_id':'restart-run','step_id':'compute','attempt_id':attempt.binding.attempt_id})
    receipt=s.query('receipt.get',{'receipt_id':output['receipt_id']})
    require(receipt['output_digest']==output['output_digest'],'Receipt/output digest mismatch')
    s.runtime_provider.restart()
    require(s.query('output.get',{'run_id':'restart-run','step_id':'compute','attempt_id':attempt.binding.attempt_id})==output,'Restart lost immutable output')
    events=s.query('events.list',{'after':0,'limit':100,'run_id':'restart-run'})
    require(any(e['kind']=='attempt.completed' for e in events['events']),'Durable completion event lost')
    check('runtime-frank-restart-receipt',{'run_id':'restart-run','output_digest':output['output_digest'],'receipt_id':receipt['receipt_id'],'event_cursor':events['next_cursor']})

    s.create('lease-run',pin); first=s.claim('lease-run',lease_ms=1000)
    wait_until(lambda:s.query('run.get',{'run_id':'lease-run'}),lambda r:r['steps'][0]['state']=='ready')
    second=s.claim('lease-run')
    require(second.binding.fence>first.binding.fence and second.binding.attempt_id!=first.binding.attempt_id,'Expiry reused Attempt/fence')
    try:
        s.command('executor.complete',{**first.binding.json(),'outputs':{'answer':14}},second.step_version)
        raise AssertionError('Stale completion admitted')
    except RuntimeClientError as error: require(error.code=='stale_fence','Unexpected stale-fence rejection')
    asyncio.run(s.executor.release(second,'Fixture finished without external effect'))
    check('expired-lease-fence',{'old_fence':first.binding.fence,'new_fence':second.binding.fence,'rejection':'stale_fence'})

    s.create('unknown-run',pin); unknown=s.claim('unknown-run'); s.adapter.failure=True
    try:
        asyncio.run(s.executor.dispatch(unknown,operation='compute',scope='answer'))
        raise AssertionError('Missing unknown outcome')
    except UnknownOutcome: pass
    key=effect_key(unknown.binding,'compute','answer'); count=len(s.adapter.sent)
    s.restart_frank(); s.runtime_provider.restart()
    recovered=asyncio.run(s.executor.recover(unknown.binding,'integration-worker'))
    require(asyncio.run(s.executor.dispatch(recovered,operation='compute',scope='answer'))['state']=='unknown','Unknown evidence lost')
    require(len(s.adapter.sent)==count,'Unknown effect was sent again')
    check('unknown-effect-no-resend',{'effect_key':key,'send_count_for_effect':sum(w['effect_id']==s.journal.get(key)['work']['effect_id'] for w in s.adapter.sent),'state':'unknown'})
    s.adapter.failure=False

    s.create('duplicate-run',pin); duplicate=s.claim('duplicate-run'); s.adapter.outcome='acknowledged'
    saved=asyncio.run(s.executor.dispatch(duplicate,operation='compute',scope='answer'))
    s.adapter.events=[s.adapter.result(saved['work'],'succeeded')]
    require(len(asyncio.run(s.executor.receive('fixture')))==1,'Delayed successful result did not commit')
    count=len(s.adapter.sent); s.adapter.events=[s.adapter.result(saved['work'],'succeeded')]
    require(asyncio.run(s.executor.receive('fixture'))==[] and len(s.adapter.sent)==count,'Duplicate provider event repeated completion or send')
    check('duplicate-delivery-no-repeat',{'effect_key':saved['effect_key'],'journal_state':s.journal.get(saved['effect_key'])['state'],'cursor':s.journal.cursor('fixture')})
    s.adapter.outcome='succeeded'
    return output

class ServiceProvider:
    """Service-owned bootstrapping; consumers use its HTTP command/query routes."""
    def __init__(self, directory, runtime_endpoint, *, production=False):
        self.directory, self.runtime_endpoint, self.production = directory, runtime_endpoint, production
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.server = None
    def start(self):
        import threading
        from service_management import Service
        from service_management.authority import LocalDevUntrustedVerifier
        from service_management.sqlite_store import SQLiteUnitOfWork
        from service_management.sdk_adapter import SDKRuntimeAdapter
        from service_management.delivery import OutboxDelivery
        from service_management.projection import RuntimeProjection
        from service_management.http_api import make_server
        from service_management.ingress import CommandIngress
        self.store = SQLiteUnitOfWork(self.directory / 'service.db')
        self.projection = RuntimeProjection(self.directory / 'projection.db')
        self.runtime = None if self.production else SDKRuntimeAdapter.connect(self.runtime_endpoint, environment='test', local_dev_untrusted=True)
        service = Service(self.store, runtime=self.runtime, projection=self.projection,
            verifier=None if self.production else LocalDevUntrustedVerifier(), environment='production' if self.production else 'test', allow_local_dev_untrusted=not self.production)
        self.worker = None if self.production else OutboxDelivery(self.store,self.runtime)
        self.server=make_server(CommandIngress(service))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.endpoint=f'http://127.0.0.1:{self.server.server_port}'
        return self
    def stop(self):
        if self.server is not None:
            self.server.shutdown(); self.server.server_close(); self.thread.join(5); self.server=None
    def restart(self): self.stop(); return self.start()
    def call(self, operation, payload=None, version=None, *, resource_id=None, query=False, request_id=None):
        kind='Case' if operation.startswith('case.') else 'ServiceRequest'
        body={'schema':'service.query.v1' if query else 'service.command.v1', 'operation':operation,
            'request_id':request_id or uuid.uuid4().hex,'resource':{'kind':kind,'id':resource_id or ('case-1' if kind=='Case' else 'request-1')},'payload':payload or {}}
        if not query: body['expected_version']=version
        opener=request.build_opener(request.ProxyHandler({}))
        call=request.Request(self.endpoint+('/v1/queries' if query else '/v1/commands'), data=canonical(body),headers={'Content-Type':'application/json'})
        with opener.open(call,timeout=10) as response: return json.loads(response.read())

def service_checks(service, session, pin, check):
    s=session
    service.call('request.create',{'summary':'Synthetic isolated service obligation'})
    service.call('request.qualify',{'qualification':'Fixture owner and scope recorded'},1)
    service.call('request.accept',{'acceptance':'Fixture owner accepts'},2)
    awaiting=service.call('request.execution',query=True)['data']
    require(awaiting['execution_state']=='awaiting_plan','Acceptance invented a runnable process')
    service.call('request.convert',{'case_id':'case-1','obligation':'Compute the agreed fixture result'},3)
    service.call('case.plan',{'scope':'Compute a typed result and retain execution evidence'},1)
    approval=service.call('case.plan.approve',{'revision_id':'revision-1','reason':'Explicit selected version','pin':pin,'inputs':{'number':7},'carry_forward':[]},2)
    event_id=approval['handoff']['event_id']
    require(approval['handoff']['state']=='pending','Plan approval did not retain outbox work')
    sent=[]; delegate=service.runtime.command
    def lose_response(envelope):
        sent.append(deepcopy(envelope)); delegate(envelope); raise TimeoutError('Synthetic response loss after handoff commit')
    service.runtime.command=lose_response
    pending=service.worker.reconcile(event_id)
    require(pending['state']=='pending' and pending['uncertain'],'Lost handoff acknowledgement was treated as failure')
    service.restart()
    original=service.runtime.command
    def retain_retry(envelope): sent.append(deepcopy(envelope)); return original(envelope)
    service.runtime.command=retain_retry
    started=service.worker.reconcile(event_id)
    require(started['state']=='started' and sent[0]==sent[1],'Restart did not replay exact retained handoff')
    require(service.worker.reconcile(event_id)==started,'Duplicate handoff changed canonical run')
    run_id=started['run_id']; attempt=s.claim(run_id)
    require(s.finish(attempt)['state']=='committed','Frank did not complete the Service-selected run')
    output=s.query('output.get',{'run_id':run_id,'step_id':'compute','attempt_id':attempt.binding.attempt_id})
    service.projection.rebuild(service.runtime)
    summary=service.call('case.summary',query=True)['data']
    require(summary['canonical_service']['state']=='planned' and summary['derived_runtime']['state']=='succeeded','Derived runtime progress mutated canonical Case state')
    check('service-intake-plan-outbox-frank',{'awaiting_plan':awaiting['execution_state'],'handoff_event':event_id,'run_id':run_id,'output_digest':output['output_digest'],'exact_delivery_attempts':len(sent),'case_state':summary['canonical_service']['state'],'derived_state':summary['derived_runtime']['state']})

    definition=s.query('process.get',{'process_id':pin['process_id']})
    draft=deepcopy(definition['draft']);draft['inputs']={'evidence':{'kind':'integer'}}
    draft['steps'][0]['bindings']['number']={'source':'run_input','name':'evidence'}
    changed=s.command('process.draft.put',{'draft':draft,'provenance':definition['provenance']},definition['version'])
    next_pin=s.command('process.publish',{'process_id':pin['process_id'],'draft_revision':changed['result']['revision'],'version_id':'integration-process-v2'},changed['version'])['result']['pin']
    selection={'run_id':run_id,'step_id':'compute','attempt_id':attempt.binding.attempt_id,'output_name':'answer','target_input':'evidence','expected_output_digest':output['output_digest']}
    revision=service.call('case.plan.revise',{'revision_id':'revision-2','reason':'Owner-selected replacement with explicit evidence','pin':next_pin,'inputs':{},'carry_forward':[selection],'superseded_run_id':run_id},3)
    replaced=service.worker.reconcile(revision['handoff']['event_id'])
    require(replaced['state']=='started' and replaced['run_id']!=run_id,'Plan revision did not create an independent run')
    require(s.query('run.get',{'run_id':run_id})['pin']==pin,'Plan revision repinned old execution')
    require(s.query('run.get',{'run_id':replaced['run_id']})['inputs']=={'evidence':14},'Explicit output carry-forward differs')
    require(s.query('output.get',{'run_id':run_id,'step_id':'compute','attempt_id':attempt.binding.attempt_id})==output,'Plan revision changed retained output')
    history=service.call('case.plan-history',{'after_revision':0,'limit':100},query=True)['data']
    require(len(history['revisions'])==2,'Plan revision history missing')
    check('case-revision-explicit-carry-forward',{'old_run':run_id,'new_run':replaced['run_id'],'old_pin':pin,'new_pin':next_pin,'retained_output_digest':output['output_digest'],'revision_count':2})
    return output

def production_checks(providers, source_paths, directory, runtime_endpoint, check):
    from process_runtime_client import RuntimeClient, RuntimeClientError, LocalDevAuthority
    from frank_executor.authority import check_authority, DenyAuthority, DenyResults
    from frank_executor.model import Rejected
    from urllib.error import HTTPError
    runtime=Provider('process_runtime',directory/'production-runtime',source_paths,production=True).start();providers.append(runtime)
    try:
        RuntimeClient(runtime.endpoint,authority=LocalDevAuthority(environment='test')).query({'schema':'runtime.query.v1','operation':'events.list','payload':{'after':0,'limit':1}})
        raise AssertionError('Production runtime accepted untrusted evidence')
    except RuntimeClientError as error: require(error.code=='unauthenticated','Unexpected production runtime denial')
    service=ServiceProvider(directory/'production-service',runtime_endpoint,production=True).start();providers.append(service)
    try:
        service.call('request.create',{'summary':'Must not be admitted'})
        raise AssertionError('Production Service admitted an untrusted caller')
    except HTTPError as error:
        require(error.code==403 and json.loads(error.read())['error']['code']=='forbidden','Unexpected production Service denial')
    try:
        check_authority('production',DenyAuthority(),DenyResults())
        raise AssertionError('Production Frank admitted unavailable authority')
    except Rejected: pass
    check('production-default-deny',{'runtime':'unauthenticated','service':'forbidden','frank':'qualified authority and results required'})

def source_manifest(roots):
    manifests={}
    for name,root in roots.items():
        paths=[root/'pyproject.toml',*sorted((root/'src').rglob('*.py')),*sorted((root/'contracts').rglob('*.json'))]
        files={str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        manifests[name]={'files':files,'manifest_sha256':hashlib.sha256(canonical(files)).hexdigest()}
    files={relative:hashlib.sha256((HUB/relative).read_bytes()).hexdigest() for relative in [
        'pyproject.toml','config/process-execution.components.json','services/process_composition/__init__.py','scripts/verify_process_execution.py']}
    manifests['hub']={'files':files,'manifest_sha256':hashlib.sha256(canonical(files)).hexdigest()}
    return manifests

def verify(*, runtime_repo=None, service_repo=None, frank_repo=None, config_path, artifact_manifest=None):
    if str(HUB) not in sys.path:sys.path.insert(0,str(HUB))
    from services.process_composition import validate_config,verify_contracts,admission,change_admission,legacy_eligibility,apply_new_run_admission
    config=validate_config(json.loads(Path(config_path).read_text()))
    artifact_pin=None
    if artifact_manifest is not None:
        if any(path is not None for path in (runtime_repo,service_repo,frank_repo)):
            raise ValueError('Installed mode cannot include source checkout overrides')
        from services.process_composition.artifacts import verify_manifest
        artifact_pin=verify_manifest(artifact_manifest)
        roots={};source_paths=[];source_pins=None
        paths={name:Path(config_path).resolve().parent/'process-contracts'/f'{name}.json' for name in config['components']}
        verify_contracts(config,paths)
        pins={}
        for name,component in config['components'].items():
            actual=artifact_pin['components'][name]
            require(actual['package']==component['package'] and actual['version']==component['version'],f'Installed tuple version mismatch: {name}')
            pins[name]={**actual,'contract_sha256':component['contract_sha256']}
        from importlib.resources import files
        require(hashlib.sha256(files('process_runtime_client').joinpath('runtime-v1.schema.json').read_bytes()).hexdigest()==config['components']['process-runtime']['contract_sha256'],'Installed SDK contract mismatch')
    else:
        require(all(path is not None for path in (runtime_repo,service_repo,frank_repo)),'Supply every source checkout or an installed artifact manifest')
        roots={name:Path(path).resolve() for name,path in [('process-runtime',runtime_repo),('service-management',service_repo),('frank',frank_repo)]}
        source_pins=source_manifest(roots)
        source_paths=[path/'src' for path in roots.values()]
        for path in source_paths:
            if str(path) not in sys.path:sys.path.insert(0,str(path))
        paths={'process-runtime':roots['process-runtime']/'contracts/runtime-v1.schema.json','service-management':roots['service-management']/'contracts/service.command.v1.schema.json','frank':roots['frank']/'contracts/integration-adapter-v1.schema.json'}
        for name,item in config['components'].items():
            candidate=roots[name]/'contracts'/item['contract_schema']
            if candidate.is_file():paths[name]=candidate
        verify_contracts(config,paths)
        import tomllib
        pins={}
        for name,path in roots.items():
            project=tomllib.loads((path/'pyproject.toml').read_text())['project']
            require(project['name']==config['components'][name]['package'] and project['version']==config['components'][name]['version'],f'Package/version mismatch: {name}')
            pins[name]={'package':project['name'],'version':project['version'],'contract_sha256':hashlib.sha256(paths[name].read_bytes()).hexdigest()}
    import tomllib
    hub_source_pins={str(path.relative_to(HUB)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted((HUB/'services/process_composition').glob('*.py'))}
    hub_source_pins['scripts/verify_process_execution.py']=hashlib.sha256((HUB/'scripts/verify_process_execution.py').read_bytes()).hexdigest()
    checks=[]
    def check(name,evidence):checks.append({'name':name,'status':'passed','evidence':evidence})
    hub_metadata=tomllib.loads((HUB/'pyproject.toml').read_text())['project']
    pins['hub']={'package':hub_metadata['name'],'version':hub_metadata['version'],
        'composition_config_sha256':hashlib.sha256(Path(config_path).read_bytes()).hexdigest(),
        'composition_source_sha256':hashlib.sha256((HUB/'services/process_composition/__init__.py').read_bytes()).hexdigest()}
    check('component-pins',pins)
    providers=[]
    with tempfile.TemporaryDirectory(prefix='process-execution-integration-') as temporary:
        directory=Path(temporary)
        try:
            test_config=deepcopy(config);test_config.update(environment='test',admission_mode='greenfield',external_effects=False,authority_qualified=False)
            test_config=validate_config(test_config)
            policy_directory=directory/'admission';policy_directory.mkdir(mode=0o700)
            policy_path=policy_directory/'new-runs.json'
            operator_environment={'PATH':'/usr/bin:/bin',**({'PYTHONPATH':':'.join(map(str,source_paths))} if source_paths else {})}
            applied=apply_new_run_admission(test_config,policy_path=policy_path,
                expected_composition_digest=hashlib.sha256(canonical(test_config)).hexdigest(),expected_policy_digest=None,
                operator_environment=operator_environment)
            runtime=Provider('process_runtime',directory/'runtime',source_paths,admission_policy=policy_path).start();providers.append(runtime)
            session=CheckSession(runtime,roots.get('frank'),directory)
            pin=session.publish()
            parallel_checks(session,check)
            runtime_frank_checks(session,pin,check)
            service=ServiceProvider(directory/'service',runtime.endpoint).start();providers.append(service)
            old_case_output=service_checks(service,session,pin,check)
            production_checks(providers,source_paths,directory,runtime.endpoint,check)
            from process_runtime_client import command_envelope, RuntimeClientError
            admitted=command_envelope('run.create',request_id='admission-existing',expected_version=None,
                payload={'run_id':'admission-existing','pin':pin,'inputs':{'number':7}})
            session.client.execute(admitted)
            in_flight=session.claim('admission-existing')
            send_count=len(session.adapter.sent)
            before=session.query('output.get',{'run_id':old_case_output['run_id'],'step_id':old_case_output['step_id'],'attempt_id':old_case_output['attempt_id']})
            changed=change_admission(test_config,expected_digest=hashlib.sha256(canonical(test_config)).hexdigest(),mode='legacy_read_only')
            route=admission(changed)
            closed=apply_new_run_admission(changed,policy_path=policy_path,
                expected_composition_digest=hashlib.sha256(canonical(changed)).hexdigest(),expected_policy_digest=applied['policy_digest'],
                operator_environment=operator_environment)
            require(route['target']=='legacy_read_only' and route['existing_run_owner']=='process-runtime' and not route['external_effects'],'Rollback changed ownership or enabled effects')
            try:
                session.create('rejected-after-rollback',pin)
                raise AssertionError('Runtime admitted new work after rollback')
            except RuntimeClientError as error:
                require(error.code=='not_ready' and error.retryable and not error.uncertain,'Closed admission lost retry semantics')
            require(session.client.execute(admitted)['duplicate'],'Rollback broke exact existing request reconciliation')
            session.command('executor.complete',{**in_flight.binding.json(),'outputs':{'answer':14}},in_flight.step_version)
            continued=session.query('run.get',{'run_id':'admission-existing'})
            require(continued['state']=='succeeded','Rollback stranded already admitted work')
            service.call('request.create',{'summary':'Accepted while new-run admission is closed'},resource_id='rollback-request')
            service.call('request.qualify',{'qualification':'Explicit source-owned intent'},1,resource_id='rollback-request')
            service.call('request.accept',{'acceptance':'Accepted source obligation'},2,resource_id='rollback-request')
            service.call('request.convert',{'case_id':'rollback-case','obligation':'Resume exact handoff later'},3,resource_id='rollback-request')
            service.call('case.plan',{'scope':'Retain accepted work'},1,resource_id='rollback-case')
            held=service.call('case.plan.approve',{'revision_id':'rollback-revision','reason':'Explicit paused delivery',
                'pin':pin,'inputs':{'number':7},'carry_forward':[]},2,resource_id='rollback-case')
            event_id=held['handoff']['event_id']
            pending=service.worker.reconcile(event_id)
            require(pending['state']=='pending' and pending['last_error']=='not_ready','Closed admission lost accepted outbox work')
            runtime.restart()
            try:
                session.create('rejected-after-closed-restart',pin)
                raise AssertionError('Restart forgot the admission policy')
            except RuntimeClientError as error:
                require(error.code=='not_ready' and error.retryable,'Restart changed closed admission semantics')
            require(session.query('output.get',{'run_id':old_case_output['run_id'],'step_id':old_case_output['step_id'],'attempt_id':old_case_output['attempt_id']})==before,'Rollback changed retained outputs')
            require(len(session.adapter.sent)==send_count,'Rollback repeated an effect')
            reopened=apply_new_run_admission(test_config,policy_path=policy_path,
                expected_composition_digest=hashlib.sha256(canonical(test_config)).hexdigest(),expected_policy_digest=closed['policy_digest'],
                operator_environment=operator_environment)
            resumed=service.worker.reconcile(event_id)
            require(resumed['state']=='started','Accepted outbox work failed to resume after reopen')
            require(service.worker.reconcile(event_id)['run_id']==resumed['run_id'],'Reopen duplicated source handoff')
            assessment=legacy_eligibility({'status':'running','execution_eligibility':'eligible','frozen_contract_hash':'a'*64,'unknown_effects':True,'root_inputs':{}})
            require(not assessment['eligible'],'Legacy unknown outcome became eligible')
            check('rollback-new-admission-only',{'target':route['target'],'existing_run_owner':route['existing_run_owner'],
                'admission_consumer':'runtime.new-run-admission.v1','closed_policy_digest':closed['policy_digest'],
                'new_run_rejection':'not_ready','existing_run_state':continued['state'],'closed_restart_rejection':'not_ready',
                'paused_handoff_state':pending['state'],'resumed_handoff_state':resumed['state'],
                'reopened_policy_digest':reopened['policy_digest'],'retained_output_digest':before['output_digest'],
                'additional_sends':0,'legacy_unknown_effects':assessment})
            asyncio.run(session.registry.close())
        finally:
            for provider in reversed(providers):provider.stop()
    current_hub_pins={str(path.relative_to(HUB)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted((HUB/'services/process_composition').glob('*.py'))}
    current_hub_pins['scripts/verify_process_execution.py']=hashlib.sha256((HUB/'scripts/verify_process_execution.py').read_bytes()).hexdigest()
    require(current_hub_pins==hub_source_pins,'Hub composition source changed during the drill')
    if artifact_manifest is not None:
        require(verify_manifest(artifact_manifest)==artifact_pin,'Installed artifact tuple changed during the drill')
    else:
        require(source_manifest(roots)==source_pins,'Component sources changed during the drill')
    return {'verification_mode':'installed-artifacts' if artifact_manifest is not None else 'source-checkouts','artifact_tuple':artifact_pin,'hub_source_sha256':hub_source_pins,'source_sha256':source_pins,'schema':'hub.process-execution-verification.v1','status':'passed','authority_mode':'local_dev_untrusted','external_network':'none; loopback providers only','external_effects':'synthetic fixture transport only','checks':checks,'production_ready':False,
        'production_no_go':['Native Wallet/secS issuer and generic installed-stack qualification are not complete.','Dedicated Frank native Wallet file-custody identity has not been provisioned.','Production credential and terminal result verification adapters remain default-deny.','Real Hermes/Matrix and review/STT/status/vault operations were not invoked or qualified.','The isolated loopback rollback applies receiver-owned admission and preserves exact retries, outbox intent and existing work; it does not qualify production cutover.']}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-repo',type=Path);parser.add_argument('--service-repo',type=Path);parser.add_argument('--frank-repo',type=Path)
    parser.add_argument('--artifact-manifest',type=Path)
    parser.add_argument('--config',type=Path,default=HUB/'config/process-execution.components.json');parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    try:
        report=verify(runtime_repo=args.runtime_repo,service_repo=args.service_repo,frank_repo=args.frank_repo,config_path=args.config,artifact_manifest=args.artifact_manifest)
    except Exception as error:
        report={'schema':'hub.process-execution-verification.v1','status':'failed','production_ready':False,'error_type':type(error).__name__,'detail':str(error)}
        if args.output:args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
        print(json.dumps(report,sort_keys=True));return 1
    if args.output:args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'status':report['status'],'checks':len(report['checks']),'production_ready':False,'report_sha256':hashlib.sha256(canonical(report)).hexdigest()},sort_keys=True))
    return 0

if __name__=='__main__':raise SystemExit(main())
