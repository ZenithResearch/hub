#!/usr/bin/env python3
"""Run the contained legacy review, then replay retained outputs through the new DAG.

The comparison proves orchestration/typed migration, not independent production
handler equivalence. All external calls are fixture transports; legacy artifacts
are captured at each commit before any later path overwrite.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
import logging
from pathlib import Path
import sys
import tempfile
from legacy_shadow_fixture import capture_legacy

HUB=Path(__file__).resolve().parents[1]

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
def require(value,reason):
    if not value:raise AssertionError(reason)
def text(value):return value if isinstance(value,str) else '' if value is None else canonical(value)
def strings(value):return [text(item) for item in value]

def migrate(legacy):
    artifacts={};mapped={};raw=legacy['outputs']
    def artifact(step,path,label):
        value=raw[step]['artifacts'][path]
        key=step+'-'+label;artifacts[key]=value
        return {'$artifact':key}
    def preserved(step,value):artifacts[step+'-raw-outputs']={'media_type':'application/json','content_utf8':canonical(value)}
    for step,item in raw.items():preserved(step,item['values'])
    one=raw['step_1']['values'];events=one['events']
    event_indexes={event['id']:index for index,event in enumerate(events)}
    mapped['step_1']={'review_id_short':one['review_id_short'],'audio_asset':artifact('step_1',one['audio_asset_path'],'audio'),
        'events':[{'event_index':i,'start_ms':event['elapsedMs'],'end_ms':event['elapsedMs'],'target':event['target'],'kind':event['type']} for i,event in enumerate(events)]}
    mapped['step_2']=deepcopy(raw['step_2']['values'])
    mapped['step_3']={'component_names':[{key:text(value.get(key)) if key in {'component','source','spatial_hint','resolution_status'} else value[key] for key in ('component','source','selectors','aliases','spatial_hint','resolution_status')} for value in raw['step_3']['values']['component_names']]}
    four=raw['step_4']['values'];mapped['step_4']={'transcript_note':artifact('step_4',four['transcript_note_path'],'transcript'),'resolved_transcript':four['resolved_transcript']}
    five=raw['step_5']['values']
    mapped['step_5']={'observations':[{'observation_id':item['id'],'type':item['type'],'component':'; '.join(item['target_refs']),'selectors':item['target_refs'],'what_they_said':item['reviewer_quote'],'issue':item['normalized_claim'],'evidence':canonical(item['evidence']),'match_type':'legacy-normalized-evidence'} for item in five['observations']],
        'target_events':[{'target':event['target_ref'],'start_ms':event['elapsed_ms'],'end_ms':event['elapsed_ms'],'event_index':event_indexes[event['event_id']]} for event in five['target_events']],
        'review_packet':artifact('step_5',five['review_packet_path'],'packet'),'review_packet_status':five['review_packet_status'],
        **{key:{name:strings(items) for name,items in five[key].items()} for key in ['actionability','negative_evidence','implementation_handoff']},
        **{key:strings(five[key]) for key in ['silent_annotations','filtered_points']}}
    mapped['step_6']={'codebase_context':[{'observation_id':text(item['feedback_item_id']),'component':text(item['component']),'references':strings(item['references']),'likely_cause':text(item['likely_cause']),'reasoning':item['reason'],'confidence':item['confidence'],'caveats':strings(item['caveats']),'resolution_status':item['status']} for item in raw['step_6']['values']['codebase_context']]}
    seven=raw['step_7']['values'];final_packet=raw['step_7']['artifacts'][five['review_packet_path']]
    mapped['step_7']={'review_note':artifact('step_7',seven['review_note_path'],'review'),'review_packet':artifact('step_7',five['review_packet_path'],'final-packet'),'review_packet_status':json.loads(final_packet['content_utf8'])['quality']['status']}
    eight=raw['step_8']['values']['review_status_updated']
    mapped['step_8']={'review_status_updated':{'review_id':eight['review_id'],'status':eight['status'],'review_note':mapped['step_7']['review_note']}}
    mapped['step_9']={}
    roots=legacy['initial_context']
    events_path=next(path for path in raw['step_1']['artifacts'] if path.endswith('/events_events_1.json'))
    inputs={key:roots[key] for key in ['review_id','subject_id','submitted_by','reviewed_at','duration_ms']}
    inputs.update(audio_asset=mapped['step_1']['audio_asset'],events_asset=artifact('step_1',events_path,'events'))
    outcomes={step:({'outcome':'failed','reason':'Legacy packet validation rejected status writeback.'} if step in raw and raw[step]['terminal']=='FAILED' else {'outcome':'succeeded','outputs':value}) for step,value in mapped.items()}
    fixture={'schema':'runtime.shadow-fixture.v1','scenario_id':'legacy-'+legacy['status'],'inputs':inputs,'artifacts':artifacts,'steps':outcomes}
    return fixture,mapped

def compare_one(runtime_repo,ready):
    from process_runtime.domain import Snapshot
    from process_runtime.shadow import run_shadow
    draft=json.loads((runtime_repo/'resources/processes/personal-review-v1.json').read_text())
    with tempfile.TemporaryDirectory(prefix='legacy-review-shadow-') as directory:
        legacy=capture_legacy(HUB,Path(directory),ready=ready)
        fixture,mapped=migrate(legacy)
        report=run_shadow(draft,fixture).value
        expected='succeeded' if legacy['status']=='completed' else legacy['status']
        require(report['run_state']==expected,'Terminal outcomes diverged')
        require([row['step_id'] for row in report['steps']]==[row['selected'] for row in legacy['readiness']],'Selected handler order diverged')
        legacy_batches=[]
        for row in legacy['readiness']:
            available=row['runnable']
            if legacy_batches and set(available)<set(legacy_batches[-1]):continue
            legacy_batches.append(available)
        require(legacy_batches==[row['runnable'] for row in report['runnable_trace'] if row['runnable']],'Dependency-ready batches diverged')
        refs={name:{'content_hash':hashlib.sha256(item['content_utf8'].encode()).hexdigest(),'byte_length':len(item['content_utf8'].encode()),'media_type':item['media_type']} for name,item in fixture['artifacts'].items()}
        def materialize(value):
            if type(value) is dict:
                if set(value)=={'$artifact'}:return refs[value['$artifact']]
                return {key:materialize(item) for key,item in value.items()}
            if type(value) is list:return [materialize(item) for item in value]
            return value
        comparisons=[]
        for row in report['steps']:
            step=row['step_id'];source=mapped[step]
            expected_digest=digest(materialize(source)) if row['outcome']=='succeeded' else None
            require(row['output_digest']==expected_digest,'Typed migrated output digest diverged at '+step)
            require(row['receipt']['output_digest']==expected_digest and row['receipt_digest']==Snapshot.of(row['receipt']).digest,'Receipt output chain diverged at '+step)
            comparisons.append({'step_id':step,'legacy_terminal':legacy['outputs'][step]['terminal'],'runtime_outcome':row['outcome'],'migrated_output_digest':expected_digest,'runtime_receipt_digest':row['receipt_digest']})
        initial_packet=refs['step_5-packet'];final_packet=refs['step_7-final-packet']
        require(initial_packet['content_hash']!=final_packet['content_hash'],'Fixture did not exercise packet path mutation')
        require(mapped['step_7']['review_packet_status']==legacy['patches'][0]['review_packet_status'],'Final immutable packet status disagrees with legacy writeback')
        return {'scenario':'source-available' if ready else 'source-unavailable','legacy_state':legacy['status'],'runtime_state':report['run_state'],
            'runnable_batches':legacy_batches,'step_comparisons':comparisons,'initial_packet':initial_packet,'final_packet':final_packet,
            'compilation_hash':report['compilation_hash'],'retained_replay_fixture':fixture,'runtime_report':report,
            'runtime_report_sha256':digest(report),'legacy_execution':{key:legacy[key] for key in ('readiness','step_states','initial_context','patches')},
            'artifact_snapshot_count':len(refs),'legacy_status_writeback':legacy['patches'][0]['status'],'unclassified_divergences':[],
            'classified_differences':[
                {'category':'orchestration','detail':'Legacy selects one ready step in source order; new runtime claims each ready batch before completing it.'},
                {'category':'closed-types','detail':'Explicit normalizer projects raw events, component/feedback/source fields; nested rich values become canonical JSON strings, absent nullable strings become empty, and all original outputs remain retained JSON artifacts.'},
                {'category':'immutable-artifacts','detail':'Each path is captured at its producing commit. Step 7 publishes the newly source-bound packet explicitly; status writeback consumes it, not the step 5 snapshot.'},
                {'category':'failed-output','detail':'Legacy commits failed step 8 diagnostic outputs; runtime fail has no successful OutputCommit. The raw failure diagnostic is retained as a fixture artifact, and terminal failure/blocked downstream ordering is compared.'},
                {'category':'receipt-model','detail':'Legacy retains fenced step terminal rows and slots. New runtime retains schema-v1 immutable receipts; compare typed output digests and receipt integrity, not byte-identical receipt envelopes.'},
                {'category':'timing','detail':'Legacy uses a wall clock and per-step scheduling; new replay uses a fixed fixture clock. Timing and production throughput are not equivalent.'}
            ]}

def source_pins(runtime_repo):
    selected={'hub':(HUB,[
        'base/ops/processes/process-queued-review.md','base/ops/processes/resources/personal-review-v1.json',
        'services/cases/main.py','services/cases/execution_contract.py','services/frank/case_pipeline_runner.py',
        'services/frank/review_packet.py','services/frank/stt_client.py',
        'scripts/legacy_shadow_fixture.py','scripts/verify_legacy_process_shadow.py']),
        'process-runtime':(runtime_repo,['resources/processes/personal-review-v1.json','resources/processes/personal-review-v1.compiled.json',
        'src/process_runtime/shadow.py','src/process_runtime/compiler.py','src/process_runtime/application.py',
        'src/process_runtime/contracts.py','src/process_runtime/domain.py','src/process_runtime/authority.py','src/process_runtime/sqlite_store.py'])}
    return {component:{relative:hashlib.sha256((root/relative).read_bytes()).hexdigest() for relative in paths} for component,(root,paths) in selected.items()}

def verify(runtime_repo):
    runtime_repo=Path(runtime_repo).resolve()
    for path in (HUB,runtime_repo/'src'):
        if str(path) not in sys.path:sys.path.insert(0,str(path))
    logging.disable(logging.CRITICAL)
    pins=source_pins(runtime_repo)
    scenarios=[compare_one(runtime_repo,True),compare_one(runtime_repo,False)]
    require(source_pins(runtime_repo)==pins,'Comparison sources changed during execution')
    return {'schema':'hub.legacy-process-shadow.v1','status':'passed','comparison_mode':'actual-contained-legacy-to-new-runtime-output-replay',
        'production_ready':False,'external_effects':'mocked transport and temporary files only','process_resource_sha256':hashlib.sha256((runtime_repo/'resources/processes/personal-review-v1.json').read_bytes()).hexdigest(),
        'source_sha256':pins,'scenarios':scenarios,
        'limitations':['Greenfield production review handlers are not independently executed; their outputs are explicit migrations of actual retained legacy handler results.','No live cases, operator files, provider credentials, runtime cutover, or real status writeback were used.','The classified closed-type and failure-evidence migrations need integration-owner review before a live migration; fixture agreement is not production semantic equivalence.']}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--runtime-repo',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args(argv)
    try:
        report=verify(args.runtime_repo)
    except Exception as error:
        report={'schema':'hub.legacy-process-shadow.v1','status':'failed','production_ready':False,'error_type':type(error).__name__,'detail':str(error)}
        args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
        print(json.dumps(report,sort_keys=True));return 1
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'status':report['status'],'scenarios':len(report['scenarios']),'production_ready':False,'report_sha256':digest(report)}))
    return 0
if __name__=='__main__':raise SystemExit(main())
