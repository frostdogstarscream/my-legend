from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import re


class MediaHandler(SimpleHTTPRequestHandler):
    """Loopback preview with byte ranges for seekable WAV/MP4; no directory listing."""
    def log_message(self,*_):pass
    def list_directory(self,path):self.send_error(403);return None

    def send_head(self):
        self.remaining=None
        path=Path(self.translate_path(self.path)).resolve()
        root=Path(self.directory).resolve()
        if not path.is_relative_to(root):self.send_error(403);return None
        if path.is_dir():path=path/'index.html'
        if not path.is_file():self.send_error(404);return None
        size=path.stat().st_size;left=0;right=size-1
        range_header=self.headers.get('Range')
        if range_header:
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',range_header.strip())
            if not match or not any(match.groups()):self.send_error(416);return None
            a,b=match.groups()
            if not a:left=max(0,size-int(b))
            else:left=int(a)
            if b and a:right=min(size-1,int(b))
            if left>=size or left>right:
                self.send_response(416);self.send_header('Content-Range',f'bytes */{size}');self.send_header('Content-Length','0');self.end_headers();return None
        stream=path.open('rb');stream.seek(left);self.remaining=right-left+1
        self.send_response(206 if range_header else 200)
        self.send_header('Content-Type',self.guess_type(str(path)))
        self.send_header('Content-Length',str(self.remaining))
        self.send_header('Accept-Ranges','bytes')
        self.send_header('X-Content-Type-Options','nosniff')
        if range_header:self.send_header('Content-Range',f'bytes {left}-{right}/{size}')
        self.end_headers();return stream

    def copyfile(self,source,outputfile):
        while self.remaining:
            data=source.read(min(64*1024,self.remaining))
            if not data:break
            outputfile.write(data);self.remaining-=len(data)


def make_server(root,port):
    return ThreadingHTTPServer(('127.0.0.1',port),partial(MediaHandler,directory=str(Path(root).resolve())))
