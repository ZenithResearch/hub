"""The actual contract-check CLI rejects drift before starting any component."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HUB=Path(__file__).resolve().parents[1]


class ProcessCompatibilityTests(unittest.TestCase):
    def invoke(self,config):
        return subprocess.run([sys.executable,str(HUB/'scripts/check_process_contracts.py'),'--config',str(config)],capture_output=True,text=True,timeout=30)

    def test_reviewed_public_contract_row_passes(self):
        result=self.invoke(HUB/'config/process-execution.components.json')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(set(json.loads(result.stdout)['contracts']),{'process-runtime','service-management','frank'})

    def test_modified_public_schema_rejects_via_ci_cli(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)
            shutil.copytree(HUB/'config/process-contracts',directory/'process-contracts')
            config=directory/'components.json';shutil.copyfile(HUB/'config/process-execution.components.json',config)
            schema=directory/'process-contracts/process-runtime.json'
            value=json.loads(schema.read_text());value['$defs']['command']['oneOf'][0]['properties']['schema']['const']='runtime.command.v99'
            schema.write_text(json.dumps(value))
            env={**os.environ,'SKIP_PYTHON_TESTS':'1','SKIP_PRIVATE_ARTIFACT_SCAN':'1','SKIP_TERRAFORM':'1','SKIP_COMPOSE_CONFIG':'1','PYTHON_BIN':sys.executable,'PROCESS_COMPOSITION_CONFIG':str(config)}
            result=subprocess.run(['bash',str(HUB/'scripts/ci_check.sh')],cwd=HUB,env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,1,result.stdout+result.stderr)
            report=json.loads(result.stdout)
            self.assertEqual(report['status'],'failed')
            self.assertIn('Incompatible contract for process-runtime',report['detail'])

    def test_unqualified_production_row_rejects_before_schema_read(self):
        with tempfile.TemporaryDirectory() as folder:
            config=Path(folder)/'components.json'
            value=json.loads((HUB/'config/process-execution.components.json').read_text())
            value.update(environment='production',admission_mode='greenfield')
            config.write_text(json.dumps(value))
            result=self.invoke(config)
            self.assertEqual(result.returncode,1)
            self.assertIn('independently qualified native authority',json.loads(result.stdout)['detail'])


if __name__=='__main__':unittest.main()
