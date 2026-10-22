"""Prepublication image input tests; real container smoke is a separate receipt."""
import base64
from contextlib import contextmanager
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import subprocess
from unittest.mock import patch
import zipfile

import pytest

from scripts.process_component_images import (
    ImageInputError, build_images, canonical, entrypoint, prepare_contexts, validate_prepared,
    verify_oci, wheel_inventory, HOST_TOOLS,
    verify_base_export, select_loaded_image,
)



@contextmanager
def docker_replay(docker, *, finish_error=False):
    """Synthetic product-flow responses; real lifecycle has its own offline tests."""
    class ReplayExecution:
        def __init__(self, output, executable, checkpoints):
            self.output, self.docker = output, executable
            self.scratch = output/'synthetic-scratch'
            self.scratch.mkdir()
        def install_handlers(self): pass
        def restore_handlers(self): pass
        def fail(self, phase, error): pass
        def finish(self):
            if finish_error:
                from scripts.process_image_execution import ExecutionFailure
                raise ExecutionFailure('Synthetic daemon cleanup unknown')
        def run(self, name, argv, **kwargs): return docker(argv, **kwargs)
        def build(self, name, argv): return self.run(name, argv)
        def container(self, name, image, arguments):
            return self.run(name, [self.docker, 'run', '--rm', '--name', 'synthetic-'+name,
                '--cidfile', str(self.output/(name+'.cid')), '--label', 'synthetic-only=true',
                '--pull=never', '--network=none', '--read-only', '--cap-drop=ALL',
                '--security-opt=no-new-privileges', '--tmpfs', '/data:uid=10001,gid=10001,mode=0700',
                '--tmpfs', '/tmp:mode=1777', '--entrypoint', '/opt/component/bin/python',
                image, '-I', '-B', *arguments])
    with patch('scripts.process_component_images._execution.ImageExecution', ReplayExecution):
        yield


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def wheel(directory, name, version='0.1.0', *, requires=(), platform='any', unsafe=None, binary=None):
    stem = name.replace('-', '_')
    filename = f'{stem}-{version}-py3-none-{platform}.whl'
    info = f'{stem}-{version}.dist-info'
    entries = {f'{stem}/__init__.py': b'', f'{info}/METADATA':
               ('Metadata-Version: 2.1\nName: '+name+'\nVersion: '+version+'\nRequires-Python: >=3.12,<3.13\n'+
                ''.join('Requires-Dist: '+r+'\n' for r in requires)).encode(),
               f'{info}/WHEEL': ('Wheel-Version: 1.0\nTag: py3-none-'+platform+'\n').encode()}
    if name in ('process-runtime', 'frank-executor'):
        entries['schemas/runtime.json'] = b'{"schema":"runtime-fixture.v1"}'
    if unsafe: entries[unsafe] = b'unsafe'
    if binary: entries['native-helper'] = binary
    records = [f'{p},sha256={base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")},{len(raw)}\n'
               for p, raw in entries.items()]
    entries[f'{info}/RECORD'] = (''.join(records)+f'{info}/RECORD,,\n').encode()
    path = directory / filename
    with zipfile.ZipFile(path, 'w') as archive:
        for p, raw in entries.items(): archive.writestr(p, raw)
    return {'filename': filename, 'name': name, 'version': version, 'sha256': sha(path.read_bytes())}


@pytest.fixture
def inputs(tmp_path):
    wheelhouse = tmp_path / 'wheels'; wheelhouse.mkdir()
    wheels = [wheel(wheelhouse, 'process-runtime', requires=('jsonschema>=4.25,<5',)),
              wheel(wheelhouse, 'service-management'),
              wheel(wheelhouse, 'frank-executor', requires=('process-runtime==0.1.0',)),
              wheel(wheelhouse, 'jsonschema', '4.26.0')]
    policy = json.loads((Path(__file__).resolve().parents[1] / 'config/process-component-images.v1.json').read_text())
    policy['wheels'] = wheels
    policy['contracts'] = {key: {'package': name, 'member': 'schemas/runtime.json',
                                'sha256': sha(b'{"schema":"runtime-fixture.v1"}')}
                           for key, name in [('runtime-sdk', 'process-runtime'), ('frank-runtime', 'frank-executor')]}
    components = {}
    for owner, name in [('process-runtime', 'process-runtime'), ('service-management', 'service-management'), ('frank', 'frank-executor')]:
        item = next(w for w in wheels if w['name'] == name)
        policy['components'][owner].update(source_commit='a'*40, wheel_sha256=item['sha256'])
        components[owner] = {'package': name, 'version': '0.1.0', 'wheel': '/not-used/'+item['filename'], 'wheel_sha256': item['sha256']}
    artifact = {'schema': 'hub.process-artifact-tuple.v1', 'components': components,
                'dependencies': {w['name']: {'version': w['version'], 'files': {}} for w in wheels},
                'production_ready': False}
    policy['tuple_manifest_sha256'] = sha(canonical(artifact))
    tuple_path = tmp_path / 'tuple.json'; tuple_path.write_bytes(canonical(artifact))
    return policy, tuple_path, wheelhouse, tmp_path


