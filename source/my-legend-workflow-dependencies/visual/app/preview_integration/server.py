"""整合预览包的本机静态服务：支持 MP4 Range 请求，不公开项目或同事源码。"""
import argparse
import re
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

class Handler(SimpleHTTPRequestHandler):
    def send_head(self):
        self.remaining=None
        value=self.headers.get('Range');path=Path(self.translate_path(self.path))
        if not value or not path.is_file():return super().send_head()
        match=re.fullmatch(r'bytes=(\d*)-(\d*)',value)
        size=path.stat().st_size
        if not match or size<=0:self.send_error(416);return None
        left,right=match.groups()
        if left:start=int(left);end=min(int(right) if right else size-1,size-1)
        else:start=max(0,size-int(right or 0));end=size-1
        if not 0<=start<=end<size:
            self.send_response(416);self.send_header('Content-Range',f'bytes */{size}');self.end_headers();return None
        file=path.open('rb');file.seek(start);self.remaining=end-start+1
        self.send_response(206);self.send_header('Content-Type',self.guess_type(str(path)))
        self.send_header('Content-Range',f'bytes {start}-{end}/{size}');self.send_header('Content-Length',str(self.remaining));self.end_headers()
        return file
    def end_headers(self):
        self.send_header('Accept-Ranges','bytes');super().end_headers()
    def copyfile(self,source,output):
        if self.remaining is None:return super().copyfile(source,output)
        remaining=self.remaining;self.remaining=None
        while remaining:
            data=source.read(min(65536,remaining))
            if not data:break
            output.write(data);remaining-=len(data)
    def log_message(self,*unused):pass

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--port',type=int,default=8099)
    args=parser.parse_args()
    if not (args.root/'preview.json').is_file():parser.error('请只指定已经生成的手机预览目录')
    server=ThreadingHTTPServer(('127.0.0.1',args.port),partial(Handler,directory=str(args.root.resolve())))
    print(f'http://127.0.0.1:{args.port}/index.html',flush=True);server.serve_forever()
