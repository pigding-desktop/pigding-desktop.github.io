"""Package a standalone PIGDING release, preview it or build the public site."""
from argparse import ArgumentParser
import hashlib
from html import escape
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import struct
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
STORE = ROOT / 'release'
DIST = ROOT / 'dist'
VERSION = re.compile(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?')
DOCK_VERSION = (1, 7, 11)
VOICE_PACK_FILENAMES = tuple(f'voice-{name}.pigding-extension.json' for name in ('rough', 'lilting', 'normal'))
RUNTIME = {'.dll', '.pak', '.bin', '.dat'}
REQUIRED = {'resources/app.asar', 'locales/en-US.pak', 'ffmpeg.dll', 'icudtl.dat', 'resources.pak', 'LICENSE', 'LICENSES.chromium.html'}


def sha(file):
    with file.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def archive_info(file):
    with file.open('rb') as stream:
        _, header_size, _, json_size = struct.unpack('<IIII', stream.read(16))
        if not 0 < json_size < 8 * 1024 * 1024:
            raise ValueError('无效的 ASAR 文件头')
        header = json.loads(stream.read(json_size))
        def entry(name):
            item = header
            for part in name.split('/'):
                item = item['files'][part]
            stream.seek(8 + header_size + int(item['offset']))
            return json.loads(stream.read(item['size']))
        edition = entry('app/edition.json')
        package = entry('package.json')
        if edition['id'] != 'demon' or edition.get('workPanel') is not False or set(edition['skins']) != {'demon', 'demon-shiny'}:
            raise ValueError('请选择移除工作面板后的恶魔叮独立版目录。')
        version = package['version']
        if not VERSION.fullmatch(version):
            raise ValueError('版本号必须来自应用 package.json，并遵循 1.2.3 格式。')
        return version


def executable(directory):
    for name in ('PIGDING.exe', '恶魔叮.exe'):
        candidate = directory / name
        if candidate.is_file():
            with candidate.open('rb') as stream:
                if stream.read(2) != b'MZ':
                    raise ValueError('启动程序不是 Windows EXE。')
                stream.seek(0x3c)
                pe = struct.unpack('<I', stream.read(4))[0]
                stream.seek(pe)
                if stream.read(6) != b'PE\0\0\x64\x86':
                    raise ValueError('只接受 Windows x64 版启动程序。')
            return candidate
    raise FileNotFoundError('缺少 PIGDING.exe 或恶魔叮.exe。')


def has_voice_dock(version):
    return tuple(map(int, version.split('-', 1)[0].split('.'))) >= DOCK_VERSION


def voice_pack_files(directory, version):
    if not has_voice_dock(version):
        return {}
    files = {f'resources/voice-packs/{name}': directory / 'resources' / 'voice-packs' / name
             for name in VOICE_PACK_FILENAMES}
    missing = [name for name, file in files.items() if not file.is_file()]
    if missing:
        raise ValueError('缺少互动语音包：' + ', '.join(missing))
    return files


def prepare(directory):
    directory = directory.resolve(strict=True)
    exe = executable(directory)
    version = archive_info(directory / 'resources/app.asar')
    files = {f.name: f for f in directory.iterdir() if f.is_file() and (f.suffix.lower() in RUNTIME or f.name in {'LICENSE','LICENSES.chromium.html','vk_swiftshader_icd.json'})}
    files['PIGDING.exe'] = exe
    files['resources/app.asar'] = directory / 'resources/app.asar'
    files.update(voice_pack_files(directory, version))
    for locale in sorted((directory / 'locales').glob('*.pak')):
        files[f'locales/{locale.name}'] = locale
    if missing := REQUIRED - files.keys():
        raise ValueError('缺少运行文件：' + ', '.join(sorted(missing)))
    for name, file in files.items():
        if not file.resolve(strict=True).is_relative_to(directory):
            raise ValueError(f'运行文件位于打包目录之外：{name}')
    STORE.mkdir(exist_ok=True)
    filename = f'PIGDING-{version}-win-x64.zip'
    target = STORE / filename
    with tempfile.TemporaryDirectory(dir=STORE) as temp:
        staged = Path(temp) / filename
        dock_help = '右键菜单点「扩展坞…」，可以试听和启用互动语音、安装飞行和其他扩展。\r\n' if has_voice_dock(version) else ''
        with zipfile.ZipFile(staged, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zip:
            for name, file in sorted(files.items()):
                zip.write(file, f'PIGDING/{name}')
            zip.writestr('PIGDING/启动说明.txt', f'恶魔叮 PIGDING {version}\r\n\r\n解压完整文件夹，双击 PIGDING.exe。\r\n不要只移动 EXE；请保留 resources、locales 和 DLL。\r\n右键宠物或托盘图标打开菜单。Ctrl + Alt + D 显示或隐藏桌宠。\r\n{dock_help}更新时请先退出旧程序，再解压新版到新的文件夹。\r\n'.encode('utf-8-sig'))
        if target.exists() and sha(staged) != sha(target):
            # File dates alone may change the ZIP hash. Compare entry hashes
            # without replacing an archive that might already have been shared.
            with zipfile.ZipFile(staged) as new, zipfile.ZipFile(target) as old:
                def contents(archive):
                    result = {}
                    for item in archive.infolist():
                        with archive.open(item) as stream:
                            result[item.filename] = hashlib.file_digest(stream, 'sha256').digest()
                    return result
                if contents(new) != contents(old):
                    raise ValueError(f'{filename} 已存在但内容不同。请给新程序提升版本号，避免覆盖已发布的版本。')
        if not target.exists():
            os.replace(staged, target)
    manifest = {'version': version, 'filename': filename, 'platform': 'Windows x64', 'sizeBytes': target.stat().st_size, 'sha256': sha(target)}
    write_json(STORE / 'current.json', manifest)
    print(f'已准备 {target} ({format_size(manifest["sizeBytes"])})', flush=True)


def write_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def format_size(size):
    return f'{size / 1024**2:.1f} MiB'


def https_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('公开下载地址必须是可直接访问的 HTTPS 链接。')
    return value


def verify_hosted_download(url, data, *, sink=None):
    https_url(url)
    print('正在校验公开下载文件的大小与 SHA-256……', flush=True)
    with urlopen(Request(url, headers={'Accept-Encoding':'identity', 'User-Agent':'PIGDING-release-verifier/1'}), timeout=30) as response:
        https_url(response.geturl())
        digest = hashlib.sha256()
        size = 0
        while block := response.read(1024 * 1024):
            size += len(block)
            if size > data['sizeBytes']:
                break
            digest.update(block)
            if sink is not None:
                sink.write(block)
        if response.status != 200 or size != data['sizeBytes'] or digest.hexdigest() != data['sha256']:
            raise ValueError('公开地址返回的文件与当前下载包不一致。请先上传正确的 ZIP 文件。')


CLOUD_SHARE_PROVIDERS = {
    'pan.quark.cn': ('夸克网盘', ('/s/',)),
    'pan.baidu.com': ('百度网盘', ('/s/', '/share/init', '/share/link')),
    'pan.xunlei.com': ('迅雷云盘', ('/s/',)),
    'yun.139.com': ('中国移动云盘', ('/shareweb/', '/w/i/')),
    'caiyun.139.com': ('中国移动云盘', ('/m/i', '/shareweb/')),
}


def cloud_shares(version):
    config = WEB / 'cloud-download.json'
    if not config.is_file():
        return []
    warning = '::warning::Ignoring invalid cloud download configuration.'
    try:
        entry = json.loads(config.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        print(warning, flush=True)
        return []
    if not isinstance(entry, dict) or entry.get('version') != version:
        return []
    links = entry.get('links', [])
    if not isinstance(links, list):
        print(warning, flush=True)
        return []
    shares = {}
    for link in links:
        try:
            if not isinstance(link, dict):
                raise ValueError('Invalid share entry')
            url = link.get('url')
            code = link.get('accessCode', '')
            if not isinstance(url, str) or re.search(r'[\x00-\x20\x7f]', url):
                raise ValueError('Invalid share URL')
            if not isinstance(code, str) or (code and not re.fullmatch(r'[A-Za-z0-9]{4,8}', code)):
                raise ValueError('Invalid share code')
            parsed = urlsplit(url)
            provider = CLOUD_SHARE_PROVIDERS.get(parsed.netloc)
            if parsed.scheme != 'https' or not provider or not parsed.path.startswith(provider[1]):
                raise ValueError('Invalid provider or share path')
            shares.setdefault(provider[0], {'provider': provider[0], 'url': url, 'accessCode': code})
        except ValueError:
            print(warning, flush=True)
    return list(shares.values())


def cloud_links_html(shares):
    links = []
    for share in shares:
        code = share['accessCode']
        note = f'<span>提取码：<code>{escape(code)}</code></span>' if code else ''
        links.append(f'<li><a class="cloud-link" href="{escape(share["url"], quote=True)}">'
                     f'{escape(share["provider"])}</a>{note}</li>')
    return '\n'.join(links)


def render(data, url, out, *, mirror_url=None, domestic_url=None):
    out.mkdir(parents=True, exist_ok=True)
    primary = domestic_url or url
    fields = {'VERSION': data['version'], 'DOWNLOAD_URL': primary, 'FILENAME': data['filename'], 'PLATFORM': data['platform'], 'SIZE': format_size(data['sizeBytes']), 'SHA256': data['sha256']}
    shares = cloud_shares(data['version'])
    cloud_links = cloud_links_html(shares)
    fields.update(CLOUD_HIDDEN='' if shares else ' hidden',
                  DOWNLOAD_TOP='国内线路下载' if domestic_url else '下载 Windows 版',
                  DOWNLOAD_BOTTOM='国内线路下载' if domestic_url else '下载便携包')
    version = data['version']
    if not VERSION.fullmatch(version):
        raise ValueError('发布版本号无效。')
    dock = has_voice_dock(version)
    notes = WEB / 'updates' / f'{version}.html'
    markup = {
        'CLOUD_LINKS': cloud_links,
        'UPDATE_NAV': '<a href="#updates">版本更新</a>' if notes.is_file() else '',
        'UPDATE_SECTION': notes.read_text(encoding='utf-8') if notes.is_file() else '',
        'DOCK_NAV': '<a href="#extension-dock">扩展坞</a>' if dock else '',
        'DOCK_SECTION': (WEB / 'extension-dock.html').read_text(encoding='utf-8') if dock else '',
    }
    html = re.sub(r'\{\{([A-Z0-9_]+)\}\}',
                  lambda m: markup[m[1]] if m[1] in markup else escape(str(fields[m[1]]), quote=True),
                  (WEB/'index.html').read_text(encoding='utf-8'))
    (out/'index.html').write_text(html, encoding='utf-8')
    for name in ('style.css','preview.js','_headers','404.html'):
        shutil.copyfile(WEB/name, out/name)
    shutil.copytree(WEB/'assets', out/'assets', dirs_exist_ok=True)
    manifest = {**data, 'url': primary}
    if domestic_url:
        manifest['releaseUrl'] = url
    if mirror_url:
        manifest['mirrorUrl'] = mirror_url
    if shares:
        manifest['cloudShares'] = shares
    write_json(out/'release.json', manifest)


def build(url=None):
    data = json.loads((STORE/'current.json').read_text(encoding='utf-8'))
    expected_name = f'PIGDING-{data["version"]}-win-x64.zip'
    if not VERSION.fullmatch(data['version']) or data['filename'] != expected_name:
        raise ValueError('发布清单文件名无效。请重新运行 prepare。')
    archive = STORE / expected_name
    if archive.stat().st_size != data['sizeBytes'] or sha(archive) != data['sha256']:
        raise ValueError('当前 ZIP 与发布清单不一致。请重新运行 prepare。')
    if url:
        verify_hosted_download(url, data)
        DIST.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=DIST) as stage:
            staging = Path(stage)
            render(data, url, staging)
            temporary = DIST/'pigding-pages.tmp'
            with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as zip:
                for file in sorted(staging.rglob('*')):
                    if file.is_file(): zip.write(file, file.relative_to(staging).as_posix())
            os.replace(temporary, DIST/'pigding-pages.zip')
        print(f'可上传到 Pages 的站点包：{DIST / "pigding-pages.zip"}', flush=True)
    else:
        preview = DIST/'preview'
        downloads = preview/'downloads'
        downloads.mkdir(parents=True, exist_ok=True)
        target = downloads/expected_name
        if target.exists() and sha(target) != data['sha256']:
            raise ValueError('预览下载文件与当前包不一致，请移走旧文件后再构建。')
        if not target.exists():
            try:
                os.link(archive, target)
            except OSError:
                shutil.copyfile(archive, target)
        render(data, f'downloads/{expected_name}', preview)
        for previous in downloads.glob('PIGDING-*-win-x64.zip'):
            if previous != target and previous.is_file():
                previous.unlink()
        print(f'本站与下载包：{preview / "index.html"}', flush=True)


def serve(port):
    directory = DIST/'preview'
    if not (directory/'index.html').is_file():
        build()
    class Preview(SimpleHTTPRequestHandler):
        def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(directory),**kwargs)
        def end_headers(self):
            self.send_header('Cache-Control','no-store')
            super().end_headers()
        def list_directory(self, path):
            self.send_error(404)
    with ThreadingHTTPServer(('127.0.0.1', port), Preview) as server:
        print(f'本地预览：http://127.0.0.1:{server.server_port}/', flush=True)
        server.serve_forever()


def main():
    parser = ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('prepare').add_argument('directory',type=Path)
    commands.add_parser('build').add_argument('--download-url')
    commands.add_parser('serve').add_argument('--port',type=int,default=8787)
    args = parser.parse_args()
    try:
        if args.command=='prepare': prepare(args.directory)
        elif args.command=='build': build(args.download_url)
        else: serve(args.port)
    except (KeyError, OSError, ValueError, struct.error) as error:
        parser.exit(1, f'失败：{error}\n')


if __name__ == '__main__':
    main()