def prepare(inputs, name='prepared'):
    policy, artifact, wheels, directory = inputs
    return prepare_contexts(policy, artifact, wheels, directory/name)


def test_prepared_contexts_reproduce_and_separate_component_roots(inputs):
    first = prepare(inputs)
    second = prepare(inputs, 'second')
    assert first == second
    root = inputs[3] / 'prepared'
    validate_prepared(root, sha((root/'contexts.json').read_bytes()))
    for owner in ('process-runtime', 'service-management', 'frank'):
        manifest = json.loads((root/owner/'build-inputs.json').read_text())
        assert manifest['component'] == owner and manifest['production_ready'] is False
        assert manifest['native_helpers'] == []
        assert manifest['host_tools'] == HOST_TOOLS
        component = manifest['source']
        admitted = next(p for p in manifest['packages'] if p['name'] == component['package'])
        assert admitted['sha256'] == component['wheel_sha256']
        assert 'RUN --network=none' in (root/owner/'Dockerfile').read_text()
        assert '--require-hashes' in (root/owner/'Dockerfile').read_text()
    runtime = (root/'process-runtime/requirements.lock').read_text()
    assert 'frank-executor' not in runtime and 'service-management' not in runtime


@pytest.mark.parametrize('change', ['missing', 'tampered', 'unlocked-extra'])
def test_missing_tampered_and_extra_wheels_deny_without_context(inputs, change):
    policy, _, wheels, directory = inputs
    path = wheels/policy['wheels'][0]['filename']
    if change == 'missing': path.unlink()
    elif change == 'tampered': path.write_bytes(path.read_bytes()+b'x')
    else: (wheels/'extra.whl').write_bytes(b'not admitted')
    with pytest.raises(ImageInputError): prepare(inputs)
    assert not (directory/'prepared').exists()


def test_tuple_mutation_denies_even_same_component_versions(inputs):
    path = inputs[1]; artifact = json.loads(path.read_bytes())
    artifact['components']['frank']['wheel_sha256'] = '0'*64
    path.write_bytes(canonical(artifact))
    with pytest.raises(ImageInputError): prepare(inputs)


def test_admitted_component_cannot_disagree_with_source_and_tuple_hash(inputs):
    policy, _, wheels, directory = inputs
    row = next(w for w in policy['wheels'] if w['name'] == 'process-runtime')
    original = row['sha256']
    replacement = wheel(wheels, 'process-runtime', requires=('jsonschema>=4.25,<5',), binary=b'changed-valid-payload')
    assert replacement['sha256'] != original
    row['sha256'] = replacement['sha256']
    with pytest.raises(ImageInputError): prepare(inputs)
    assert not (directory/'prepared').exists()


@pytest.mark.parametrize('change', ['wrong-python', 'mutable-base', 'native-helper', 'unknown-component'])
def test_policy_cannot_weaken_platform_or_native_boundary(inputs, change):
    policy = inputs[0]
    if change == 'wrong-python': policy['base']['python_version'] = '3.13.0'
    elif change == 'mutable-base': policy['base']['reference'] = 'python:latest'
    elif change == 'native-helper': policy['native_helpers'] = ['darwin-helper']
    else: policy['components']['other'] = {}
    with pytest.raises(ImageInputError): prepare(inputs)


@pytest.mark.parametrize('platform', ['macosx_11_0_arm64', 'manylinux_2_28_x86_64', 'win_amd64'])
def test_wrong_platform_wheel_is_rejected_before_build(tmp_path, platform):
    item = wheel(tmp_path, 'example', platform=platform)
    with pytest.raises(ImageInputError): wheel_inventory(tmp_path/item['filename'], item, 'linux/arm64')


def test_zip_path_traversal_denies_even_reviewed_file_digest(tmp_path):
    item = wheel(tmp_path, 'example', unsafe='../private-key')
    with pytest.raises(ImageInputError): wheel_inventory(tmp_path/item['filename'], item, 'linux/arm64')


def test_darwin_helper_inside_any_tag_wheel_is_rejected(tmp_path):
    item = wheel(tmp_path, 'example', binary=b'\xcf\xfa\xed\xfe' + b'fixture')
    with pytest.raises(ImageInputError, match='Darwin'): wheel_inventory(tmp_path/item['filename'], item, 'linux/arm64')


