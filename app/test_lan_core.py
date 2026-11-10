"""LAN protocol tests use synthetic files and loopback only."""
import tempfile,unittest,time,uuid,hashlib,threading,socket
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from lan_core import ShareService,ShareClient,ShareError,file_hash

def valid(path):
    if not Path(path).read_bytes().startswith(b'board:'):raise ValueError('invalid project')

class LanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent);self.root=Path(self.tmp.name)
        self.source=self.root/'source.fbboard';self.source.write_bytes(b'board:original')
        self.service=ShareService(self.source,self.root/'server',valid)
        self.a=ShareClient(self.service.connection_code,'Alice');self.b=ShareClient(self.service.connection_code,'Bob')
    def tearDown(self):
        self.service.stop();self.tmp.cleanup()
    def upload(self,data=b'board:changed',lease=None,revision=0):
        p=self.root/'edited.fbboard';p.write_bytes(data)
        return self.a.upload(p,lease,revision)
    def test_second_host_cannot_share_same_file(self):
        with self.assertRaises(ShareError):ShareService(self.source,self.root/'second',valid)
        self.service.stop()
        other=ShareService(self.source,self.root/'second',valid)
        other.stop()
    def test_commit_rechecks_revision_and_checksum(self):
        lease=self.a.acquire()['lease'];state=self.service.state;rid=uuid.uuid4().hex
        state.begin_upload(lease,0,rid);p=self.root/'staged';p.write_bytes(b'board:new')
        with self.assertRaises(ShareError):state.commit_upload(p,lease,0,rid,'0'*64)
        with self.assertRaises(ShareError):state.commit_upload(p,lease,1,rid,file_hash(p))
        self.assertEqual(self.source.read_bytes(),b'board:original')
    def test_alternate_address_fallback(self):
        code=self.service.legacy_code.replace('127.0.0.1:','127.0.0.2:')+'&hosts=127.0.0.1'
        client=ShareClient(code,'fallback');self.assertEqual(client.status()['revision'],0)
        self.assertEqual(client.address,'127.0.0.1')
    def test_multicast_fallback_keeps_certificate_pin(self):
        from unittest.mock import patch
        code=self.service.legacy_code.replace('127.0.0.1:','127.0.0.2:')
        client=ShareClient(code,'discovery')
        with patch('lan_discovery.locate',return_value=[('127.0.0.1',self.service.port)]) as discovery:
            self.assertEqual(client.status()['revision'],0)
            self.assertEqual(discovery.call_args.args[0],self.service.fingerprint)
    def test_discovery_reply_has_no_credentials(self):
        import json,secrets
        from lan_discovery import MAGIC,PORT,message
        self.assertIsNotNone(self.service.discovery)
        nonce=secrets.token_hex(12)
        query={'protocol':MAGIC,'type':'locate','fingerprint':self.service.fingerprint,'nonce':nonce}
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            sock.settimeout(2);sock.sendto(json.dumps(query).encode(),('127.0.0.1',PORT));data,_=sock.recvfrom(2048)
        reply=message(data);self.assertEqual(reply['nonce'],nonce);self.assertEqual(reply['port'],self.service.port)
        self.assertNotIn(self.service.state.secret,data.decode());self.assertNotIn('source.fbboard',data.decode())
        self.assertIsNone(message(b'x'*2049))
    def test_multiple_listeners_share_one_edit_lock(self):
        self.service.stop()
        self.service=ShareService(self.source,self.root/'multi',valid,['127.0.0.1','127.0.0.2'])
        self.assertEqual(len(self.service.addresses),2)
        a=ShareClient(self.service.connection_code,'A')
        b=ShareClient(self.service.legacy_code.replace('127.0.0.1:','127.0.0.2:'),'B')
        a.acquire()
        with self.assertRaises(ShareError) as e:b.acquire()
        self.assertEqual(e.exception.status,423)
    def test_partial_bind_failure_preserves_working_address(self):
        self.service.stop()
        self.service=ShareService(self.source,self.root/'partial',valid,['192.0.2.123','127.0.0.1'])
        self.assertTrue(self.service.bind_errors);self.assertEqual(self.service.addresses,['127.0.0.1'])
        self.assertEqual(ShareClient(self.service.connection_code).status()['revision'],0)
    def test_failure_report_omits_key(self):
        self.service.stop()
        with self.assertRaises(ShareError) as e:self.a.status()
        self.assertIn('127.0.0.1:',str(e.exception));self.assertIn('TCP / TLS',str(e.exception))
        self.assertNotIn(self.a.secret,str(e.exception))
    def test_download_and_certificate_pin(self):
        dest=self.root/'copy.fbboard';info=self.a.download(dest)
        self.assertEqual(dest.read_bytes(),self.source.read_bytes());self.assertEqual(info['revision'],0)
        self.a.fingerprint='0'*64
        with self.assertRaises(ShareError) as e:self.a.status()
        self.assertEqual(e.exception.status,403)
    def test_authorization_and_routes(self):
        self.a.secret='x'*43
        with self.assertRaises(ShareError) as e:self.a.status()
        self.assertEqual(e.exception.status,401)
        with self.assertRaises(ShareError) as e:self.b.request('/../source.fbboard')
        self.assertEqual(e.exception.status,404)
        c=self.b._connect()
        try:
            c.request('GET','/v1/status',headers={**self.b._headers(),'Origin':'https://example.com'})
            self.assertEqual(c.getresponse().status,403)
        finally:c.close()
    def test_single_writer_and_release(self):
        lease=self.a.acquire()['lease']
        with self.assertRaises(ShareError) as e:self.b.acquire()
        self.assertEqual(e.exception.status,423)
        self.a.heartbeat(lease);self.a.release(lease)
        self.assertTrue(self.b.acquire()['lease'])
        with self.assertRaises(ShareError):self.upload(lease=lease)
        self.assertEqual(self.source.read_bytes(),b'board:original')
    def test_commit_history_and_reader_refresh(self):
        lease=self.a.acquire()['lease'];info=self.upload(lease=lease)
        self.assertEqual(info['revision'],1);self.assertEqual(self.source.read_bytes(),b'board:changed')
        self.assertEqual((self.service.state.history/'000000.fbboard').read_bytes(),b'board:original')
        dest=self.root/'copy.fbboard';self.b.download(dest);self.assertEqual(dest.read_bytes(),b'board:changed')
    def test_stale_revision_and_external_write(self):
        lease=self.a.acquire()['lease'];self.upload(lease=lease)
        with self.assertRaises(ShareError) as e:self.upload(b'board:stale',lease,0)
        self.assertEqual(e.exception.status,409)
        self.source.write_bytes(b'board:external')
        with self.assertRaises(ShareError) as e:self.upload(b'board:overwrite',lease,1)
        self.assertEqual(e.exception.status,409);self.assertEqual(self.source.read_bytes(),b'board:external')
    def test_invalid_upload_does_not_replace(self):
        lease=self.a.acquire()['lease']
        with self.assertRaises(ShareError):self.upload(b'invalid',lease)
        self.assertEqual(self.source.read_bytes(),b'board:original');self.assertIsNone(self.service.state.uploading)
    def test_expired_lease_fenced(self):
        lease=self.a.acquire()['lease'];self.service.state.lease['expires']=time.monotonic()-1
        second=self.b.acquire()['lease']
        with self.assertRaises(ShareError):self.upload(lease=lease)
        self.assertEqual(self.b.heartbeat(second)['editor'],'Bob')
    def test_stop_blocks_inflight_commit(self):
        lease=self.a.acquire()['lease'];state=self.service.state;rid=uuid.uuid4().hex
        state.begin_upload(lease,0,rid);p=self.root/'staged';p.write_bytes(b'board:new')
        state.stop()
        with self.assertRaises(ShareError):state.commit_upload(p,lease,0,rid,file_hash(p))
        self.assertEqual(self.source.read_bytes(),b'board:original')
    def test_concurrent_acquire_only_one_winner(self):
        def attempt(i):
            try:ShareClient(self.service.connection_code,str(i)).acquire();return True
            except ShareError:return False
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(attempt,range(8)))
        self.assertEqual(sum(results),1)
    def test_slow_handshake_does_not_block_other_clients(self):
        sock=socket.create_connection((self.service.address,self.service.port))
        try:self.assertEqual(self.a.status()['revision'],0)
        finally:sock.close()
    def test_partial_upload_preserves_original(self):
        lease=self.a.acquire()['lease'];c=self.a._connect()
        c.putrequest('PUT','/v1/project')
        for k,v in {**self.a._headers(lease),'Content-Length':'1000','If-Match':'0','X-SHA256':'0'*64,'X-Upload-ID':uuid.uuid4().hex}.items():c.putheader(k,v)
        c.endheaders();c.send(b'board:partial');c.close()
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if self.service.state.uploading is None:break
            time.sleep(.01)
        self.assertEqual(self.source.read_bytes(),b'board:original')
    def test_retry_ack_same_submission_commits_once(self):
        lease=self.a.acquire()['lease'];p=self.root/'upload';p.write_bytes(b'board:new');rid=uuid.uuid4().hex
        self.a.upload(p,lease,0,rid);info=self.a.upload(p,lease,0,rid)
        self.assertEqual(info['revision'],1);self.assertEqual(self.a.status()['revision'],1)

if __name__=='__main__':unittest.main(verbosity=2)

