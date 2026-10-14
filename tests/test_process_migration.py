"""Read-only migration evidence from actual legacy compiler/API/store fixtures."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import time

import pytest

from services.cases.contract import compile_process_contract
from services.process_composition import legacy_eligibility
from services.process_composition.migration import (EvidenceError, assess_inventory, assess_legacy_evidence,
    canonical, collect_legacy_evidence, digest)
from tests import test_legacy_containment as legacy_fixtures
from tests.test_legacy_containment import claim, complete, headers, start_step

api = legacy_fixtures.api

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT/'base/ops/processes/process-queued-review.md').read_text()


def create_bound(api):
    compiled=compile_process_contract(SOURCE,process_path='process-queued-review')
    roots={name:(0 if compiled['variables'][name]['type']=='number' else 'root-'+name) for name in compiled['root_inputs']}
    response=api[0].post('/cases',json={'queue_message_id':'migration-intent','process_name':'personal-review',
        'process_path':'process-queued-review','process_source':SOURCE,'title':'Private intake sentinel','objective':'review',
        'sender':'fixture-only','dispatch_packet_json':{'initial_context':roots,'runtime':{'mode':'native_case_pipeline'}}})
    assert response.status_code==201,response.text
    return response.json(),roots


def frozen(api,case_id):
    path=api[3]/'frozen.db'
    if path.exists():path.unlink()
    with sqlite3.connect(api[2].as_uri()+'?mode=ro',uri=True) as source,sqlite3.connect(path) as target:
        source.backup(target)
    return collect_legacy_evidence(path,case_ids=[case_id])[0],path


def seal(value):
    value['provenance']['snapshot_sha256']=digest({key:item for key,item in value.items() if key!='provenance'})
    return value


def reviewed_assessment(value, **options):
    # Deliberately pin each malformed reviewer fixture to exercise inner checks.
    # Production receivers never derive the independent pin from caller JSON.
    return assess_legacy_evidence(value, expected_evidence_sha256=digest(value), **options)


def root_packet(value,roots):
    packet=json.loads(value['case']['dispatch_packet_json']);packet['initial_context']=roots
    value['case']['dispatch_packet_json']=json.dumps(packet)
    value['root_intent']['root_inputs']=roots;value['root_intent']['packet_sha256']=digest(packet)
    return seal(value)


def retained(api):
    case,roots=create_bound(api);lease=claim(api,case,roots)
    attempt=start_step(api,case,lease,1)
    response=complete(api,case,lease,attempt,1)
    assert response.status_code==200,response.text
    finished=api[0].post(f"/cases/{case['case_id']}/execution/finish",headers=headers(lease),json={'outcome':'blocked','reason':'Retain successful first-step evidence for explicit review'})
    assert finished.status_code==200,finished.text
    return case,roots,lease,attempt


def test_old_label_and_hash_only_wrapper_is_never_eligible():
    old={'status':'open','execution_eligibility':'eligible','frozen_contract_hash':'a'*64,'root_inputs':{'intent':'review'}}
    assert legacy_eligibility(old)=={'eligible':False,'reason':'closed_legacy_evidence_required','mode':'read_only'}
    assert not legacy_eligibility({**old,'unknown_effects':True})['eligible']


def test_actual_compiler_root_intent_and_closed_sqlite_inventory_candidate(api):
    case,_=create_bound(api);snapshot,path=frozen(api,case['case_id'])
    before=path.read_bytes();names={item.name for item in path.parent.iterdir()}
    def schema_and_logs():
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
            return (db.execute('SELECT type,name,tbl_name,rootpage,sql FROM sqlite_schema ORDER BY type,name').fetchall(),
                    db.execute('SELECT * FROM case_logs ORDER BY id').fetchall())
    metadata_before=schema_and_logs()
    snapshots=collect_legacy_evidence(path)
    assert path.read_bytes()==before
    assert schema_and_logs()==metadata_before,'inventory changed the schema or retained log rows'
    assert {item.name for item in path.parent.iterdir()}==names,'read-only inventory created a sidecar'
    prior=deepcopy(snapshots)
    result=assess_inventory(snapshots)
    assert snapshots==prior,'assessment mutated retained evidence'
    row=result['assessments'][0]
    assert row['eligible'] and row['disposition']=='new_root_run_candidate'
    assert row['retained_outputs']==[] and row['effects']==[] and not row['production_ready']
    assert row['operator_review_required'] and row['reassessment_before_admission_required']
    assert legacy_eligibility(snapshot)=={'eligible':True,'reason':'new_pinned_run_from_explicit_root_intent','mode':'create_new_run'}
    assert 'Private intake sentinel' not in canonical(result).decode()


@pytest.mark.parametrize('mutation',['snapshot_digest','contract_digest','source_hash','altered_contract','closed_root','closed_case','foreign_step','missing_slot','duplicate_step','invalid_graph','claim_boolean'])
def test_hash_labels_closed_parser_contract_and_inventory_mismatch_fail_closed(api,mutation):
    case,_=create_bound(api);value,_=frozen(api,case['case_id'])
    if mutation=='snapshot_digest':value['provenance']['snapshot_sha256']='0'*64
    elif mutation=='contract_digest':value['frozen_contract_hash']='a'*64
    elif mutation=='source_hash':value['case']['process_hash']='a'*64
    elif mutation=='altered_contract':
        value['frozen_contract']['description']='Forged retained contract'
        value['frozen_contract_hash']=digest(value['frozen_contract']);value['case']['contract_json']=json.dumps(value['frozen_contract'])
    elif mutation=='closed_root':value['execution_eligibility']='eligible'
    elif mutation=='closed_case':value['case']['authority']='native_verified'
    elif mutation=='foreign_step':value['steps'][0]['case_id']='other-case'
    elif mutation=='missing_slot':value['slots'].pop()
    elif mutation=='duplicate_step':value['steps'].append(deepcopy(value['steps'][0]))
    elif mutation=='invalid_graph':
        value['frozen_contract']['dag_edges'].append({'from':0,'to':0,'variables':[]})
        value['frozen_contract_hash']=digest(value['frozen_contract']);value['case']['contract_json']=json.dumps(value['frozen_contract'])
    elif mutation=='claim_boolean':value['claim']=True
    if mutation!='snapshot_digest':seal(value)
    result=reviewed_assessment(value)
    assert not result['eligible'] and result['disposition']=='read_only_unsupported'
    assert result['retained_outputs']==[]


@pytest.mark.parametrize('mutation',['wrong_packet','missing_root','extra_output_as_root','boolean_number','changed_root_slot'])
def test_actual_root_intent_packet_typed_inputs_and_provenance_validated(api,mutation):
    case,roots=create_bound(api);value,_=frozen(api,case['case_id'])
    if mutation=='wrong_packet':value['root_intent']['packet_sha256']='0'*64
    elif mutation=='missing_root':
        roots.pop(next(iter(roots)));root_packet(value,roots)
    elif mutation=='extra_output_as_root':
        roots['transcript']='Ordinal success must not be root intent';root_packet(value,roots)
    elif mutation=='boolean_number':
        roots['duration_ms']=True;root_packet(value,roots)
    elif mutation=='changed_root_slot':
        slot=next(row for row in value['slots'] if row['name']=='review_id');slot['value']=json.dumps('different-review')
    seal(value);result=reviewed_assessment(value)
    assert not result['eligible']
    assert result['reason'] in {'missing_intake_evidence','root_slot_provenance_disagreement'}


def test_real_active_expired_released_and_wrong_fence_claims_require_reconciliation(api):
    case,roots=create_bound(api);lease=claim(api,case,roots)
    value,_=frozen(api,case['case_id']);result=reviewed_assessment(value)
    assert result['disposition']=='reconciliation_required' and 'active_legacy_lease' in result['reasons']
    changed=deepcopy(value);changed['claim']['expires_at']=time.time()-1;seal(changed)
    result=reviewed_assessment(changed)
    assert result['disposition']=='reconciliation_required' and 'expired_claim_requires_reconciliation' in result['reasons']
    changed=deepcopy(value);changed['claim']['fence']=lease['fence']+1;seal(changed)
    assert reviewed_assessment(changed)['reason']=='claim_attempt_fence_disagreement'
    changed=deepcopy(value);changed['claim']=None;seal(changed)
    assert 'missing_current_claim_and_fence' in reviewed_assessment(changed)['reasons']


def test_real_fenced_retained_outputs_require_explicit_compatibility_never_runtime_success(api):
    case,_,_,_=retained(api);value,_=frozen(api,case['case_id']);before=deepcopy(value)
    result=reviewed_assessment(value)
    assert result['disposition']=='compatibility_revision_required',result
    assert not result['eligible'] and len(result['retained_outputs'])==1
    evidence=result['retained_outputs'][0]
    assert evidence['outcome']=='completed' and evidence['fence']==1
    assert not evidence['carry_forward_allowed'] and not evidence['native_receipt_verified']
    assert evidence['output_digest']==digest(json.loads(value['steps'][0]['result_json']))
    assert value==before


@pytest.mark.parametrize('mutation',['ordinal_only','output_value','output_attempt','missing_attempt','failed_as_success','stale_fence','unknown_effect_label'])
def test_ordinal_missing_receipt_changed_output_stale_fence_never_becomes_success(api,mutation):
    case,_,_,_=retained(api);value,_=frozen(api,case['case_id'])
    if mutation=='ordinal_only':value['steps'][0]['result_json']=None;value['steps'][0]['runtime_state_json']=None
    elif mutation=='output_value':
        row=next(row for row in value['slots'] if row['name']=='audio_asset_path');row['value']=json.dumps('changed-output')
    elif mutation=='output_attempt':
        row=next(row for row in value['slots'] if row['name']=='audio_asset_path');row['agent_run_id']='foreign-attempt'
    elif mutation=='missing_attempt':value['step_runs']=[]
    elif mutation=='failed_as_success':value['step_runs'][0]['status']='failed'
    elif mutation=='stale_fence':value['runs'][0]['metadata_json']=json.dumps({**json.loads(value['runs'][0]['metadata_json']),'attempt_number':2})
    elif mutation=='unknown_effect_label':value['unknown_effects']=False
    seal(value);result=reviewed_assessment(value)
    assert not result['eligible'] and result['disposition']=='read_only_unsupported'
    assert not any(row['carry_forward_allowed'] for row in result['retained_outputs'])


def test_real_unknown_and_observed_effect_inventory_deterministic_identity_and_no_resend(api):
    case,roots=create_bound(api);lease=claim(api,case,roots)
    first=start_step(api,case,lease,1);assert complete(api,case,lease,first,1).status_code==200
    attempt=start_step(api,case,lease,2)
    route=f"/cases/{case['case_id']}/execution/effects"
    sent=api[0].post(route,headers=headers(lease),json={'step_run_id':attempt['id'],'operation':'transcribe','state':'unknown'})
    assert sent.status_code==200,sent.text
    value,_=frozen(api,case['case_id']);before=deepcopy(value)
    result=reviewed_assessment(value)
    assert result['disposition']=='reconciliation_required'
    assert 'external_effect_outcome_requires_reconciliation' in result['reasons']
    assert result['effects'][0]['state']=='unknown' and value==before
    observed=api[0].post(route,headers=headers(lease),json={'step_run_id':attempt['id'],'operation':'transcribe','state':'observed','evidence':{'provider_reference':'synthetic-observation'}})
    assert observed.status_code==200,observed.text
    value,_=frozen(api,case['case_id']);result=reviewed_assessment(value)
    assert result['disposition']=='reconciliation_required'
    changed=deepcopy(value);changed['effects'][0]['id']='a'*64;seal(changed)
    assert reviewed_assessment(changed)['reason']=='effect_inventory_binding_mismatch'
    changed=deepcopy(value);changed['effects'][0]['evidence_json']='{}';seal(changed)
    assert reviewed_assessment(changed)['reason']=='effect_observation_evidence_missing'


def test_terminal_history_preserved_without_inferred_runtime_output_or_restart(api):
    case,_,_,_=retained(api);value,_=frozen(api,case['case_id'])
    value['case']['status']='FAILED';seal(value)
    result=reviewed_assessment(value)
    assert result['disposition']=='read_only_terminal' and not result['eligible']
    assert all(not row['carry_forward_allowed'] for row in result['retained_outputs'])
    assert value['case']['status']=='FAILED'


def test_strict_collector_refuses_active_journals_incomplete_tables_and_changed_columns(api):
    case,_=create_bound(api);_,path=frozen(api,case['case_id']);before=path.read_bytes()
    wal=Path(str(path)+'-wal');wal.write_bytes(b'active-journal')
    with pytest.raises(EvidenceError,match='journal_free'):collect_legacy_evidence(path)
    wal.unlink();assert path.read_bytes()==before
    with sqlite3.connect(path) as db:db.execute('ALTER TABLE legacy_execution_effects ADD COLUMN unreviewed TEXT')
    db.close()
    with pytest.raises(EvidenceError,match='unreviewed'):collect_legacy_evidence(path)
    _,path=frozen(api,case['case_id'])
    with sqlite3.connect(path) as db:db.execute('DROP TABLE legacy_execution_effects')
    db.close()
    with pytest.raises(EvidenceError,match='incomplete'):collect_legacy_evidence(path)


def test_inventory_duplicate_case_and_selected_bounds_reject_without_mutation(api):
    case,_=create_bound(api);snapshot,path=frozen(api,case['case_id']);before=path.read_bytes()
    with pytest.raises(EvidenceError,match='duplicate_case'):assess_inventory([snapshot,snapshot])
    with pytest.raises(EvidenceError):collect_legacy_evidence(path,case_ids=[case['case_id'],case['case_id']])
    with pytest.raises(EvidenceError):collect_legacy_evidence(path,case_ids=['missing'])
    assert path.read_bytes()==before


@pytest.mark.parametrize('mutation',['boolean_ordinal','null_output','claimed_without_runs','future_snapshot','future_step','boolean_protocol','root_null_slot'])
def test_challenger_temporal_boolean_null_and_omitted_history_cases(api,mutation):
    case,_=create_bound(api);value,_=frozen(api,case['case_id'])
    if mutation=='boolean_ordinal':value['steps'][0]['idx']=False
    elif mutation=='null_output':
        row=next(row for row in value['slots'] if row['name']=='transcript');row['value']='null'
    elif mutation=='claimed_without_runs':value['case']['claimed_at']=value['case']['created_at']
    elif mutation=='future_snapshot':value['captured_at']='2999-01-01T00:00:00+00:00'
    elif mutation=='future_step':value['steps'][0]['updated_at']='2999-01-01T00:00:00+00:00'
    elif mutation=='boolean_protocol':
        # Actual supported attempts cannot replace protocol integers with booleans.
        api[0].post(f"/cases/{case['case_id']}/execution/claim",json={'request_key':'claim','owner':'worker','lease_seconds':120,'initial_context':value['root_intent']['root_inputs']})
        value,_=frozen(api,case['case_id'])
        meta=json.loads(value['runs'][0]['metadata_json']);meta['legacy_claim_protocol']=True;value['runs'][0]['metadata_json']=json.dumps(meta)
    elif mutation=='root_null_slot':
        row=next(row for row in value['slots'] if row['name']=='review_id');row['value']='null'
    seal(value);result=reviewed_assessment(value)
    assert not result['eligible'],result
    assert result['disposition'] in {'read_only_unsupported','reconciliation_required'}


def test_archived_json_needs_separately_selected_full_source_pin(api):
    case,_=create_bound(api);snapshot,_=frozen(api,case['case_id'])
    archived=json.loads(canonical(snapshot))
    assert assess_legacy_evidence(snapshot)['eligible']
    assert assess_legacy_evidence(archived)['reason']=='source_snapshot_not_independently_pinned'
    selected=snapshot.evidence_sha256
    assert selected==digest(snapshot)
    row=assess_legacy_evidence(archived,expected_evidence_sha256=selected)
    assert row['eligible'] and not row['migration_authorized'] and not row['production_ready']
    assert legacy_eligibility(archived,expected_evidence_sha256=selected)['eligible']
    assert assess_inventory([archived],expected_evidence_sha256={case['case_id']:selected})['counts']['new_root_run_candidate']==1
    archived['source_snapshot_sha256']=selected
    assert assess_legacy_evidence(archived,expected_evidence_sha256=selected)['reason']=='closed_legacy_evidence_required'
    with pytest.raises(AttributeError):snapshot._evidence_sha256='a'*64


def test_erased_attempt_effect_history_and_self_rehashed_provenance_never_choose_source_pin(api):
    case,roots=create_bound(api);root,_=frozen(api,case['case_id'])
    lease=claim(api,case,roots);first=start_step(api,case,lease,1)
    assert complete(api,case,lease,first,1).status_code==200
    second=start_step(api,case,lease,2)
    response=api[0].post(f"/cases/{case['case_id']}/execution/effects",headers=headers(lease),
        json={'step_run_id':second['id'],'operation':'transcribe','state':'unknown'})
    assert response.status_code==200,response.text
    source,_=frozen(api,case['case_id'])
    assert source['runs'] and source['step_runs'] and source['effects'] and source['logs']
    forged=json.loads(canonical(root))
    forged['captured_at']=source['captured_at']
    forged['provenance']['database_sha256']=source['provenance']['database_sha256']
    seal(forged)
    assert assess_legacy_evidence(forged)['reason']=='source_snapshot_not_independently_pinned'
    assert assess_legacy_evidence(forged,expected_evidence_sha256=source.evidence_sha256)['reason']=='source_snapshot_pin_mismatch'
    changed=deepcopy(source)
    for key in list(changed):changed[key]=deepcopy(forged[key])
    assert assess_legacy_evidence(changed)['reason']=='source_snapshot_pin_mismatch'
    assert legacy_eligibility(forged)['eligible'] is False
    assert assess_legacy_evidence(source)['disposition']=='reconciliation_required'


def test_actual_committed_effect_cannot_be_omitted_or_unbound_by_reviewer_fixture(api):
    case,roots=create_bound(api);lease=claim(api,case,roots)
    first=start_step(api,case,lease,1);assert complete(api,case,lease,first,1).status_code==200
    second=start_step(api,case,lease,2)
    dispatched=api[0].post(f"/cases/{case['case_id']}/execution/effects",headers=headers(lease),
        json={'step_run_id':second['id'],'operation':'transcribe','state':'unknown'})
    assert dispatched.status_code==200,dispatched.text
    response=api[0].post(f"/cases/{case['case_id']}/execution/effects",headers=headers(lease),
        json={'step_run_id':second['id'],'operation':'transcribe','state':'observed','evidence':{'provider_reference':'fixture-only'}})
    assert response.status_code==200,response.text
    assert complete(api,case,lease,second,2).status_code==200
    finished=api[0].post(f"/cases/{case['case_id']}/execution/finish",headers=headers(lease),json={'outcome':'blocked','reason':'review retained effects'})
    assert finished.status_code==200,finished.text
    source,_=frozen(api,case['case_id'])
    assert source['effects'][0]['state']=='committed'
    assert assess_legacy_evidence(source)['disposition']=='compatibility_revision_required'
    changed=deepcopy(source);changed['effects']=[];seal(changed)
    result=reviewed_assessment(changed)
    assert not result['eligible'] and result['disposition']=='reconciliation_required'
    assert 'terminal_effect_step_missing_committed_inventory' in result['reasons']
    changed=deepcopy(source);changed['claim_requests']=[];seal(changed)
    assert reviewed_assessment(changed)['reason']=='incomplete_claim_inventory'
    changed=deepcopy(source);changed['effects'][0]['step_run_id']=first['id'];seal(changed)
    assert reviewed_assessment(changed)['reason']=='effect_inventory_binding_mismatch'


def test_collector_refuses_changed_source_without_returning_candidate(api,monkeypatch):
    from services.process_composition import migration
    case,_=create_bound(api);_,path=frozen(api,case['case_id']);before=path.read_bytes()
    original=migration._file_digest
    calls=[]
    def changed_hash(value):
        calls.append(value)
        return original(value) if len(calls)==1 else '0'*64
    monkeypatch.setattr(migration,'_file_digest',changed_hash)
    with pytest.raises(EvidenceError,match='changed_during'):collect_legacy_evidence(path)
    assert path.read_bytes()==before


def test_selected_ids_and_archived_pin_port_fail_closed(api):
    case,_=create_bound(api);value,path=frozen(api,case['case_id'])
    with pytest.raises(EvidenceError,match='invalid_selected'):collect_legacy_evidence(path,case_ids=[{}])
    with pytest.raises(EvidenceError,match='invalid_independent'):assess_inventory([value],expected_evidence_sha256={'case':True})
    assert assess_legacy_evidence(value,expected_evidence_sha256=True)['eligible'] is False


@pytest.mark.parametrize('mutation',['fence_gap','missing_prior','claim_request_key','commit_time','duplicate_outputs','slot_time','future_log','audit_step','event_run','artifact_attempt'])
def test_correlated_receipts_complete_fences_and_retained_links_fail_closed(api,mutation):
    case,roots,lease,first=retained(api)
    # The actual API's next claim preserves committed first-step evidence.
    second_lease=claim(api,case,roots,key='next',owner='another-worker')
    second=start_step(api,case,second_lease,2)
    source,_=frozen(api,case['case_id']);value=deepcopy(source)
    if mutation=='fence_gap':
        meta=json.loads(value['runs'][0]['metadata_json']);meta['attempt_number']=3
        value['runs'][0]['metadata_json']=json.dumps(meta)
    elif mutation=='missing_prior':
        row=next(row for row in value['runs'] if row['id']==second_lease['run_id'])
        meta=json.loads(row['metadata_json']);meta['prior_attempt_id']=None;row['metadata_json']=json.dumps(meta)
    elif mutation=='claim_request_key':value['claim_requests'][0]['request_key']='changed-key'
    elif mutation=='commit_time':
        state=json.loads(value['steps'][0]['runtime_state_json']);state['completed_at']=value['case']['created_at'];value['steps'][0]['runtime_state_json']=json.dumps(state)
    elif mutation=='duplicate_outputs':
        state=json.loads(value['steps'][0]['runtime_state_json']);state['committed_outputs']*=2;value['steps'][0]['runtime_state_json']=json.dumps(state)
    elif mutation=='slot_time':
        slot=next(row for row in value['slots'] if row['name']=='audio_asset_path');slot['produced_at']=None
    elif mutation=='future_log':value['logs'][0]['created_at']='2999-01-01T00:00:00+00:00'
    elif mutation=='audit_step':
        from services.process_composition.migration import TABLE_FIELDS
        row=dict.fromkeys(TABLE_FIELDS['model_task_audits'])
        row.update(id='audit-fixture',case_id=case['case_id'],step_id='step_2',step_db_row_id=case['steps'][0]['id'],reference_json='{}',audit_record_json='{}')
        value['model_task_audits'].append(row)
    elif mutation in {'event_run','artifact_attempt'}:
        from services.process_composition.migration import TABLE_FIELDS
        name='events' if mutation=='event_run' else 'artifacts';table='execution_events' if name=='events' else 'execution_artifacts'
        row=dict.fromkeys(TABLE_FIELDS[table]);row.update(id='retained-link-fixture',case_id=case['case_id'],case_run_id=lease['run_id'],step_run_id=second['id'],metadata_json='{}')
        value[name].append(row)
    seal(value);result=reviewed_assessment(value)
    assert not result['eligible'],result
    if mutation in {'commit_time','duplicate_outputs','slot_time'}:
        assert result['retained_outputs']==[],result
        assert 'output_commit_timestamp_disagreement' in result['reasons'] or 'ordinal_terminal_label_without_committed_output_evidence' in result['reasons']
    else:assert result['disposition']=='read_only_unsupported',result