def test_missing_and_incompatible_transitive_dependencies_deny(inputs):
    policy, artifact, wheels, directory = inputs
    old = next(w for w in policy['wheels'] if w['name']=='jsonschema')
    policy['wheels'].remove(old); (wheels/old['filename']).unlink()
    with pytest.raises(ImageInputError): prepare(inputs)


def test_contract_hash_mismatch_denies_before_build(inputs):
    inputs[0]['contracts']['frank-runtime']['sha256'] = '0'*64
    with pytest.raises(ImageInputError): prepare(inputs)


def test_incompatible_metadata_dependency_denies_before_build(inputs):
    policy, artifact, wheels, _ = inputs
    old = next(w for w in policy['wheels'] if w['name']=='jsonschema')
    (wheels/old['filename']).unlink()
    new = wheel(wheels, 'jsonschema', '3.0.0'); policy['wheels'].remove(old); policy['wheels'].append(new)
    value = json.loads(artifact.read_bytes()); value['dependencies']['jsonschema']['version'] = '3.0.0'
    artifact.write_bytes(canonical(value)); policy['tuple_manifest_sha256'] = sha(artifact.read_bytes())
    with pytest.raises(ImageInputError, match='Incompatible transitive'): prepare(inputs)


@pytest.mark.parametrize('target', ['Dockerfile', 'requirements.lock', 'build-inputs.json', 'inventory.py'])
def test_changed_context_denies_prepublication(inputs, target):
    prepare(inputs); root=inputs[3]/'prepared'; digest=sha((root/'contexts.json').read_bytes())
    path=root/'frank'/target; path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ImageInputError): validate_prepared(root,digest)


def test_added_private_config_denies_prepublication(inputs):
    prepare(inputs); root=inputs[3]/'prepared'; digest=sha((root/'contexts.json').read_bytes())
    (root/'frank'/'.env').write_text('PRIVATE=sentinel')
    with pytest.raises(ImageInputError): validate_prepared(root,digest)


def test_prepared_root_requires_reviewed_digest_and_no_overwrite(inputs):
    prepare(inputs); root=inputs[3]/'prepared'
    with pytest.raises(ImageInputError): validate_prepared(root,'0'*64)
    with pytest.raises(ImageInputError): prepare(inputs)


def test_context_drift_is_rejected_before_any_docker_action(inputs):
    prepare(inputs); root=inputs[3]/'prepared'; digest=sha((root/'contexts.json').read_bytes())
    (root/'frank/Dockerfile').write_text('FROM unreviewed:latest')
    with patch('scripts.process_component_images.subprocess.run') as docker:
        with pytest.raises(ImageInputError): build_images(root,digest,inputs[3]/'images')
    docker.assert_not_called()
    assert not (inputs[3]/'images').exists()


def layer_tar(number):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        payload = f'fixture {number}'.encode()
        item = tarfile.TarInfo(f'layer-{number}.txt'); item.size = len(payload)
        archive.addfile(item, io.BytesIO(payload))
    return output.getvalue()


def image_archive(path, policy, *, mutate=None, corrupt=False, false_diff_ids=False,
                  layer_bytes=None, media='application/vnd.oci.image.layer.v1.tar+gzip', encoded=None,
                  descriptor_mutate=None, archive_extra=None, owner='frank'):
    layers = [layer_tar(n) for n in range(4)] if layer_bytes is None else layer_bytes
    actual_diff_ids = ['sha256:'+sha(raw) for raw in layers]
    claims = ['sha256:'+str(n)*64 for n in range(4)] if false_diff_ids else actual_diff_ids
    policy['base']['rootfs_diff_ids'] = claims
    config={'architecture':'arm64','os':'linux',
        'rootfs':{'type':'layers','diff_ids':claims},
        'config':{'Entrypoint':entrypoint(owner),'Cmd':['--help'],'User':'10001:10001',
                  'Labels':{'io.hub.production-qualified':'false','io.hub.native-helpers':'absent',
                            'org.opencontainers.image.revision':policy['components'][owner]['source_commit']}}}
    if mutate: mutate(config)
    files={}
    def blob(raw, media):
        digest=sha(raw);files['blobs/sha256/'+digest]=raw
        return {'digest':'sha256:'+digest,'size':len(raw),'mediaType':media}
    cfg=blob(canonical(config),'application/vnd.oci.image.config.v1+json')
    descriptors = [blob(encoded if encoded is not None else gzip.compress(raw,mtime=0) if media.endswith('+gzip') else raw,media)
                   for raw in layers]
    if descriptor_mutate: descriptor_mutate(descriptors)
    manifest=blob(canonical({'schemaVersion':2,'config':cfg,'layers':descriptors}), 'application/vnd.oci.image.manifest.v1+json')
    files['index.json']=canonical({'schemaVersion':2,'manifests':[manifest]})
    files['oci-layout']=canonical({'imageLayoutVersion':'1.0.0'})
    if corrupt: files['blobs/sha256/'+cfg['digest'].split(':')[1]]=b'changed'
    if archive_extra: files.update(archive_extra)
    with tarfile.open(path,'w') as archive:
        for name,raw in files.items():
            entry=tarfile.TarInfo(name);entry.size=len(raw);archive.addfile(entry,io.BytesIO(raw))


