from __future__ import annotations
import asyncio
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch


def capture_legacy(root: Path, directory: Path, *, ready: bool):
    import httpx
    from fastapi.testclient import TestClient
    from services.cases.execution_contract import profile_for,runnable
    from services.frank.case_pipeline_runner import CasePipelineRunner,read_slot_values
    source=(root/'base/ops/processes/process-queued-review.md').read_text()
    codebase=directory/'codebase';codebase.mkdir(parents=True)
    (codebase/'view.tsx').write_text('<button className="review-dismiss" />')
    outputs={};traces=[];artifacts={};patches=[]
    def capture_artifacts():
        for path in directory.glob('**/*'):
            if path.is_file() and ('artifacts' in path.parts or 'assets' in path.parts):
                artifacts[str(path)]={'content_utf8':path.read_text(),'media_type':'application/json' if path.suffix=='.json' else 'text/markdown' if path.suffix=='.md' else 'text/plain'}
    class RecordingRunner(CasePipelineRunner):
        async def run_step(self,case_run_id,case_id,packet,detail,step,case_dir):
            traces.append({'selected':step['step_id'],'runnable':sorted(row['step_id'] for row in runnable(detail['contract'],detail['steps'],read_slot_values(detail),profile=profile_for(detail['contract'])))})
            await super().run_step(case_run_id,case_id,packet,detail,step,case_dir)
        async def complete_output_step(self,case_id,step,values,step_run_id,**kwargs):
            capture_artifacts()
            outputs[step['step_id']]={'values':deepcopy(values),'terminal':kwargs.get('terminal_status','COMPLETED'),'artifacts':deepcopy(artifacts)}
            return await super().complete_output_step(case_id,step,values,step_run_id,**kwargs)
        async def complete_no_output_step(self,case_id,step,step_run_id,**kwargs):
            capture_artifacts();outputs[step['step_id']]={'values':{},'terminal':'COMPLETED','artifacts':deepcopy(artifacts)}
            return await super().complete_no_output_step(case_id,step,step_run_id,**kwargs)
    env={'CASES_DB_PATH':str(directory/'cases.db'),'FRANK_EXECUTION_ROOT':str(directory),'FRANK_SUBJECT_CODEBASE_MAP':f'http://fixture.test={codebase}' if ready else ''}
    with patch.dict(os.environ,env):
        from services.cases import main
        main=importlib.reload(main)
        with TestClient(main.app) as client:
            created=client.post('/cases',json={'queue_message_id':'fixture-request','process_name':'personal-review','process_path':'process-queued-review','process_source':source,'title':'review','objective':'review','sender':'test'})
            created.raise_for_status();case=created.json()
            roots={name:(0 if case['contract']['variables'][name]['type']=='number' else 'root-'+name) for name in case['contract']['root_inputs']}
            roots.update(review_id='review_12345678',audio_asset_id='audio_1',events_asset_id='events_1',subject_id='http://fixture.test',duration_ms=1000,reviewed_at='2026-01-01T00:00:00Z')
            events=[{'id':1,'type':'click','target':'button.review-dismiss','elapsedMs':200,'x':10,'y':10}]
            transcript='This button is not centered.'
            transcription={'transcript':transcript,'words':[{'text':word,'start':i*.1,'end':i*.1+.1} for i,word in enumerate(transcript.split())]}
            class Transport(httpx.AsyncBaseTransport):
                async def handle_async_request(self,request):
                    if request.url.host=='cases':return await httpx.ASGITransport(app=main.app).handle_async_request(request)
                    if request.url.path.endswith('/assets/audio_1'):return httpx.Response(200,content=b'audio',headers={'content-type':'audio/webm'})
                    if request.url.path.endswith('/assets/events_1'):return httpx.Response(200,json=events)
                    if request.method=='PATCH':
                        patches.append(json.loads(request.content));return httpx.Response(200,json={'review_id':roots['review_id'],'status':patches[-1]['status']})
                    raise AssertionError(str(request.url))
            async def execute():
                async with httpx.AsyncClient(transport=Transport()) as transport:
                    runner=RecordingRunner(client=transport,cases_url='http://cases',gateway_url='http://gateway',stt_url='http://stt',execution_root=directory)
                    with patch('services.frank.stt_client.transcribe_audio',new=AsyncMock(return_value=transcription)) as stt:
                        result=await runner.run(case['case_id'],{'initial_context':roots})
                        assert stt.await_count==1
                        return result
            result=asyncio.run(execute())
            detail=client.get(f"/cases/{case['case_id']}").json()
            return {'status':result.status,'outputs':outputs,'readiness':traces,'initial_context':roots,'events':events,'patches':patches,'step_states':{row['step_id']:row['status'] for row in detail['steps']},'case_id':case['case_id'],'run_id':result.case_run_id}
