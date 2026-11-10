"""Targeted, credential-free LAN discovery; enabled only for active shares.
Inspired by LocalSend's multicast discovery, with a separate protocol and port.
Only a client already holding the certificate fingerprint can locate a share.
"""
import ipaddress,json,secrets,socket,threading,time
GROUP='239.255.61.3'
PORT=48762
MAGIC='feedback-lan-discovery-1'

def message(data):
    if len(data)>2048:return None
    try:
        value=json.loads(data)
        if not isinstance(value,dict) or value.get('protocol')!=MAGIC:return None
        nonce=value.get('nonce','');fp=value.get('fingerprint','')
        if not isinstance(nonce,str) or len(nonce)!=24 or any(c not in '0123456789abcdef' for c in nonce):return None
        if not isinstance(fp,str) or len(fp) not in (32,64) or any(c not in '0123456789abcdef' for c in fp):return None
        return value
    except (ValueError,UnicodeError,TypeError):return None

class DiscoveryResponder:
    def __init__(self,fingerprint,tcp_port,addresses):
        self.fingerprint=fingerprint;self.port=tcp_port;self.closed=False
        self.socket=socket.socket(socket.AF_INET,socket.SOCK_DGRAM,socket.IPPROTO_UDP)
        try:
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            self.socket.bind(('',PORT));self.socket.settimeout(.2)
            joined=0
            for address in addresses:
                try:
                    self.socket.setsockopt(socket.IPPROTO_IP,socket.IP_ADD_MEMBERSHIP,socket.inet_aton(GROUP)+socket.inet_aton(address));joined+=1
                except OSError:pass
            if not joined:raise OSError('No multicast-capable interface')
            self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
        except Exception:self.socket.close();raise
    def run(self):
        interval=time.monotonic();count=0
        while not self.closed:
            try:
                data,origin=self.socket.recvfrom(2049);value=message(data)
                # Short links carry a 128-bit certificate prefix; match it without revealing anything new.
                if not value or value.get('type')!='locate' or not self.fingerprint.startswith(value['fingerprint']):continue
                now=time.monotonic()
                if now-interval>=1:interval=now;count=0
                if count>=16:continue
                count+=1
                reply={'protocol':MAGIC,'type':'found','fingerprint':value['fingerprint'],'nonce':value['nonce'],'port':self.port}
                self.socket.sendto(json.dumps(reply).encode(),origin)
            except socket.timeout:continue
            except OSError:
                if self.closed:return
    def close(self):
        self.closed=True;self.socket.close();self.thread.join(timeout=1)

def locate(fingerprint,interfaces=(),duration=.7):
    """No subnet scan, file names, keys or document data are transmitted."""
    nonce=secrets.token_hex(12);payload=json.dumps({'protocol':MAGIC,'type':'locate','fingerprint':fingerprint,'nonce':nonce}).encode()
    found=[]
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM,socket.IPPROTO_UDP) as sock:
        sock.bind(('',0));sock.settimeout(.08);sock.setsockopt(socket.IPPROTO_IP,socket.IP_MULTICAST_TTL,1)
        for interface in ['0.0.0.0']+list(interfaces)[:8]:
            try:
                sock.setsockopt(socket.IPPROTO_IP,socket.IP_MULTICAST_IF,socket.inet_aton(interface))
                sock.sendto(payload,(GROUP,PORT))
            except OSError:continue
        end=time.monotonic()+duration
        while time.monotonic()<end:
            try:
                data,origin=sock.recvfrom(2049);value=message(data)
                if not value or value.get('type')!='found' or value['nonce']!=nonce or value['fingerprint']!=fingerprint:continue
                port=value.get('port');ip=ipaddress.ip_address(origin[0])
                if type(port) is not int or not 1<=port<=65535 or ip.is_unspecified or ip.is_multicast:continue
                endpoint=(str(ip),port)
                if endpoint not in found:found.append(endpoint)
                if len(found)>=8:break
            except socket.timeout:continue
            except (OSError,ValueError):break
    return found