def test_oci_artifact_digest_and_owner_entrypoint_validation(inputs):
    path=inputs[3]/'image.tar';image_archive(path,inputs[0])
    result=verify_oci(path,inputs[0],'frank')
    assert result['archive_sha256']==sha(path.read_bytes())
    assert result['rootfs_diff_ids'] == inputs[0]['base']['rootfs_diff_ids']
    assert result['uncompressed_layer_bytes'] == sum(len(layer_tar(n)) for n in range(4))


@pytest.mark.parametrize('change', ['architecture','entrypoint','user','qualification','source','base'])
def test_wrong_oci_image_is_rejected_before_publication(inputs,change):
    def mutate(config):
        if change=='architecture':config['architecture']='amd64'
        elif change=='entrypoint':config['config']['Entrypoint']=['sh']
        elif change=='user':config['config']['User']='root'
        elif change=='qualification':config['config']['Labels']['io.hub.production-qualified']='true'
        elif change=='source':config['config']['Labels']['org.opencontainers.image.revision']='0'*40
        else:config['rootfs']['diff_ids']=[]
    path=inputs[3]/'image.tar';image_archive(path,inputs[0],mutate=mutate)
    with pytest.raises(ImageInputError):verify_oci(path,inputs[0],'frank')


def test_tampered_oci_blob_rejected(inputs):
    path=inputs[3]/'image.tar';image_archive(path,inputs[0],corrupt=True)
    with pytest.raises(ImageInputError):verify_oci(path,inputs[0],'frank')


def test_valid_compressed_descriptors_cannot_lie_about_diff_ids(inputs):
    path=inputs[3]/'false-base.tar';image_archive(path,inputs[0],false_diff_ids=True)
    with pytest.raises(ImageInputError): verify_oci(path,inputs[0],'frank')


def test_valid_uncompressed_oci_layers(inputs):
    path=inputs[3]/'plain.tar';image_archive(path,inputs[0],media='application/vnd.oci.image.layer.v1.tar')
    assert verify_oci(path,inputs[0],'frank')['rootfs_diff_ids'] == inputs[0]['base']['rootfs_diff_ids']


@pytest.mark.parametrize('kind', ['order', 'count', 'rootfs-type', 'unsupported', 'bad-gzip', 'bad-tar', 'trailing-payload'])
def test_layer_structure_and_order_are_verified(inputs,kind):
    kwargs = {}
    if kind == 'order': kwargs['descriptor_mutate'] = lambda layers: layers.reverse()
    elif kind == 'count': kwargs['mutate'] = lambda cfg: cfg['rootfs']['diff_ids'].pop()
    elif kind == 'rootfs-type': kwargs['mutate'] = lambda cfg: cfg['rootfs'].update(type='other')
    elif kind == 'unsupported': kwargs['media'] = 'application/vnd.oci.image.layer.v1.tar+zstd'
    elif kind == 'bad-gzip': kwargs['encoded'] = b'invalid gzip with valid compressed SHA'
    elif kind == 'bad-tar': kwargs['layer_bytes'] = [b'not a tar']
    else: kwargs['layer_bytes'] = [layer_tar(0)+b'hidden nonzero trailer'+b'\0'*(512-22)]
    path=inputs[3]/'bad-structure.tar';image_archive(path,inputs[0],**kwargs)
    with pytest.raises(ImageInputError): verify_oci(path,inputs[0],'frank')


def tar_with_member(name, *, link=None, kind=None):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w') as archive:
        item = tarfile.TarInfo(name)
        if link is not None: item.type=tarfile.SYMTYPE; item.linkname=link
        if kind is not None: item.type=kind
        archive.addfile(item)
    return raw.getvalue()


@pytest.mark.parametrize('raw', [tar_with_member('../escape'), tar_with_member('/absolute'),
    tar_with_member('a/link',link='../../escape'), tar_with_member('device',kind=tarfile.CHRTYPE)])
def test_layer_paths_links_and_special_members_deny(inputs,raw):
    path=inputs[3]/'unsafe-layer.tar';image_archive(path,inputs[0],layer_bytes=[raw])
    with pytest.raises(ImageInputError): verify_oci(path,inputs[0],'frank')


