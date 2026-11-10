"""Optional LAN transport. No dependency on the editor or Qt.

TLS is pinned to the certificate fingerprint distributed with the random share key.
A single expiring lease plus revision CAS serializes writers. Immutable revisions
serve downloads while uploads stream to a staging file beside the original.
"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, urlencode
import base64
import hashlib
import re
import hmac
import http.client
import ipaddress
import json
import os
import secrets
import shutil
import socket
import ssl
import tempfile
import threading
import time
import uuid

MAX_PROJECT=528*1024*1024
CHUNK=1024*1024
LEASE_SECONDS=60
DEFAULT_PORT=48761
LINK_VERSION=1
FINGERPRINT_BYTES=16
SECRET_BYTES=24
_LINK_RE=re.compile(r'(?:fbcanvas:(?://)?)?(?:https?://)?(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})(?:/fb)?/?(?:#|/)([A-Za-z0-9_\-]{40,120})(?![A-Za-z0-9_\-])')
_LEGACY_RE=re.compile(r'https://\d{1,3}(?:\.\d{1,3}){3}:\d{1,5}/?#[A-Za-z0-9_\-=&%.,]+')

def _b64(data):return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')
def _unb64(text):return base64.urlsafe_b64decode(text+'='*(-len(text)%4))


def make_link(address,port,secret,fingerprint,extra=()):
    """One short link: opens a landing page in browsers/chat apps, and is parsed by the app.

    The token after '#' (browsers never send it to a server) packs:
    version | 24-byte access key | first 16 bytes of the TLS certificate SHA-256 | backup IPv4s.
    """
    raw=bytes([LINK_VERSION])+_unb64(secret)+bytes.fromhex(fingerprint)[:FINGERPRINT_BYTES]
    if len(raw)!=1+SECRET_BYTES+FINGERPRINT_BYTES:raise ShareError('访问密钥格式无效。')
    for ip in list(extra)[:7]:raw+=ipaddress.IPv4Address(ip).packed
    return f'http://{address}:{int(port)}/fb#{_b64(raw)}'


def find_link(text):
    """Extract a share link from pasted text (chat messages, browser address bar, fbcanvas: URL)."""
    if not isinstance(text,str) or len(text)>20000:return None
    match=_LINK_RE.search(text)
    if match:
        address,port,token=match.groups()
        try:
            raw=_unb64(token);ip=ipaddress.IPv4Address(address)
            if raw[0]!=LINK_VERSION or len(raw)<1+SECRET_BYTES+FINGERPRINT_BYTES or (len(raw)-1-SECRET_BYTES-FINGERPRINT_BYTES)%4:return None
            if not 0<int(port)<65536:return None
        except (ValueError,IndexError):return None
        return f'http://{ip}:{int(port)}/fb#{token}'
    match=_LEGACY_RE.search(text)
    return match.group(0) if match else None


def parse_link(code):
    """Returns (address, port, secret, fingerprint_hex, [extra ips]); accepts new and v0.6.3 formats."""
    link=find_link(code or '')
    if not link:raise ShareError('这不是有效的反馈画布共享链接，请完整复制同事发来的链接。')
    parsed=urlsplit(link)
    try:ip=ipaddress.ip_address(parsed.hostname or '');port=parsed.port
    except ValueError:raise ShareError('连接信息不是有效的局域网地址。')
    if ip.version!=4 or ip.is_unspecified or ip.is_multicast or not port:raise ShareError('链接中的地址无效。')
    if parsed.scheme=='http':
        raw=_unb64(parsed.fragment)
        secret=_b64(raw[1:1+SECRET_BYTES]);fingerprint=raw[1+SECRET_BYTES:1+SECRET_BYTES+FINGERPRINT_BYTES].hex()
        rest=raw[1+SECRET_BYTES+FINGERPRINT_BYTES:]
        extra=[str(ipaddress.IPv4Address(rest[i:i+4])) for i in range(0,len(rest),4)]
        return str(ip),port,secret,fingerprint,extra
    params=parse_qs(parsed.fragment)
    secret=params.get('key',[''])[0];fingerprint=params.get('fp',[''])[0].lower()
    if parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query:
        raise ShareError('请完整粘贴共享电脑提供的连接信息。')
    extra=[c for c in params.get('hosts',[''])[0].split(',')[:7] if c]
    return str(ip),port,secret,fingerprint,extra


LANDING_PAGE='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>反馈画布 · 局域网共享</title>
<style>body{margin:0;background:#202020;color:#dedede;font:15px/1.6 "Microsoft YaHei",system-ui,sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center}
main{max-width:520px;padding:32px;background:#2a2a2a;border:1px solid #494949;border-radius:14px;margin:16px}
h1{font-size:22px;margin:0 0 6px}p{color:#b8b8b8;margin:8px 0}a.btn,button{display:block;width:100%;box-sizing:border-box;text-align:center;margin:14px 0 0;padding:12px;border-radius:8px;border:1px solid #6a6a6a;background:#4b4b4b;color:#fff;font-size:16px;text-decoration:none;cursor:pointer}
button{background:#2f2f2f}small{color:#8c8c8c;display:block;margin-top:16px}#bad{color:#e7a76a;display:none}</style></head>
<body><main><h1>同事给你共享了一个反馈画布</h1>
<p>需要电脑上装有 <b>反馈画布 v0.6.4 或更新版本</b>。</p>
<a class="btn" id="open" href="#">用反馈画布打开</a>
<button id="copy">复制链接</button>
<p id="tip">按钮没反应时：点“复制链接”，切到反馈画布窗口按 <b>Ctrl+V</b> 即可打开。</p>
<p id="bad">链接不完整：请让同事重新复制完整链接（包含 # 后面的部分）。</p>
<small>此页面不含项目内容，内容只在反馈画布里经加密连接传输。</small></main>
<script>
var token=location.hash.replace('#','');var link=location.href;
if(token.length<40){document.getElementById('bad').style.display='block'}
document.getElementById('open').href='fbcanvas:'+location.host+'/'+token;
document.getElementById('copy').onclick=function(){
 function fallback(){var t=document.createElement('textarea');t.value=link;document.body.appendChild(t);t.select();try{document.execCommand('copy')}catch(e){}document.body.removeChild(t)}
 if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(link).catch(fallback)}else{fallback()}
 this.textContent='已复制，到反馈画布里按 Ctrl+V';};
</script></body></html>'''


class ShareError(Exception):
    def __init__(self,message,status=400):
        super().__init__(message);self.status=status


def file_hash(path):
    digest=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(CHUNK),b''):digest.update(block)
    return digest.hexdigest()


def atomic_json(path,value):
    temp=path.with_name(path.name+'.tmp')
    with open(temp,'w',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)


def make_certificate(directory):
    # Imported only when the user explicitly starts sharing.
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Feedback Studio LAN')])
    now=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5))
          .not_valid_after(now+timedelta(days=30)).add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
          .sign(key,hashes.SHA256()))
    certfile=directory/'certificate.pem';keyfile=directory/'private-key.pem'
    keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return certfile,keyfile,cert.fingerprint(hashes.SHA256()).hex()


class ShareState:
    def __init__(self,project,directory,validator,lease_seconds=LEASE_SECONDS):
        self.project=Path(project).resolve()
        if not self.project.is_file() or self.project.suffix.lower()!='.fbboard':raise ShareError('请选择已保存的 .fbboard 项目。')
        if not 0<self.project.stat().st_size<=MAX_PROJECT:raise ShareError('项目大小超出支持范围。')
        validator(str(self.project))
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.history=self.project.parent/'.反馈画布共享历史'/(self.project.stem+'-'+uuid.uuid4().hex[:10])
        self.history.mkdir(parents=True)
        self.identifier=uuid.uuid4().hex;self.secret=secrets.token_urlsafe(SECRET_BYTES)
        self.lock=threading.RLock();self.active=True;self.revision=0;self.lease=None
        self.lease_seconds=lease_seconds;self.validator=validator;self.uploading=None;self.receipts={}
        self.digest=file_hash(self.project)
        initial=self.history/'000000.fbboard';shutil.copyfile(self.project,initial)
        if file_hash(initial)!=self.digest or file_hash(self.project)!=self.digest:raise ShareError('文件在开启共享时发生变化，请保存后重试。',409)
        self.snapshot=initial;self.size=initial.stat().st_size
        self._write_history()

    def _write_history(self):
        atomic_json(self.history/'history.json',{'project':str(self.project),'revision':self.revision,'sha256':self.digest,'updated_at':time.time()})

    def _active(self):
        if not self.active:raise ShareError('共享已停止。请将未提交修改另存为本地文件。',410)

    def _expire(self):
        if self.lease and time.monotonic()>=self.lease['expires']:self.lease=None

    def _check_lease(self,token):
        self._active();self._expire()
        if not self.lease or not token or not hmac.compare_digest(token,self.lease['token']):
            raise ShareError('编辑权限已失效。未提交的修改请另存为本地副本。',423)

    def status(self):
        with self.lock:
            self._active();self._expire()
            return {'protocol':1,'session':self.identifier,'name':self.project.name,'revision':self.revision,
                    'sha256':self.digest,'size':self.size,'editor':self.lease['name'] if self.lease else '',
                    'lease_remaining':max(0,round(self.lease['expires']-time.monotonic())) if self.lease else 0}

    def acquire(self,client,name):
        if not isinstance(client,str) or not 16<=len(client)<=100:raise ShareError('无效的客户端标识。')
        if not isinstance(name,str) or not name.strip() or len(name)>80:raise ShareError('请输入不超过 80 字的编辑者名称。')
        with self.lock:
            self._active();self._expire()
            if self.uploading:raise ShareError('上一次提交尚在处理，请稍后重试。',409)
            if self.lease:raise ShareError('当前正在编辑：'+self.lease['name'],423)
            self.lease={'client':client,'name':name.strip(),'token':secrets.token_urlsafe(32),'expires':time.monotonic()+self.lease_seconds}
            return {**self.status(),'lease':self.lease['token']}

    def heartbeat(self,token):
        with self.lock:
            self._check_lease(token);self.lease['expires']=time.monotonic()+self.lease_seconds
            return self.status()

    def release(self,token):
        with self.lock:
            self._check_lease(token)
            if self.uploading:raise ShareError('正在保存，请完成后结束编辑。',409)
            self.lease=None;return self.status()

    def stop(self):
        with self.lock:self.active=False;self.lease=None

    @contextmanager
    def download(self):
        with self.lock:
            self._active();status=self.status();stream=open(self.snapshot,'rb')
        try:yield stream,status
        finally:stream.close()

    def begin_upload(self,lease,revision,request_id):
        if len(request_id)!=32 or any(c not in '0123456789abcdef' for c in request_id):raise ShareError('无效的提交标识。')
        with self.lock:
            self._check_lease(lease)
            if request_id in self.receipts:return self.receipts[request_id]
            if revision!=self.revision:raise ShareError('共享文件已有新版本，拒绝覆盖。请另存当前修改。',409)
            if self.uploading:raise ShareError('已有保存正在进行，请稍后再试。',409)
            self.uploading=request_id
            return None

    def commit_upload(self,temp,lease,revision,request_id,expected_hash):
        digest=file_hash(temp)
        if not hmac.compare_digest(digest,expected_hash):raise ShareError('传输校验失败，原文件未改变。')
        self.validator(str(temp))
        with self.lock:
            self._check_lease(lease)
            if self.uploading!=request_id or revision!=self.revision:raise ShareError('项目版本或编辑权限发生变化，原文件未改变。',409)
            if file_hash(self.project)!=self.digest:raise ShareError('共享源文件被其他程序改动，已拒绝覆盖。请停止共享并检查文件。',409)
            next_revision=self.revision+1
            snapshot=self.history/f'{next_revision:06d}.fbboard'
            shutil.copyfile(temp,snapshot)
            with open(snapshot,'r+b') as stream:stream.flush();os.fsync(stream.fileno())
            os.replace(temp,self.project)
            self.snapshot=snapshot;self.revision=next_revision;self.digest=digest;self.size=snapshot.stat().st_size
            self.lease['expires']=time.monotonic()+self.lease_seconds
            receipt=self.status();self.receipts[request_id]=receipt
            # A nonessential history-index cleanup failure cannot turn a committed save into a failure.
            try:
                self._write_history()
                for old in sorted(self.history.glob('*.fbboard'))[:-10]:old.unlink(missing_ok=True)
            except OSError:pass
            if len(self.receipts)>100:self.receipts.pop(next(iter(self.receipts)))
            return receipt


class _Server(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=False
    def __init__(self,*args,**kwargs):
        self.slots=threading.BoundedSemaphore(32)
        super().__init__(*args,**kwargs)
    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):request.close();return
        try:super().process_request(request,address)
        except Exception:self.slots.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()
    def handle_error(self,request,client_address):pass


class _Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    server_version='FeedbackLAN/1'
    sys_version=''
    def setup(self):
        self.request.settimeout(10)
        # One port serves both: TLS for the app, a plain landing page for browsers/chat apps.
        try:first=self.request.recv(1,socket.MSG_PEEK)
        except OSError:first=b''
        self.plain=first!=b'\x16'
        if not self.plain:self.request=self.server.tls.wrap_socket(self.request,server_side=True)
        self.request.settimeout(30)
        super().setup()
    def _landing(self):
        path=urlsplit(self.path).path
        if path not in ('/','/fb','/fb/'):
            self._json(404,{'error':'请用反馈画布打开共享链接。'});return
        data=LANDING_PAGE.encode('utf-8')
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('Connection','close');self.end_headers()
        if self.command!='HEAD':self.wfile.write(data)
        self.close_connection=True
    def do_HEAD(self):
        if getattr(self,'plain',False):self._landing()
        else:self._json(405,{'error':'不支持的请求。'})
    def finish(self):
        try:super().finish()
        finally:self.request.close()
    def log_message(self,*args):pass
    def handle_expect_100(self):
        self._json(417,{'error':'不支持此传输方式。'});return False
    def _json(self,status,value):
        data=json.dumps(value,ensure_ascii=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
        self.send_header('Connection','close');self.end_headers();self.wfile.write(data);self.close_connection=True
    def _authenticate(self):
        if self.headers.get('Origin'):raise ShareError('请使用桌面客户端连接。',403)
        if self.headers.get('Transfer-Encoding'):raise ShareError('不支持此传输方式。')
        value=self.headers.get('Authorization','')
        if not hmac.compare_digest(value,'Bearer '+self.server.state.secret):raise ShareError('连接信息无效或共享已重新开启。',401)
        self.server.state._active()
    def _body(self):
        try:size=int(self.headers.get('Content-Length','0'))
        except ValueError:raise ShareError('请求长度无效。')
        if not 0<size<=8192:raise ShareError('请求内容过大或为空。')
        data=json.loads(self.rfile.read(size))
        if not isinstance(data,dict):raise ShareError('无效的请求。')
        return data
    def _run(self,operation):
        try:self._authenticate();operation()
        except ShareError as exc:self._json(exc.status,{'error':str(exc)})
        except (BrokenPipeError,ConnectionResetError,ssl.SSLError,socket.timeout):self.close_connection=True
        except Exception:self._json(400,{'error':'请求失败，文件未提交；请检查项目或网络连接。'})
    def do_GET(self):
        if self.plain:
            self._landing();return
        def read():
            state=self.server.state
            if self.path=='/v1/status':self._json(200,state.status());return
            if self.path!='/v1/project':raise ShareError('接口不存在。',404)
            with state.download() as (stream,info):
                self.send_response(200);self.send_header('Content-Type','application/octet-stream')
                self.send_header('Content-Length',str(info['size']));self.send_header('X-Revision',str(info['revision']))
                self.send_header('X-SHA256',info['sha256']);self.send_header('Cache-Control','no-store');self.send_header('Connection','close');self.end_headers()
                while True:
                    state._active();data=stream.read(CHUNK)
                    if not data:break
                    self.wfile.write(data)
            self.close_connection=True
        self._run(read)
    def do_POST(self):
        if self.plain:
            self._json(403,{'error':'请用反馈画布打开共享链接。'});return
        def post():
            data=self._body();state=self.server.state;lease=self.headers.get('X-Edit-Lease','')
            if self.path=='/v1/lease':result=state.acquire(data.get('client_id'),data.get('name'))
            elif self.path=='/v1/heartbeat':result=state.heartbeat(lease)
            elif self.path=='/v1/release':result=state.release(lease)
            else:raise ShareError('接口不存在。',404)
            self._json(200,result)
        self._run(post)
    def do_PUT(self):
        if self.plain:
            self._json(403,{'error':'请用反馈画布打开共享链接。'});return
        def upload():
            if self.path!='/v1/project':raise ShareError('接口不存在。',404)
            try:size=int(self.headers.get('Content-Length','0'));revision=int(self.headers.get('If-Match','-1'))
            except ValueError:raise ShareError('项目长度或版本无效。')
            digest=self.headers.get('X-SHA256','');lease=self.headers.get('X-Edit-Lease','');request_id=self.headers.get('X-Upload-ID','')
            if not 0<size<=MAX_PROJECT or len(digest)!=64:raise ShareError('项目大小或校验值无效。',413)
            state=self.server.state
            receipt=state.begin_upload(lease,revision,request_id)
            if receipt is not None:self._json(200,receipt);return
            temporary=None
            try:
                fd,name=tempfile.mkstemp(prefix='.feedback-lan-',suffix='.tmp',dir=state.project.parent);temporary=Path(name)
                with os.fdopen(fd,'wb') as f:
                    remaining=size
                    while remaining:
                        state._active();chunk=self.rfile.read(min(CHUNK,remaining))
                        if not chunk:raise ShareError('传输被中断，原文件未改变。')
                        f.write(chunk);remaining-=len(chunk)
                    f.flush();os.fsync(f.fileno())
                result=state.commit_upload(temporary,lease,revision,request_id,digest)
                self._json(200,result)
            finally:
                if temporary is not None:temporary.unlink(missing_ok=True)
                with state.lock:
                    if state.uploading==request_id:state.uploading=None
        self._run(upload)


class _ProjectLock:
    """OS lock prevents two host processes sharing the same source concurrently."""
    def __init__(self,path):
        target=Path(path).resolve()
        self.stream=open(target.with_name('.'+target.name+'.lan-lock'),'a+b')
        try:
            self.stream.seek(0,2)
            if not self.stream.tell():self.stream.write(b'0');self.stream.flush()
            self.stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except Exception:
            self.stream.close();self.stream=None
            raise ShareError('该文件已在另一个程序窗口共享，或无法建立文件锁。',423)
    def close(self):
        if self.stream:self.stream.close();self.stream=None


def connection_error(exc,address,port,phase):
    number=getattr(exc,'winerror',None) or getattr(exc,'errno',None)
    if isinstance(exc,ShareError):reason=str(exc)
    elif isinstance(exc,(TimeoutError,socket.timeout)):reason='连接超时：多半是共享电脑的防火墙拦截了入站连接（共享方点“修复防火墙”），或两台电脑不在同一局域网/网络隔离。'
    elif number in (10061,111,61):reason='连接被拒绝：此地址端口没有接受连接，请确认共享仍在开启。'
    elif number in (10013,13):reason='连接被本机安全策略拒绝，请检查应用网络权限。'
    elif number in (10051,10065,101,113):reason='没有可达路由：可能选择了虚拟网卡、VPN 地址或不同网段。'
    elif isinstance(exc,ssl.SSLError):reason='TLS 加密握手失败：'+str(exc)
    else:reason=str(exc) or type(exc).__name__
    return f'{address}:{port} · {phase} · {type(exc).__name__}'+(f' [{number}]' if number else '')+'：'+reason


class ShareService:
    def __init__(self,project,directory,validator,address='127.0.0.1',port=0,lease_seconds=LEASE_SECONDS,advertise=None):
        requested=list(address) if isinstance(address,(tuple,list)) else [address]
        # '0.0.0.0' = listen on every adapter (also ones connected later); the link lists advertised LAN IPs.
        self.bind_all=requested==['0.0.0.0']
        addresses=[]
        for value in ((advertise or ['127.0.0.1']) if self.bind_all else requested[:8]):
            ip=ipaddress.ip_address(value)
            if ip.version!=4 or ip.is_unspecified or ip.is_multicast:raise ShareError('请选择本机 IPv4 网卡。')
            if str(ip) not in addresses:addresses.append(str(ip))
        addresses=addresses[:8]
        if not addresses:raise ShareError('没有可用网卡，请连接局域网后重试。')
        self.file_lock=_ProjectLock(project)
        self.state=None;self.server=None;self.thread=None;self.servers=[];self.threads=[];self.discovery=None
        self.bind_errors=[];self.addresses=[];self.automatic=len(requested)>1;self.discovery_error=''
        try:
            self.state=ShareState(project,directory,validator,lease_seconds)
            cert,key,self.fingerprint=make_certificate(directory)
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(str(cert),str(key))
            actual_port=int(port)
            if self.bind_all:
                server=None
                for candidate in ([actual_port,0] if actual_port else [0]):
                    try:server=_Server(('0.0.0.0',candidate),_Handler);break
                    except OSError as exc:self.bind_errors.append(connection_error(exc,'0.0.0.0',candidate,'监听'))
                if server is not None:
                    server.state=self.state;server.tls=context
                    actual_port=server.server_address[1];self.servers.append(server);self.addresses=list(addresses)
            else:
                for ip in addresses:
                    try:server=_Server((ip,actual_port),_Handler)
                    except OSError as exc:self.bind_errors.append(connection_error(exc,ip,actual_port,'监听'));continue
                    server.state=self.state;server.tls=context
                    actual_port=server.server_address[1];self.servers.append(server);self.addresses.append(ip)
            if not self.servers:raise ShareError('无法开启共享。\n'+'\n'.join(self.bind_errors))
            self.server=self.servers[0];self.address=self.addresses[0];self.port=actual_port
            for server in self.servers:
                thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.1},daemon=True)
                thread.start();self.threads.append(thread)
            self.thread=self.threads[0]
            try:
                from lan_discovery import DiscoveryResponder
                self.discovery=DiscoveryResponder(self.fingerprint,self.port,self.addresses)
            except OSError as exc:self.discovery_error=str(exc)
        except Exception:
            self.stop();raise
    @property
    def connection_code(self):
        return make_link(self.address,self.port,self.state.secret,self.fingerprint,self.addresses[1:])
    @property
    def local_code(self):
        """Link the sharing computer itself uses (loopback always reaches a 0.0.0.0 listener)."""
        host='127.0.0.1' if self.bind_all else self.address
        return make_link(host,self.port,self.state.secret,self.fingerprint,[])
    @property
    def legacy_code(self):
        params={'key':self.state.secret,'fp':self.fingerprint}
        if len(self.addresses)>1:params['hosts']=','.join(self.addresses[1:])
        return f'https://{self.address}:{self.port}/#'+urlencode(params)
    def stop(self):
        if self.state:self.state.stop()
        if self.discovery:self.discovery.close();self.discovery=None
        for server,thread in zip(self.servers,self.threads):
            if thread.is_alive():server.shutdown()
        for server in self.servers:server.server_close()
        for thread in self.threads:thread.join(timeout=2)
        self.file_lock.close()


class ShareClient:
    def __init__(self,code,name='访客'):
        address,port,self.secret,self.fingerprint,extra=parse_link(code)
        if len(self.secret)<32 or len(self.fingerprint) not in (32,64) or any(c not in '0123456789abcdef' for c in self.fingerprint):
            raise ShareError('连接信息不完整，请重新复制。')
        self.address=address;self.port=port;self.name=(name or '').strip()[:80] or '访客';self.identifier=uuid.uuid4().hex
        self.session=None;self.local_addresses=[];self.last_connection_report=''
        self.endpoints=[(self.address,self.port)];self._discovery_at=0
        self.link=find_link(code)
        for candidate in extra:
            if not candidate:continue
            try:other=ipaddress.ip_address(candidate)
            except ValueError:raise ShareError('连接信息中的备用地址无效，请重新复制。')
            if other.version!=4 or other.is_unspecified or other.is_multicast:raise ShareError('备用地址必须是有效 IPv4 地址。')
            endpoint=(str(other),self.port)
            if endpoint not in self.endpoints:self.endpoints.append(endpoint)
    def _connect(self,timeout=20):
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);context.check_hostname=False;context.verify_mode=ssl.CERT_NONE
        context.minimum_version=ssl.TLSVersion.TLSv1_2
        failures=[];security_failure=False;attempted=set();deadline=time.monotonic()+min(timeout,20)
        candidates=[(self.address,self.port)]+[e for e in self.endpoints if e!=(self.address,self.port)]
        for round_number in range(2):
            for address,port in candidates:
                if (address,port) in attempted or time.monotonic()>=deadline:continue
                attempted.add((address,port));phase='TCP / TLS 连接'
                connection=http.client.HTTPSConnection(address,port,context=context,timeout=max(.1,min(2.5,deadline-time.monotonic())))
                try:
                    connection.connect();phase='证书指纹校验'
                    actual=hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
                    if not hmac.compare_digest(actual[:len(self.fingerprint)],self.fingerprint):raise ShareError('证书与连接信息不符，已拒绝发送访问密钥。',403)
                    connection.sock.settimeout(timeout)
                    self.address=address;self.port=port
                    self.last_connection_report=f'已连接 {address}:{port}，TLS 指纹校验通过。'
                    return connection
                except Exception as exc:
                    connection.close();failures.append(connection_error(exc,address,port,phase))
                    if isinstance(exc,ShareError) and exc.status==403:security_failure=True
            if round_number or time.monotonic()-self._discovery_at<10:break
            self._discovery_at=time.monotonic()
            try:
                from lan_discovery import locate
                candidates=locate(self.fingerprint,self.local_addresses)
            except OSError as exc:
                failures.append('局域网发现不可用：'+str(exc));candidates=[]
        self.last_connection_report='连接失败，已尝试 '+str(len(attempted))+' 个地址。\n'+'\n'.join(failures)
        raise ShareError(self.last_connection_report,403 if security_failure else 503)
    def _headers(self,lease=None):
        headers={'Authorization':'Bearer '+self.secret,'Connection':'close'}
        if lease:headers['X-Edit-Lease']=lease
        return headers
    @staticmethod
    def _result(response):
        data=response.read(65537)
        if len(data)>65536:raise ShareError('共享电脑返回了异常响应。')
        try:value=json.loads(data)
        except Exception:raise ShareError('共享电脑返回了无效响应。')
        if response.status!=200:raise ShareError(value.get('error','共享请求失败。'),response.status)
        return value
    def request(self,path,body=None,lease=None):
        connection=self._connect()
        try:
            payload=None if body is None else json.dumps(body).encode('utf-8')
            connection.request('GET' if body is None else 'POST',path,body=payload,headers=self._headers(lease))
            return self._result(connection.getresponse())
        finally:connection.close()
    def status(self):return self.request('/v1/status')
    def acquire(self):return self.request('/v1/lease',{'client_id':self.identifier,'name':self.name})
    def heartbeat(self,lease):return self.request('/v1/heartbeat',{},lease)
    def release(self,lease):return self.request('/v1/release',{},lease)
    def download(self,destination,progress=None):
        connection=self._connect(timeout=60);destination=Path(destination)
        fd,temp=tempfile.mkstemp(prefix='.download-',dir=destination.parent)
        try:
            connection.request('GET','/v1/project',headers=self._headers());response=connection.getresponse()
            if response.status!=200:self._result(response)
            size=int(response.getheader('Content-Length','0'));revision=int(response.getheader('X-Revision','-1'));digest=response.getheader('X-SHA256','')
            if not 0<size<=MAX_PROJECT:raise ShareError('下载项目大小无效。')
            hashed=hashlib.sha256();done=0
            with os.fdopen(fd,'wb') as f:
                fd=None
                while done<size:
                    data=response.read(min(CHUNK,size-done))
                    if not data:raise ShareError('下载被中断。')
                    hashed.update(data);f.write(data);done+=len(data)
                    if progress:progress(done,size)
                f.flush();os.fsync(f.fileno())
            if not hmac.compare_digest(hashed.hexdigest(),digest):raise ShareError('下载校验失败。')
            os.replace(temp,destination)
            return {'revision':revision,'sha256':digest,'size':size}
        finally:
            connection.close()
            if fd is not None:os.close(fd)
            Path(temp).unlink(missing_ok=True)
    def upload(self,path,lease,revision,request_id=None,progress=None):
        path=Path(path);size=path.stat().st_size;digest=file_hash(path)
        request_id=request_id or uuid.uuid4().hex
        info=self.heartbeat(lease)
        if info['revision']!=revision:
            if info['sha256']==digest:return info
            raise ShareError('共享文件已有新版本，拒绝覆盖。请另存当前修改。',409)
        connection=self._connect(timeout=120)
        try:
            headers=self._headers(lease);headers.update({'Content-Length':str(size),'Content-Type':'application/octet-stream','If-Match':str(revision),'X-SHA256':digest,'X-Upload-ID':request_id})
            connection.putrequest('PUT','/v1/project')
            for key,value in headers.items():connection.putheader(key,value)
            connection.endheaders()
            sent=0
            try:
                with open(path,'rb') as f:
                    for chunk in iter(lambda:f.read(CHUNK),b''):
                        connection.send(chunk);sent+=len(chunk)
                        if progress:progress(sent,size)
            except (BrokenPipeError,ConnectionResetError):
                # The server may reject stale leases before consuming the body.
                pass
            return self._result(connection.getresponse())
        finally:connection.close()
