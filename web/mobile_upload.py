"""Opt-in LAN upload only. The gallery and its CSRF token stay on loopback."""
from __future__ import annotations
import base64
import errno
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import subprocess
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit
import uuid

from qrcodegen import QrCode

STATIC = Path(__file__).with_name('static')
MAX_BYTES = 25 * 1024 * 1024  # Same limit as the existing classifier.
EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}


def checked_directory(path, create=False):
    """Recreate missing folders, refusing symlinks and Windows junctions."""
    for part in (*reversed(path.parents), path):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise PermissionError('保存路径含符号链接或 Windows 重解析点')
        if create:
            part.mkdir(exist_ok=True)
        if not part.is_dir():
            raise FileNotFoundError('保存目录不存在：' + str(part))


def file_sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def adapters():
    if os.name == 'nt':
        script = r"""[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$result = foreach ($nic in [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) {
 if ($nic.OperationalStatus -eq 'Up' -and $nic.NetworkInterfaceType -notin @('Loopback','Tunnel')) {
  foreach ($address in $nic.GetIPProperties().UnicastAddresses) {
   if ($address.Address.AddressFamily -eq 'InterNetwork' -and $address.DuplicateAddressDetectionState -eq 'Preferred') {
    [pscustomobject]@{name=$nic.Name; address=$address.Address.ToString()}
   }
  }
 }
}
ConvertTo-Json -InputObject @($result) -Compress
"""
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
            capture_output=True, text=True, encoding='utf-8-sig', errors='replace', check=True, timeout=10,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        values = json.loads(result.stdout)
    else:
        values = [{'name': socket.gethostname(), 'address': a}
                  for a in set(socket.gethostbyname_ex(socket.gethostname())[2])]
    valid = []
    for row in values:
        address = ipaddress.IPv4Address(row['address'])
        if not (address.is_loopback or address.is_link_local or address.is_unspecified
                or address.is_multicast or address.is_reserved):
            valid.append(row)
    # Preserve PhotoTransfer's adapter preference without hardcoding its DHCP IP.
    return sorted(valid, key=lambda row: (row['name'] != '以太网 2', row['name'], row['address']))


def link_original(source, destination, data, incoming):
    """Stage a move without risking the source before the archive DB commits."""
    source = Path(source)
    checked_directory(source.parent)
    if source.is_symlink() or not source.resolve().is_relative_to(incoming.resolve()):
        raise ValueError('invalid_mobile_source')
    expected = hashlib.sha256(data).hexdigest()
    if file_sha(source) != expected:
        raise ValueError('mobile_source_changed')
    try:
        os.link(source, destination)  # Same volume: no second image data copy.
    except OSError as error:
        if error.errno not in (errno.EXDEV, errno.EPERM, errno.EACCES, errno.ENOSYS, errno.EOPNOTSUPP):
            raise
        # Cross-volume / no hard links: exclusive, flushed copy; source survives.
        try:
            with destination.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            raise
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    try:
        if file_sha(destination) != expected:
            raise ValueError('mobile_archive_verification_failed')
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def finish_move(store, item, result):
    """Only discard the incoming name after a committed exact-byte archive exists."""
    source = Path(item['path'])
    sha = result.get('sha256') or item.get('sha')
    recover = not source.exists() and sha
    accepted = result.get('status') == 'saved' or result.get('reason') == 'exact_duplicate'
    if not (accepted or recover):
        return result  # Rejections, near duplicates and errors keep their original.
    try:
        checked_directory(source.parent, create=True)
        if item.get('sha') and sha != item['sha']:
            raise ValueError('mobile_source_changed')
        if source.is_symlink() or not source.resolve().is_relative_to((store.state/'incoming'/'phone').resolve()):
            raise ValueError('invalid_mobile_source')
        with store.lock, store.connect() as db:
            # Coordinate with both the manager and the QQ archive's SQLite writer.
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT files FROM images WHERE sha=?', (sha,)).fetchone()
            if not row:
                raise ValueError('archive_not_committed')
            files = json.loads(row[0])
            valid = False
            for relative in files:
                try:
                    target = store.safe_file(relative)
                    if file_sha(target) == sha:
                        valid = True
                        break
                except (OSError, ValueError):
                    continue
            if not valid:
                raise ValueError('archive_verification_failed')
            if source.exists():
                if file_sha(source) != sha:
                    raise ValueError('mobile_source_changed')
                source.unlink()
            db.commit()
        if not accepted:
            result = {**result, 'status': 'saved', 'reason': 'archive_recovered', 'sha256': sha, 'files': files}
        return {**result, 'moved': True}
    except (OSError, ValueError) as error:
        return {**result, 'status': 'error', 'reason': 'archive_move_pending',
                'moveError': str(error)[:200]}