@pytest.mark.parametrize('link', ['../lib/python3.12', '/usr/lib/python3.12'])
def test_safe_container_root_symlinks_are_accepted_without_extraction(inputs,link):
    path=inputs[3]/'safe-link.tar';image_archive(path,inputs[0],layer_bytes=[tar_with_member('usr/bin/python',link=link)])
    assert verify_oci(path,inputs[0],'frank')['rootfs_diff_ids'] == inputs[0]['base']['rootfs_diff_ids']


@pytest.mark.parametrize('constant,bound', [('MAX_OCI_BYTES',100), ('MAX_OCI_MEMBERS',2),
    ('MAX_JSON_BYTES',20), ('MAX_LAYERS',2), ('MAX_COMPRESSED_LAYER',30),
    ('MAX_LAYER_BYTES',5000), ('MAX_TOTAL_LAYER_BYTES',30000), ('MAX_LAYER_MEMBERS',0), ('MAX_TAR_HEADERS',0)])
def test_oci_limits_deny_before_publication(inputs,monkeypatch,constant,bound):
    path=inputs[3]/'bounded.tar';image_archive(path,inputs[0])
    monkeypatch.setattr('scripts.process_component_images.'+constant,bound)
    with pytest.raises(ImageInputError): verify_oci(path,inputs[0],'frank')


def test_extended_tar_metadata_allocation_is_bounded(inputs):
    path=inputs[3]/'extended.tar'
    raw=io.BytesIO()
    with tarfile.open(fileobj=raw,mode='w',format=tarfile.PAX_FORMAT) as archive:
        item=tarfile.TarInfo('a');item.pax_headers={'comment':'x'*70000};archive.addfile(item)
    image_archive(path,inputs[0],layer_bytes=[raw.getvalue()])
    with pytest.raises(ImageInputError,match='extended metadata'):verify_oci(path,inputs[0],'frank')


def test_unreferenced_oci_payload_denies(inputs):
    path=inputs[3]/'extra.tar';image_archive(path,inputs[0],archive_extra={'unadmitted-data':b'fixture'})
    with pytest.raises(ImageInputError,match='Unreferenced'):verify_oci(path,inputs[0],'frank')


def test_empty_but_valid_layer_tar_is_supported(inputs):
    path=inputs[3]/'empty-layer.tar'
    raw=io.BytesIO()
    with tarfile.open(fileobj=raw,mode='w'): pass
    image_archive(path,inputs[0],layer_bytes=[raw.getvalue()])
    assert verify_oci(path,inputs[0],'frank')['rootfs_diff_ids'] == inputs[0]['base']['rootfs_diff_ids']


def test_nonzero_hidden_oci_trailer_is_rejected(inputs):
    path=inputs[3]/'outer-trailer.tar';image_archive(path,inputs[0])
    with path.open('ab') as output: output.write(b'hidden'+b'\0'*506)
    with pytest.raises(ImageInputError,match='trailing OCI'): verify_oci(path,inputs[0],'frank')


def archive_files(path):
    with tarfile.open(path,'r:') as archive:
        return {m.name:archive.extractfile(m).read() for m in archive if m.isfile()}


def write_archive_files(path,files):
    with tarfile.open(path,'w') as archive:
        for name,raw in files.items():
            member=tarfile.TarInfo(name);member.size=len(raw);archive.addfile(member,io.BytesIO(raw))


@pytest.fixture
def local_base(inputs):
    policy,_,_,directory=inputs
    path=directory/'base-save.tar';image_archive(path,policy)
    inspect=bind_base_archive(path,policy)
    return policy,path,inspect


def bind_base_archive(path,policy):
    files=archive_files(path);index=json.loads(files['index.json']);descriptor=index['manifests'][0]
    manifest=json.loads(files['blobs/sha256/'+descriptor['digest'][7:]])
    config=json.loads(files['blobs/sha256/'+manifest['config']['digest'][7:]])
    files['manifest.json']=canonical([{'Config':'blobs/sha256/'+manifest['config']['digest'][7:],
        'Layers':['blobs/sha256/'+row['digest'][7:] for row in manifest['layers']],'RepoTags':None}])
    write_archive_files(path,files)
    policy['base'].update(manifest_digest=descriptor['digest'],config_digest=manifest['config']['digest'],
                          reference='docker.io/library/python@'+descriptor['digest'])
    inspect={'Id':descriptor['digest'],'Descriptor':descriptor,'RepoDigests':['python@'+descriptor['digest']],
             'Architecture':'arm64','Os':'linux','RootFS':{'Type':'layers','Layers':config['rootfs']['diff_ids']},
             'Config':config['config']}
    return inspect


