"""Public bootstrap/verification checks in the dedicated locked host closure."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts import bootstrap_process_image_tools as tools

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT/'scripts/bootstrap_process_image_tools.py'
ORIGINAL = json.loads((Path(sys.prefix)/tools.RECEIPT).read_bytes())
WHEELHOUSE = Path(ORIGINAL['wheelhouse'])


def run(python, *arguments):
    return subprocess.run([str(python), '-I', '-B', str(SCRIPT), *map(str, arguments)],
                          capture_output=True, timeout=40)


def test_public_verify_receipt_and_actual_module_origins():
    result = run(sys.executable, 'verify')
    assert result.returncode == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload['packages'] == {'pip':'25.3','packaging':'26.2','pytest':'9.1.1',
                                   'iniconfig':'2.3.1','pluggy':'1.6.0','pygments':'2.20.0'}
    for name in tools.MODULES:
        assert Path(importlib.util.find_spec(name).origin).resolve().is_relative_to(Path(sys.prefix).resolve())


def test_unisolated_interpreter_cannot_use_tooling():
    result = run(sys._base_executable, 'verify')
    assert result.returncode != 0 and json.loads(result.stdout)['error_type'] == 'HostToolError'


def test_fresh_public_offline_bootstrap_and_receipt(tmp_path,monkeypatch):
    config=tmp_path/'ambient-pip.conf'
    ambient=tmp_path/'unadmitted-target'
    config.write_text('[global]\ntarget = '+str(ambient)+'\n')
    monkeypatch.setenv('PIP_CONFIG_FILE',str(config))
    monkeypatch.setenv('PIP_TARGET',str(ambient))
    destination=tmp_path/'new-host'
    result=run(sys._base_executable,'create','--environment',destination,'--wheelhouse',WHEELHOUSE)
    assert result.returncode == 0, result.stdout+result.stderr
    receipt=json.loads((destination/tools.RECEIPT).read_bytes())
    assert receipt['wheels'] == ORIGINAL['wheels'] and receipt['python'] == ORIGINAL['python']
    assert run(destination/'bin/python','verify').returncode == 0
    assert 'include-system-site-packages = false' in (destination/'pyvenv.cfg').read_text()
    assert not ambient.exists()


@pytest.mark.parametrize('change', ['missing','tampered','extra','symlink'])
def test_bad_host_wheelhouse_denies_before_creating_environment(tmp_path,change):
    wheels = tmp_path/'wheels';shutil.copytree(WHEELHOUSE,wheels)
    target = wheels/ORIGINAL['wheels'][0]['filename']
    if change == 'missing': target.unlink()
    elif change == 'tampered': target.write_bytes(target.read_bytes()+b'changed')
    elif change == 'extra': (wheels/'unadmitted.whl').write_bytes(b'extra')
    else: target.unlink();target.symlink_to(WHEELHOUSE/target.name)
    environment = tmp_path/'environment'
    result = run(sys._base_executable,'create','--environment',environment,'--wheelhouse',wheels)
    assert result.returncode != 0 and json.loads(result.stdout)['error_type'] == 'HostToolError'
    assert not environment.exists()


def test_python_and_lock_mismatch_fail_closed(tmp_path,monkeypatch):
    profile = json.loads(tools.PROFILE.read_bytes());profile['python']['version']='3.13.0'
    path = tmp_path/'profile.json';path.write_text(json.dumps(profile))
    monkeypatch.setattr(tools,'PROFILE',path)
    with pytest.raises(tools.HostToolError,match='interpreter'): tools.admitted_profile()
    profile['python']['version']='3.12.12';path.write_text(json.dumps(profile))
    lock = tmp_path/'requirements.lock';lock.write_text('packaging==26.3\n')
    monkeypatch.setattr(tools,'LOCK',lock)
    with pytest.raises(tools.HostToolError,match='lock/profile'): tools.admitted_profile()


@pytest.fixture
def copied_environment(tmp_path):
    target = tmp_path/'host'
    shutil.copytree(sys.prefix,target,symlinks=True)
    return target


@pytest.mark.parametrize('change', ['package','receipt','extra-distribution','missing-receipt','extra-module'])
def test_installed_environment_drift_denies(copied_environment,change):
    prefix=copied_environment
    site=prefix/'lib/python3.12/site-packages'
    if change == 'package':
        path=site/'packaging/__init__.py';path.write_bytes(path.read_bytes()+b'\n# changed\n')
    elif change == 'receipt':
        path=prefix/tools.RECEIPT;value=json.loads(path.read_bytes());value['python']['version']='3.13.0';path.write_text(json.dumps(value))
    elif change == 'extra-distribution':
        path=site/'unadmitted-1.0.dist-info';path.mkdir();(path/'METADATA').write_text('Name: unadmitted\nVersion: 1.0\n')
    elif change == 'missing-receipt': (prefix/tools.RECEIPT).unlink()
    else: (site/'unadmitted.py').write_text('# no executable code\n')
    result=run(prefix/'bin/python','verify')
    assert result.returncode != 0 and json.loads(result.stdout)['error_type'] == 'HostToolError'


def test_explicit_source_override_cannot_supply_packaging_parser(tmp_path):
    fake=tmp_path/'packaging';fake.mkdir();(fake/'__init__.py').write_text('')
    code='import sys;sys.path.insert(0,sys.argv[1]);sys.path.insert(0,sys.argv[2]);import scripts.process_component_images'
    result=subprocess.run([sys.executable,'-I','-B','-c',code,str(tmp_path),str(ROOT)],capture_output=True,timeout=30)
    assert result.returncode != 0 and b'HostToolError' in result.stderr and b'origin' in result.stderr


def test_ambient_pythonpath_is_ignored_for_public_cli(tmp_path):
    fake=tmp_path/'packaging';fake.mkdir();(fake/'__init__.py').write_text('raise RuntimeError("ambient source override")\n')
    env={**os.environ,'PYTHONPATH':str(tmp_path)}
    result=subprocess.run([sys.executable,'-I','-B',str(ROOT/'scripts/process_component_images.py'),'--help'],
                          capture_output=True,env=env,timeout=30)
    assert result.returncode == 0 and b'prepare' in result.stdout


def test_existing_host_environment_is_create_only():
    result=run(sys._base_executable,'create','--environment',sys.prefix,'--wheelhouse',WHEELHOUSE)
    assert result.returncode != 0 and json.loads(result.stdout)['error_type'] == 'HostToolError'