class UploadSession:
    def __init__(self, store, jobs, address):
        self.store, self.jobs = store, jobs
        self.id = uuid.uuid4().hex
        self.token = secrets.token_urlsafe(24)
        self.address = address
        self.expires = time.time() + 3600
        self.active = True
        self.job = None
        self.receipts = {}
        self.lock = threading.Lock()
        self.incoming = store.state/'incoming'/'phone'/self.id

    def authenticate(self, token):
        if not self.active or time.time() >= self.expires:
            raise PermissionError('手机上传已关闭或过期，请在电脑重新开启')
        if not secrets.compare_digest(token, 'Bearer ' + self.token):
            raise PermissionError('连接码错误，请重新扫描电脑二维码')

    def receipt(self, upload_id):
        receipt = self.receipts[upload_id]
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM manager_job_items WHERE job=? AND position=?',
                             (receipt['job'], receipt['position'])).fetchone()
        if not row:
            raise FileNotFoundError('上传记录不存在，请保留手机原件')
        path = Path(row['path'])
        if path.exists():
            checked_directory(path.parent)
            if path.is_symlink() or file_sha(path) != receipt['sha256']:
                raise ValueError('暂存图片已变化，请保留手机原件')
        else:
            result = finish_move(self.store, dict(row), {'status': 'duplicate', 'reason': 'exact_duplicate', 'sha256': receipt['sha256']})
            if not result.get('moved'):
                raise FileNotFoundError('电脑上的图片已不在原位置，请保留手机原件并核对')
        return {**receipt, 'status': row['status'], 'reason': row['reason'], 'ok': True}

    def receive(self, headers, body, name):
        length = int(headers.get('Content-Length', '0'))
        upload_id = headers.get('X-Upload-ID', '')
        expected = headers.get('X-File-SHA256', '').lower()
        if not re.fullmatch('[a-f0-9]{32}', upload_id) or not re.fullmatch('[a-f0-9]{64}', expected):
            raise ValueError('无效上传编号或 SHA-256')
        if not name or len(name) > 500 or any(c in name for c in '\x00\r\n'):
            raise ValueError('文件名过长或无效')
        extension = Path(name).suffix.lower()
        if extension not in EXTENSIONS or not 0 < length <= MAX_BYTES:
            raise ValueError('仅支持 JPG/PNG/WEBP/BMP/GIF，每张最多 25MiB')
        with self.lock:
            self.authenticate(headers.get('Authorization', ''))
            if upload_id in self.receipts:
                old = self.receipts[upload_id]
                if (old['sha256'], old['size'], old['name']) != (expected, length, name):
                    raise ValueError('上传编号已用于另一张图片')
                return self.receipt(upload_id)
            checked_directory(self.incoming, create=True)
            target = self.incoming/(upload_id+extension)
            partial = self.incoming/(upload_id+'.part')
            if target.exists():
                # A completed receive whose enqueue failed is recoverable, never overwritten.
                if target.is_symlink() or target.stat().st_size != length or file_sha(target) != expected:
                    raise ValueError('暂存图片已变化，请保留手机原件')
                queued = self.jobs.append_mobile(self.job, {'path': str(target), 'name': name, 'sha': expected})
                self.job = queued['id']
                self.receipts[upload_id] = {'job': self.job, 'position': queued['position'],
                    'sha256': expected, 'size': length, 'name': name}
                return {**self.receipts[upload_id], 'ok': True, 'status': 'pending'}
            digest = hashlib.sha256()
            try:
                with partial.open('xb') as stream:
                    remaining = length
                    while remaining:
                        block = body.read(min(remaining, 256*1024))
                        if not block:
                            raise ValueError('图片传输未完成，请重试')
                        stream.write(block)
                        digest.update(block)
                        remaining -= len(block)
                    stream.flush()
                    os.fsync(stream.fileno())
                if digest.hexdigest() != expected or file_sha(partial) != expected:
                    raise ValueError('手机与电脑 SHA-256 不一致，请重试')
                self.authenticate(headers.get('Authorization', ''))
                # Never overwrite even if an earlier enqueue failed.
                if os.name == 'nt':
                    os.rename(partial, target)
                else:
                    os.link(partial, target)
                    partial.unlink()
                queued = self.jobs.append_mobile(self.job, {'path': str(target), 'name': name, 'sha': expected})
                self.job = queued['id']
                self.receipts[upload_id] = {'job': self.job, 'position': queued['position'],
                    'sha256': expected, 'size': length, 'name': name}
                return {**self.receipts[upload_id], 'ok': True, 'status': 'pending'}
            finally:
                partial.unlink(missing_ok=True)