class ReachedFirstBuild(RuntimeError):
    pass


def base_build_replay(inputs,local_base,*,inspected=None,save_returncode=0,calls=None):
    policy,archive,actual=local_base
    prepare(inputs);root=inputs[3]/'prepared';digest=sha((root/'contexts.json').read_bytes())
    calls=[] if calls is None else calls
    def docker(command,**kwargs):
        calls.append(command)
        if command[1:3]==['image','inspect']:
            return subprocess.CompletedProcess(command,0,canonical([actual if inspected is None else inspected]),b'')
        if command[1:3]==['image','save']:
            target=Path(command[command.index('--output')+1]);target.write_bytes(archive.read_bytes())
            if save_returncode:raise subprocess.CalledProcessError(save_returncode,command)
            return subprocess.CompletedProcess(command,0,b'',b'')
        if command[1:3]==['buildx','build']:raise ReachedFirstBuild()
        if command[1]=='version' or command[1:3]==['buildx','version']:
            return subprocess.CompletedProcess(command,0,b'fixture-tool-version',b'')
        raise AssertionError(command)
    with docker_replay(docker):
        build_images(root,digest,inputs[3]/'images')
    return calls


@pytest.mark.parametrize('mode',['containerd','classic'])
def test_public_build_accepts_exact_local_base_store_modes(inputs,local_base,mode):
    inspected=json.loads(canonical(local_base[2]))
    if mode=='classic':
        inspected['Id']=local_base[0]['base']['config_digest'];inspected.pop('Descriptor')
    with pytest.raises(ReachedFirstBuild):base_build_replay(inputs,local_base,inspected=inspected)
    output=inputs[3]/'images'
    if mode=='containerd':
        assert (output/'base.local-save.tar').read_bytes()==local_base[1].read_bytes()
        receipt=json.loads((output/'base.verification.json').read_bytes())
        assert receipt['raw_local_export_verified'] is True
        assert receipt['manifest_digest']==local_base[0]['base']['manifest_digest']
    else:
        assert json.loads((output/'base.verification.json').read_bytes())['mode']=='classic-config-id'


@pytest.mark.parametrize('change',['id','descriptor','descriptor-size','platform','rootfs','repository','missing-descriptor',
    'missing-blob','tampered-config','sidecar-config','sidecar-layers','sidecar-tag','extra-file','partial-save','save-error'])
def test_public_base_failures_precede_every_build_load_and_run(inputs,local_base,change):
    policy,path,inspected=local_base
    inspected=json.loads(canonical(inspected));files=archive_files(path)
    if change=='id':inspected['Id']='sha256:'+'0'*64
    elif change=='descriptor':inspected['Descriptor']['digest']='sha256:'+'0'*64
    elif change=='descriptor-size':inspected['Descriptor']['size']+=1
    elif change=='platform':inspected['Architecture']='amd64'
    elif change=='rootfs':inspected['RootFS']['Layers']=[]
    elif change=='repository':inspected['RepoDigests']=['unadmitted@'+policy['base']['manifest_digest']]
    elif change=='missing-descriptor':inspected.pop('Descriptor')
    elif change=='missing-blob':
        manifest=json.loads(files['blobs/sha256/'+policy['base']['manifest_digest'][7:]])
        del files['blobs/sha256/'+manifest['layers'][0]['digest'][7:]]
    elif change=='tampered-config':files['blobs/sha256/'+policy['base']['config_digest'][7:]]+=b'changed'
    elif change.startswith('sidecar-'):
        sidecar=json.loads(files['manifest.json'])
        if change=='sidecar-config':sidecar[0]['Config']='wrong-config'
        elif change=='sidecar-layers':sidecar[0]['Layers'].reverse()
        else:sidecar[0]['RepoTags']=['unadmitted:latest']
        files['manifest.json']=canonical(sidecar)
    elif change=='extra-file':files['unadmitted']=b'fixture'
    write_archive_files(path,files)
    if change=='partial-save':path.write_bytes(path.read_bytes()[:700])
    calls=[]
    with pytest.raises((ImageInputError,subprocess.CalledProcessError)):
        base_build_replay(inputs,local_base,inspected=inspected,save_returncode=1 if change=='save-error' else 0,calls=calls)
    assert not any(c[1:3] in (['buildx','build'],['load','--input']) or c[1]=='run' for c in calls)
    assert not (inputs[3]/'images/image-manifest.json').exists()
    assert not (inputs[3]/'images/base.verification.json').exists()


