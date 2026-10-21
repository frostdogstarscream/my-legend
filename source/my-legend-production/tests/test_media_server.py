from http.client import HTTPConnection
import threading

import pytest

from my_legend_production.server import make_server


@pytest.fixture
def server(tmp_path):
    (tmp_path/'index.html').write_text('preview',encoding='utf-8')
    (tmp_path/'test.wav').write_bytes(bytes(range(100)))
    http=make_server(tmp_path,0)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    yield http
    http.shutdown();http.server_close();thread.join(timeout=2)


@pytest.mark.parametrize('range_value,expected,start,end',[
 ('bytes=10-19',206,10,20),('bytes=90-',206,90,100),('bytes=-5',206,95,100),('bytes=200-',416,0,0)])
def test_http_byte_ranges(server,range_value,expected,start,end):
    c=HTTPConnection(*server.server_address,timeout=5);c.request('GET','/test.wav',headers={'Range':range_value})
    r=c.getresponse();data=r.read()
    assert r.status==expected
    if expected==206:
        assert data==bytes(range(100))[start:end]
        assert r.getheader('Content-Range')==f'bytes {start}-{end-1}/100'
    c.close()


def test_head_and_directory_protection(server):
    c=HTTPConnection(*server.server_address,timeout=5);c.request('HEAD','/test.wav')
    r=c.getresponse();assert r.status==200 and r.getheader('Content-Length')=='100' and r.read()==b''
    c.close()
