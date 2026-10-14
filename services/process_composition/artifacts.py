"""Exact installed artifact tuple verification for Hub composition drills."""
import hashlib
import importlib.metadata as metadata
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

from . import CompositionError, canonical

MODULES={'process-runtime':'process_runtime','service-management':'service_management','frank':'frank_executor'}
PACKAGES={'process-runtime':'process-runtime','service-management':'service-management','frank':'frank-executor'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dependency_inventory():
    prefix=Path(sys.prefix).resolve()
    inventory={}
    for dist in metadata.distributions():
        name=dist.metadata['Name']
        if name in inventory:
            raise CompositionError('Duplicate installed distribution identity')
        files={}
        for item in dist.files or []:
            if str(item).endswith(('.pyc','/RECORD')):
                continue
            path=Path(dist.locate_file(item)).resolve()
            if not path.is_relative_to(prefix) or not path.is_file():
                raise CompositionError('Installed dependency escapes the isolated prefix')
            files[str(path.relative_to(prefix))]=sha(path)
        inventory[name]={'version':dist.version,'files':files}
    return inventory


def verify_wheel(component, entry, *, manifest_directory):
    if (set(entry)!={'package','version','wheel','wheel_sha256'} or entry['package']!=PACKAGES[component]
            or not isinstance(entry['version'],str) or not entry['version']):
        raise CompositionError('Unsupported artifact identity')
    path=Path(entry['wheel'])
    if not path.is_absolute(): path=manifest_directory/path
    if not path.is_file() or sha(path)!=entry['wheel_sha256']:
        raise CompositionError('Wheel digest mismatch for '+component)
    dist=metadata.distribution(entry['package'])
    if dist.version!=entry['version']:
        raise CompositionError('Installed package version mismatch for '+component)
    prefix=Path(sys.prefix).resolve()
    count=0
    packages=set()
    with zipfile.ZipFile(path) as wheel:
        for member in wheel.namelist():
            if member.endswith('/') or member.endswith('/RECORD'):
                continue
            parts=Path(member).parts
            if '..' in parts or member.startswith('/'):
                raise CompositionError('Unsafe wheel member')
            if '.data/data/' in member:
                target=prefix/member.split('.data/data/',1)[1]
            elif '.data/' in member:
                raise CompositionError('Unsupported wheel installation layout')
            else:
                target=Path(dist.locate_file(member)).resolve()
            if not target.is_relative_to(prefix) or not target.is_file() or target.read_bytes()!=wheel.read(member):
                raise CompositionError('Installed wheel bytes mismatch for '+component+' / '+member)
            if len(parts)==2 and parts[1]=='__init__.py':
                packages.add(parts[0])
            count+=1
    for package in sorted(packages):
        spec=importlib.util.find_spec(package)
        expected_package=Path(dist.locate_file(package+'/__init__.py')).resolve()
        if not spec or not spec.origin or Path(spec.origin).resolve()!=expected_package:
            raise CompositionError('Component package import uses a source checkout: '+package)
    spec=importlib.util.find_spec(MODULES[component])
    expected=Path(dist.locate_file(MODULES[component]+'/__init__.py')).resolve()
    if not spec or not spec.origin or Path(spec.origin).resolve()!=expected:
        raise CompositionError('Component import uses a source checkout: '+component)
    return {'package':dist.metadata['Name'],'version':dist.version,'wheel_sha256':entry['wheel_sha256'],
            'installed_files_verified':count,'packages_verified':sorted(packages),'module_origin':str(expected),'isolated_prefix':str(prefix)}


def create_manifest(wheels, *, native_helper=None):
    if set(wheels)!=set(MODULES):
        raise CompositionError('Every component wheel must be supplied')
    components={}
    for component,path in wheels.items():
        path=Path(path).resolve()
        dist=metadata.distribution(PACKAGES[component])
        entry={'package':PACKAGES[component],'version':dist.version,'wheel':str(path),'wheel_sha256':sha(path)}
        verify_wheel(component,entry,manifest_directory=path.parent)
        components[component]=entry
    native=None
    if native_helper:
        path=Path(native_helper).resolve()
        native={'path':str(path),'sha256':sha(path),'scope':'verification-only artifact pin; installed authority chain unqualified'}
    dependencies=dependency_inventory()
    return {'schema':'hub.process-artifact-tuple.v1','python_version':sys.version.split()[0],
            'components':components,'native_helper':native,'dependencies':dependencies,
            'dependency_inventory_sha256':hashlib.sha256(canonical(dependencies)).hexdigest(),
            'production_ready':False}


def verify_manifest(path):
    path=Path(path).resolve();value=json.loads(path.read_text())
    fields={'schema','python_version','components','native_helper','dependencies','dependency_inventory_sha256','production_ready'}
    if (type(value) is not dict or set(value)!=fields or value['schema']!='hub.process-artifact-tuple.v1'
            or value['production_ready'] is not False or set(value['components'])!=set(MODULES)
            or value['python_version']!=sys.version.split()[0]):
        raise CompositionError('Unsupported installed artifact tuple')
    pins={component:verify_wheel(component,entry,manifest_directory=path.parent) for component,entry in value['components'].items()}
    current=dependency_inventory()
    if current!=value['dependencies'] or hashlib.sha256(canonical(current)).hexdigest()!=value['dependency_inventory_sha256']:
        raise CompositionError('Installed dependency content changed')
    native=value['native_helper']
    if native is not None:
        if type(native) is not dict or set(native)!={'path','sha256','scope'} or sha(native['path'])!=native['sha256']:
            raise CompositionError('Native verification artifact changed')
    return {'schema':value['schema'],'manifest_sha256':sha(path),'components':pins,
            'dependency_inventory_sha256':value['dependency_inventory_sha256'],
            'native_helper':native,'production_ready':False}