@pytest.mark.parametrize('change',['platform','rootfs','manifest-pin','config-pin'])
def test_local_raw_base_semantics_and_pins_are_required(inputs,change):
    policy=inputs[0];path=inputs[3]/'local-base.tar'
    def mutate(config):
        if change=='platform':config['architecture']='amd64'
        elif change=='rootfs':config['rootfs']['diff_ids'].reverse()
    image_archive(path,policy,mutate=mutate);bind_base_archive(path,policy)
    if change=='manifest-pin':policy['base']['manifest_digest']='sha256:'+'0'*64
    elif change=='config-pin':policy['base']['config_digest']='sha256:'+'0'*64
    with pytest.raises(ImageInputError):verify_base_export(path,policy['base'])


@pytest.mark.parametrize('mode',['containerd','classic'])
def test_loaded_component_selects_only_raw_archive_bound_local_digest(inputs,mode):
    policy=inputs[0];path=inputs[3]/'component.tar';image_archive(path,policy)
    files=archive_files(path);desc=json.loads(files['index.json'])['manifests'][0]
    manifest=json.loads(files['blobs/sha256/'+desc['digest'][7:]])
    raw=json.loads(files['blobs/sha256/'+manifest['config']['digest'][7:]])
    inspected={'Id':desc['digest'],'Descriptor':desc,'Architecture':'arm64','Os':'linux',
        'RootFS':{'Type':'layers','Layers':raw['rootfs']['diff_ids']},'Config':raw['config']}
    if mode=='classic':inspected['Id']=manifest['config']['digest'];inspected.pop('Descriptor')
    receipt=verify_oci(path,policy,'frank');output=inputs[3]/'selection';output.mkdir();calls=[]
    def inspect(command,**kwargs):
        calls.append(command)
        if mode=='classic' and command[-1]==receipt['manifest_digest']:
            return subprocess.CompletedProcess(command,1,b'[]',b'local image absent')
        return subprocess.CompletedProcess(command,0,canonical([inspected]),b'')
    with patch('scripts.process_component_images.subprocess.run',side_effect=inspect):
        selected=select_loaded_image(path,policy,'frank',receipt,output,'docker')
    assert selected['local_image_id']==inspected['Id'] and selected['raw_archive_reverified']
    assert all(c[1:3]==['image','inspect'] and c[-1] in (receipt['manifest_digest'],receipt['config_digest']) for c in calls)


@pytest.mark.parametrize('change',['tag','manifest','config','descriptor-size','platform','rootfs','entrypoint','labels','env',
    'missing','ambiguous','receipt-manifest','receipt-config','receipt-tag','archive-missing-blob'])
def test_loaded_component_mismatch_never_runs_or_pulls(inputs,change):
    policy=inputs[0];path=inputs[3]/'component.tar';image_archive(path,policy)
    files=archive_files(path);desc=json.loads(files['index.json'])['manifests'][0]
    manifest=json.loads(files['blobs/sha256/'+desc['digest'][7:]])
    raw=json.loads(files['blobs/sha256/'+manifest['config']['digest'][7:]])
    inspected={'Id':desc['digest'],'Descriptor':desc,'Architecture':'arm64','Os':'linux',
        'RootFS':{'Type':'layers','Layers':raw['rootfs']['diff_ids']},'Config':raw['config']}
    receipt=verify_oci(path,policy,'frank')
    if change=='tag':inspected['Id']='unadmitted:latest'
    elif change=='manifest':inspected['Descriptor']['digest']='sha256:'+'0'*64
    elif change=='config':inspected['Id']='sha256:'+'0'*64
    elif change=='descriptor-size':inspected['Descriptor']['size']+=1
    elif change=='platform':inspected['Os']='windows'
    elif change=='rootfs':inspected['RootFS']['Layers']=[]
    elif change=='entrypoint':inspected['Config']['Entrypoint']=['unadmitted']
    elif change=='labels':inspected['Config']['Labels']['io.hub.production-qualified']='true'
    elif change=='env':inspected['Config']['Env']=['UNADMITTED=fixture']
    elif change=='receipt-manifest':receipt['manifest_digest']='sha256:'+'0'*64
    elif change=='receipt-config':receipt['config_digest']='sha256:'+'0'*64
    elif change=='receipt-tag':receipt['tag']='unadmitted:latest'
    elif change=='archive-missing-blob':
        del files['blobs/sha256/'+manifest['layers'][0]['digest'][7:]];write_archive_files(path,files)
    output=inputs[3]/'selection';output.mkdir();calls=[]
    def inspect(command,**kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command,1 if change=='missing' else 0,
            b'[]' if change=='missing' else canonical([inspected,inspected] if change=='ambiguous' else [inspected]),b'')
    with patch('scripts.process_component_images.subprocess.run',side_effect=inspect):
        with pytest.raises(ImageInputError):select_loaded_image(path,policy,'frank',receipt,output,'docker')
    assert all(c[1:3]==['image','inspect'] for c in calls)
    assert not (output/'frank.local-image.json').exists()


