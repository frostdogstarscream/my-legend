from copy import deepcopy
from hashlib import sha256
import json

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from my_legend_production.production.life_contracts import canonical_hash
from my_legend_production.production.repository import ProductionError
from my_legend_production.production.theme_library import library_asset, library_catalog, library_theme, register_theme_library_routes


def build(root, name='LOFT'):
    image = b'fixture original image bytes'
    h = sha256(image).hexdigest()
    (root/'content').mkdir(parents=True, exist_ok=True)
    (root/'content'/(h+'.png')).write_bytes(image)
    package = {'schema_version': 'legend_theme_package/1', 'theme_id': 'theme_000006', 'name': name,
               'asset_ids': ['sofa'], 'auxiliary_asset_ids': [], 'items': []}
    package['package_sha256'] = canonical_hash(package)
    c = {'schema_version': 'legend_theme_library/1', 'themes': [package], 'assets': {'sofa': {'sha256': h}},
         'files': {h: {'size_bytes': len(image)}}, 'groups': {'loft': ['sofa']}}
    c['catalog_sha256'] = canonical_hash(c)
    folder = root/'catalogs'/c['catalog_sha256']
    (folder/'themes/theme_000006').mkdir(parents=True)
    (folder/'catalog.json').write_text(json.dumps(c), encoding='utf-8')
    (folder/'themes/theme_000006/manifest.json').write_text(json.dumps(package), encoding='utf-8')
    (root/'current.json').write_text(json.dumps({'catalog_sha256': c['catalog_sha256']}))
    return c, h, image


def test_version_pin_survives_pointer_change_and_returns_source_bytes(tmp_path):
    first, image_hash, raw = build(tmp_path)
    second, _, _ = build(tmp_path, 'Next revision')
    assert library_catalog(tmp_path)['catalog_sha256'] == second['catalog_sha256']
    restored = library_theme(tmp_path, 'theme_000006', first['catalog_sha256'])
    assert restored['manifest']['name'] == 'LOFT'
    assert library_asset(tmp_path, image_hash, first['catalog_sha256']).read_bytes() == raw
    assert restored['assets'] == first['assets']


@pytest.mark.parametrize('kind', ['catalog', 'manifest', 'image', 'unknown_image', 'unknown_theme', 'path_digest', 'path_theme'])
def test_corruption_and_unregistered_paths_are_rejected(tmp_path, kind):
    c, h, _ = build(tmp_path)
    folder = tmp_path/'catalogs'/c['catalog_sha256']
    if kind == 'catalog':
        changed = deepcopy(c);changed['themes'][0]['name'] = 'tamper'
        (folder/'catalog.json').write_text(json.dumps(changed))
    if kind == 'manifest': (folder/'themes/theme_000006/manifest.json').write_text('{}')
    if kind == 'image': (tmp_path/'content'/(h+'.png')).write_bytes(b'bad')
    with pytest.raises(ProductionError):
        if kind in ('catalog', 'manifest'): library_theme(tmp_path, 'theme_000006')
        elif kind in ('image', 'unknown_image'): library_asset(tmp_path, h if kind == 'image' else 'f'*64)
        elif kind == 'path_digest': library_catalog(tmp_path, '../escape')
        else: library_theme(tmp_path, 'theme_999999' if kind == 'unknown_theme' else '../escape')


def test_all_api_reads_require_auth_and_unknown_assets_return_404(tmp_path):
    c, h, raw = build(tmp_path/'theme-library')
    def owner(request):
        if request.headers.get('authorization') != 'test-owner': raise HTTPException(401)
    app = FastAPI();router = APIRouter(prefix='/api/v1')
    register_theme_library_routes(router, owner, lambda: tmp_path);app.include_router(router)
    client = TestClient(app);headers = {'authorization': 'test-owner'}
    prefix = '/api/v1/stage-preview/theme-library'
    for path in (prefix, prefix+'/themes/theme_000006', prefix+'/assets/'+h):
        assert client.get(path).status_code == 401
        assert client.get(path, headers=headers).status_code == 200
    summary = client.get(prefix, headers=headers).json()['data']
    assert 'assets' not in summary and 'files' not in summary
    assert client.get(prefix+'/assets/'+h, headers=headers).content == raw
    assert client.get(prefix+'/themes/theme_999999', headers=headers).status_code == 404
    assert client.get(prefix+'/assets/'+'f'*64, headers=headers).status_code == 404
