from copy import deepcopy
import json

import pytest

from my_legend_production.core import PROJECT,parse_json
from my_legend_production.storage import ArtifactStore
from my_legend_production.adapters import AdapterService


@pytest.fixture
def factory(tmp_path):
    store=ArtifactStore(tmp_path/'store',PROJECT/'examples')
    service=AdapterService(store)
    def request(kind='audio',edit_context=None,edit_source=None):
        req=parse_json((PROJECT/'examples'/f'{kind}.request.json').read_bytes())
        if edit_context:
            ctx=store.json(req['context']);edit_context(ctx);req['context']=store.put_json(ctx)
        if edit_source:
            source=store.json(req['source']['delivery']);edit_source(source);req['source']['delivery']=store.put_json(source)
        return req
    return store,service,request


@pytest.fixture
def prepared_pair(factory):
    store,service,request=factory
    results={k:service.prepare(request(k)) for k in ('audio','visual')}
    return store,results
