"""Build the Pages homepage from the newest stable GitHub Release."""
from argparse import ArgumentParser
import json
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
import zipfile

import release


REPO = re.compile(r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+')
STABLE_TAG = re.compile(r'v(\d+\.\d+\.\d+)')
HASH = re.compile(r'sha256:([a-f0-9]{64})')


def current_download(details, repo):
    if not REPO.fullmatch(repo):
        raise ValueError('预期仓库名称为 owner/repo。')
    tag = details['tag_name']
    version = STABLE_TAG.fullmatch(tag)
    if not version or details['draft'] or details['prerelease']:
        raise ValueError('当前公开版本需要使用类似 v1.7.8 的稳定版标签。')
    version = version[1]
    filename = f'PIGDING-{version}-win-x64.zip'
    matches = [asset for asset in details['assets'] if asset['name'] == filename]
    if len(matches) != 1:
        raise ValueError(f'发布缺少唯一的资产：{filename}')
    asset = matches[0]
    url = f'https://github.com/{repo}/releases/download/{tag}/{filename}'
    digest = HASH.fullmatch(asset.get('digest') or '')
    size = asset['size']
    if asset['state'] != 'uploaded' or type(size) is not int or size <= 0 or not digest:
        raise ValueError(f'GitHub 尚未提供 {filename} 的上传完成状态、大小或 SHA-256。')
    if asset['browser_download_url'] != url:
        raise ValueError('下载地址不属于当前仓库的当前版本。')
    return {'version': version, 'filename': filename, 'platform': 'Windows x64',
            'sizeBytes': size, 'sha256': digest[1]}, url


def build(repository, release_json, output):
    data, url = current_download(json.loads(release_json.read_text(encoding='utf-8')), repository)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('站点输出路径必须为空，避免把历史文件发布到首页。')
    with TemporaryDirectory() as temp:
        archive = Path(temp) / data['filename']
        with archive.open('wb') as sink:
            release.verify_hosted_download(url, data, sink=sink)
        mirror_url = f'downloads/{archive.name}'
        release.render(data, url, output, mirror_url=mirror_url)
        downloads = output / 'downloads'
        downloads.mkdir()
        shutil.move(archive, downloads / archive.name)
    (output / '_headers').unlink()
    (output / '.nojekyll').touch()
    print(f'已生成 GitHub Pages 网站：{output}', flush=True)


def package_source():
    base = release.ROOT
    target = release.DIST / 'pigding-github-source.zip'
    target.parent.mkdir(exist_ok=True)
    temp = target.with_suffix('.tmp')
    with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for web in sorted(release.WEB.rglob('*')):
            if web.is_file():
                bundle.write(web, web.relative_to(base).as_posix())
        for name in ('tools/release.py', 'tools/github_site.py'):
            bundle.write(base / name, name)
        for source in sorted((base / 'github').rglob('*')):
            if source.is_file():
                bundle.write(source, source.relative_to(base / 'github').as_posix())
    temp.replace(target)
    print(f'GitHub 仓库内容包：{target}', flush=True)


def main():
    parser = ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    make = sub.add_parser('build')
    make.add_argument('--repository', required=True)
    make.add_argument('--release-json', type=Path, required=True)
    make.add_argument('--output', type=Path, required=True)
    sub.add_parser('source')
    options = parser.parse_args()
    try:
        if options.command == 'source':
            package_source()
        else:
            build(options.repository, options.release_json, options.output)
    except (KeyError, OSError, ValueError) as error:
        parser.exit(1, f'失败：{error}\n')


if __name__ == '__main__':
    main()