def test_component_archive_does_not_accept_docker_save_sidecar(inputs,local_base):
    with pytest.raises(ImageInputError,match='Unreferenced'):
        verify_oci(local_base[1],inputs[0],'frank')


@pytest.mark.parametrize('mode,failure',[('containerd',None),('classic',None),
    ('containerd','tag'),('containerd','config-summary'),('containerd','absent'),
    ('containerd','cleanup-unknown')])
def test_public_build_flow_uses_only_validated_local_image_and_never_pulls(inputs,local_base,mode,failure):
    # The Docker responses here are synthetic CLI contract fixtures. Actual
    # component builds and owner smoke require a separate engine receipt.
    policy,base_archive,base_inspect=local_base
    if mode=='classic':base_inspect['Id']=policy['base']['config_digest'];base_inspect.pop('Descriptor')
    prepare(inputs);root=inputs[3]/'prepared';digest=sha((root/'contexts.json').read_bytes())
    output=inputs[3]/'images';calls=[];loaded={}
    def docker(command,**kwargs):
        calls.append(command)
        if command[1:3]==['image','inspect']:
            if command[-1]==policy['base']['reference']:body=base_inspect
            elif command[-1] in loaded:body=json.loads(canonical(loaded[command[-1]]))
            else:return subprocess.CompletedProcess(command,1,b'[]',b'local image absent')
            if command[-1]!=policy['base']['reference']:
                if failure=='absent':return subprocess.CompletedProcess(command,1,b'[]',b'local image absent')
                if failure=='tag':body['Id']='unadmitted:latest'
                elif failure=='config-summary':body['Config']['Entrypoint']=['unadmitted']
            return subprocess.CompletedProcess(command,0,canonical([body]),b'')
        if command[1:3]==['image','save']:
            Path(command[command.index('--output')+1]).write_bytes(base_archive.read_bytes())
        elif command[1:3]==['buildx','build']:
            destination=next(p[5:] for p in command[command.index('--output')+1].split(',') if p.startswith('dest='))
            image_archive(Path(destination),policy,owner=Path(command[-1]).name)
        elif command[1]=='load':
            files=archive_files(Path(command[-1]));desc=json.loads(files['index.json'])['manifests'][0]
            manifest=json.loads(files['blobs/sha256/'+desc['digest'][7:]])
            raw=json.loads(files['blobs/sha256/'+manifest['config']['digest'][7:]])
            row={'Id':desc['digest'],'Descriptor':desc,'Architecture':'arm64','Os':'linux',
                 'RootFS':{'Type':'layers','Layers':raw['rootfs']['diff_ids']},'Config':raw['config']}
            if mode=='classic':row['Id']=manifest['config']['digest'];row.pop('Descriptor')
            loaded[row['Id']]=row
        elif command[1]=='run':
            assert '--pull=never' in command and '--network=none' in command and '--read-only' in command
            assert '--cap-drop=ALL' in command and '--security-opt=no-new-privileges' in command
            image=command[command.index('--entrypoint')+2]
            assert image in loaded and image.startswith('sha256:')
            if '/opt/image-tools/smoke.py' in command:
                return subprocess.CompletedProcess(command,0,canonical({'component':command[-1],
                    'production_default_deny':True,'external_sends':0,'production_qualified':False}),b'')
            return subprocess.CompletedProcess(command,0,b'{"synthetic_inventory_fixture":true}',b'')
        elif not (command[1]=='version' or command[1:3]==['buildx','version']):raise AssertionError(command)
        return subprocess.CompletedProcess(command,0,b'synthetic-tool-version',b'')
    from scripts.process_image_execution import ExecutionFailure
    with docker_replay(docker, finish_error=failure=='cleanup-unknown'):
        if failure:
            expected_error = ExecutionFailure if failure=='cleanup-unknown' else ImageInputError
            with pytest.raises(expected_error):build_images(root,digest,output)
        else:
            result=build_images(root,digest,output)
            assert set(result['images'])=={'frank','process-runtime','service-management'}
            assert all(row['local_image_selection']['mode']==('classic-config-id' if mode=='classic' else 'containerd-manifest-id')
                       for row in result['images'].values())
    if failure:
        if failure=='cleanup-unknown':
            assert len([c for c in calls if c[1]=='run'])==6
        else:
            assert not any(c[1]=='run' for c in calls)
        assert not (output/'image-manifest.json').exists()
    else:
        assert len([c for c in calls if c[1]=='run'])==6
        assert (output/'image-manifest.json').exists()
