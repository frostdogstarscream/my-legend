"""Read-only, version-pinned access to the portable local static theme library."""
from hashlib import sha256
import json
import re

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse

from .life_contracts import canonical_hash
from .repository import ProductionError


def library_catalog(root, digest=None):
    try:
        digest = digest or json.loads((root/'current.json').read_text('utf-8'))['catalog_sha256']
        if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('Invalid catalog digest')
        value = json.loads((root/'catalogs'/digest/'catalog.json').read_text('utf-8'))
        if value.get('schema_version') != 'legend_theme_library/1' or value.get('catalog_sha256') != digest:
            raise ValueError('Invalid schema')
        if canonical_hash({k: v for k, v in value.items() if k != 'catalog_sha256'}) != digest:
            raise ValueError('Catalog hash mismatch')
        return value
    except (OSError, ValueError, KeyError, TypeError):
        raise ProductionError('THEME_LIBRARY_INVALID_OR_NOT_CONFIGURED') from None


def library_theme(root, theme_id, digest=None):
    catalog = library_catalog(root, digest)
    entry = next((t for t in catalog['themes'] if t['theme_id'] == theme_id), None)
    if not re.fullmatch(r'theme_[0-9]{6}', theme_id) or entry is None:
        raise ProductionError('THEME_NOT_FOUND')
    try:
        value = json.loads((root/'catalogs'/catalog['catalog_sha256']/'themes'/theme_id/'manifest.json').read_text('utf-8'))
        if (value.get('schema_version') != 'legend_theme_package/1' or value.get('theme_id') != theme_id
                or value.get('package_sha256') != entry['package_sha256']
                or canonical_hash({k: v for k, v in value.items() if k != 'package_sha256'}) != entry['package_sha256']):
            raise ValueError('Theme manifest hash mismatch')
        return {'catalog_sha256': catalog['catalog_sha256'], 'manifest': value,
                'assets': {k: catalog['assets'][k] for k in value['asset_ids']+value['auxiliary_asset_ids']}}
    except (OSError, ValueError, KeyError, TypeError):
        raise ProductionError('THEME_PACKAGE_INVALID') from None


def library_asset(root, digest, catalog_digest=None):
    catalog = library_catalog(root, catalog_digest)
    if not re.fullmatch('[a-f0-9]{64}', digest) or digest not in catalog['files']:
        raise ProductionError('THEME_ASSET_NOT_FOUND')
    path = root/'content'/(digest+'.png')
    try:
        data = path.read_bytes()
        if len(data) != catalog['files'][digest]['size_bytes'] or sha256(data).hexdigest() != digest:
            raise ValueError('Asset hash mismatch')
    except (OSError, ValueError):
        raise ProductionError('THEME_ASSET_HASH_MISMATCH') from None
    return path


def register_theme_library_routes(router, owner, local_root):
    def response(operation):
        try:
            return {'code': 0, 'message': 'ok', 'data': operation()}
        except ProductionError as error:
            raise HTTPException(404, error.code) from None

    @router.get('/stage-preview/theme-library')
    def catalog(request: Request, catalog_sha256: str | None = None):
        owner(request)
        return response(lambda: {k: v for k, v in library_catalog(local_root()/'theme-library', catalog_sha256).items()
                                 if k not in ('assets', 'files', 'groups')})

    @router.get('/stage-preview/theme-library/themes/{theme_id}')
    def theme(theme_id: str, request: Request, catalog_sha256: str | None = None):
        owner(request)
        return response(lambda: library_theme(local_root()/'theme-library', theme_id, catalog_sha256))

    @router.get('/stage-preview/theme-library/assets/{digest}')
    def asset(digest: str, request: Request, catalog_sha256: str | None = None):
        owner(request)
        try:
            path = library_asset(local_root()/'theme-library', digest, catalog_sha256)
        except ProductionError as error:
            raise HTTPException(404, error.code) from None
        return FileResponse(path, media_type='image/png', headers={'Cache-Control': 'private, no-store'})