class PhoneHandler(BaseHTTPRequestHandler):
    # Close each request: replayed uploads may have an unread body.
    protocol_version = 'HTTP/1.0'

    def setup(self):
        super().setup()
        self.connection.settimeout(120)

    def log_message(self, *_):
        pass  # Never log pairing secrets or private image names.

    def reply(self, value, status=200, mime='application/json; charset=utf-8'):
        data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        for key, text in {'Content-Type': mime, 'Content-Length': str(len(data)), 'Cache-Control': 'no-store',
            'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'none'"}.items():
            self.send_header(key, text)
        self.end_headers()
        self.wfile.write(data)

    def dispatch(self, post=False):
        try:
            session = self.server.session
            host = f'{session.address}:{self.server.server_port}'
            if self.headers.get('Host') != host or self.headers.get('Origin') not in (None, 'http://'+host):
                raise PermissionError('不允许跨站访问')
            route = urlsplit(self.path).path
            if not post and route in ('/', '/phone.js', '/phone.css', '/hash.js', '/hash-worker.js'):
                name = 'phone.html' if route == '/' else route[1:]
                mime = 'text/html' if name.endswith('.html') else 'text/css' if name.endswith('.css') else 'text/javascript'
                return self.reply((STATIC/name).read_bytes(), mime=mime+'; charset=utf-8')
            session.authenticate(self.headers.get('Authorization', ''))
            if not post and route == '/api/status':
                return self.reply({'ok': True, 'expires': session.expires, 'maxBytes': MAX_BYTES, 'job': session.job})
            if not post and route == '/api/progress':
                with session.store.connect() as db:
                    rows=db.execute('SELECT position,status,reason FROM manager_job_items WHERE job=? ORDER BY position', (session.job,)).fetchall()
                return self.reply({'items': [dict(row) for row in rows]})
            if not post and route == '/api/receipt':
                key = parse_qs(urlsplit(self.path).query).get('id', [''])[0]
                if key not in session.receipts:
                    raise FileNotFoundError('尚未收到该图片')
                with session.lock:
                    return self.reply(session.receipt(key))
            if post and route == '/api/upload':
                name = parse_qs(urlsplit(self.path).query).get('name', [''])[0]
                return self.reply(session.receive(self.headers, self.rfile, name))
            raise FileNotFoundError('接口不存在')
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            status = 403 if isinstance(error, PermissionError) else 404 if isinstance(error, FileNotFoundError) else 400
            try:
                self.reply({'error': str(error)[:250]}, status)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch(post=True)


class MobileUploads:
    def __init__(self, store, jobs):
        self.store, self.jobs = store, jobs
        self.server = self.timer = None
        self.lock = threading.RLock()

    def info(self):
        with self.lock:
            if not self.server:
                return {'active': False}
            session = self.server.session
            url = f'http://{session.address}:{self.server.server_port}/#token={session.token}'
            qr = QrCode.encode_text(url, QrCode.Ecc.MEDIUM)
            parts = [f'M{x+4},{y+4}h1v1h-1z' for y in range(qr.get_size())
                     for x in range(qr.get_size()) if qr.get_module(x, y)]
            size = qr.get_size()+8
            svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" shape-rendering="crispEdges"><path fill="white" d="M0,0h{size}v{size}H0z"/><path d="{"".join(parts)}"/></svg>'
            return {'active': session.active and time.time() < session.expires, 'url': url,
                'qr': 'data:image/svg+xml;base64,'+base64.b64encode(svg.encode()).decode(),
                'expires': session.expires, 'job': session.job, 'address': session.address}

    def start(self, address):
        if not self.store.config.get('classificationEnabled', True):
            raise ValueError('当前为浏览模式，需先安装本地分类环境')
        with self.lock:
            if self.server:
                if self.info()['active']:
                    return self.info()
                self.stop()
            if address not in [row['address'] for row in adapters()]:
                raise ValueError('请选择当前已连接网卡的 IPv4 地址')
            session = UploadSession(self.store, self.jobs, address)
            server = ThreadingHTTPServer((address, 0), PhoneHandler)
            server.daemon_threads = True
            server.session = session
            self.server = server
            threading.Thread(target=server.serve_forever, daemon=True, name='phone-uploads').start()
            self.timer = threading.Timer(3600, self.stop)
            self.timer.daemon = True
            self.timer.start()
            return self.info()

    def stop(self):
        with self.lock:
            if self.timer:
                self.timer.cancel()
                self.timer = None
            if self.server:
                self.server.session.active = False
                self.server.shutdown()
                self.server.server_close()
                self.server = None
        return {'active': False}
