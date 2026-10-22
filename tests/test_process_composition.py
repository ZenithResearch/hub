from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from services.process_composition import admission, canonical, change_admission, CompositionError, legacy_eligibility, validate_config, verify_contracts

ROOT=Path(__file__).resolve().parents[1]

class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.config=json.loads((ROOT/'config/process-execution.components.json').read_text())
    def test_explicit_pin_mismatch_and_ownership(self):
        validate_config(self.config)
        self.assertEqual(admission(self.config)['existing_run_owner'],'process-runtime')
        for bad in ({**self.config,'cases':[]}, {**self.config,'external_effects':True}):
            with self.assertRaises(CompositionError): validate_config(bad)
        paths={name:ROOT/'config/process-contracts'/f'{name}.json' for name in self.config['components']}
        verify_contracts(self.config,paths)
        changed=deepcopy(self.config);changed['components']['process-runtime']['contract_sha256']='0'*64
        with self.assertRaises(CompositionError): verify_contracts(changed,paths)
    def test_shadow_greenfield_and_rollback_change_admission_only(self):
        value=deepcopy(self.config);value['environment']='test'
        for mode in ('greenfield','legacy_read_only','shadow'):
            before=deepcopy(value['components'])
            value=change_admission(value,expected_digest=hashlib.sha256(canonical(value)).hexdigest(),mode=mode)
            self.assertEqual(value['components'],before)
            self.assertFalse(value['external_effects'])
            self.assertEqual(admission(value)['existing_run_owner'],'process-runtime')
        with self.assertRaises(CompositionError):change_admission(value,expected_digest='0'*64,mode='greenfield')
    def test_production_gate_and_secret_references(self):
        value=deepcopy(self.config);value['environment']='production';value['admission_mode']='greenfield'
        with self.assertRaises(CompositionError):validate_config(value)
        value=deepcopy(self.config);value['secret_references']={'matrix':'literal-token'}
        with self.assertRaises(CompositionError):validate_config(value)
    def test_legacy_ordinal_completion_never_becomes_success(self):
        clean={'status':'open','execution_eligibility':'eligible','frozen_contract_hash':'a'*64,'root_inputs':{'intent':'review'}}
        # Hash/eligibility labels without actual retained evidence now deny.
        self.assertFalse(legacy_eligibility(clean)['eligible'])
        for changes in ({'status':'completed'},{'step_progress':{'9':'done'}},{'active_lease':True},{'unknown_effects':['effect']},{'unfenced_progress':True},{'outputs':{'path':'old'}}):
            self.assertFalse(legacy_eligibility({**clean,**changes})['eligible'])

if __name__=='__main__':unittest.main()
