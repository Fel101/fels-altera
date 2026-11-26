"""Build FelsAltera-Setup and SHA256SUMS from a local Git commit or release tag."""

import argparse
from contextlib import contextmanager
from io import BytesIO
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

from windows_install import file_digest, setup_name, version_key

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ('altera.py', 'core', 'content', 'docs', 'scripts', 'static', 'templates', 'voice',
           'README.md', 'LICENSE', 'SECURITY.md', 'CHANGELOG.md', 'CONTRIBUTING.md', 'config.example.yaml',
           'personality.yaml', '.env.example', 'requirements.txt', 'requirements.lock.txt', 'setup.bat',
           'setup.sh', 'start.bat')


def stage_app(ref, destination):
    data = subprocess.check_output(['git', 'archive', '--format=zip', ref, '--', *PACKAGE], cwd=ROOT)
    with zipfile.ZipFile(BytesIO(data)) as archive:
        archive.extractall(destination)


def stage_python(staging, package=None):
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    command = [str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass',
               '-File', str(ROOT / 'scripts/get_installer_python.ps1'), '-Destination', str(staging / 'python')]
    if package:
        command += ['-Package', str(package.resolve(strict=True))]
    environment = {key: value for key, value in os.environ.items() if key.upper() != 'PSMODULEPATH'}
    subprocess.run(command, check=True, env=environment)


def stage_wheels(source, destination, *, cached=None):
    if cached:
        destination.mkdir()
        for wheel in cached.glob('*.whl'):
            shutil.copyfile(wheel, destination / wheel.name)
        return
    subprocess.run([sys.executable, '-I', '-m', 'pip', '--isolated', 'download', '--disable-pip-version-check',
                    '--only-binary=:all:', '--index-url', 'https://pypi.org/simple', '--dest', str(destination),
                    '-r', str(source / 'requirements.lock.txt')], check=True,
                   env={**os.environ, 'PIP_CONFIG_FILE': os.devnull})


def find_iscc(given=None):
    for candidate in (given, os.environ.get('ISCC'), shutil.which('iscc'),
                      Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Inno Setup 6/ISCC.exe',
                      Path(os.environ.get('ProgramFiles(x86)', '')) / 'Inno Setup 6/ISCC.exe',
                      Path(os.environ.get('ProgramFiles', '')) / 'Inno Setup 6/ISCC.exe'):
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    raise ValueError('Inno Setup 6 was not found. Install it or give its ISCC.exe with --iscc.')


def stage_icon(source, destination):
    # An ICO directory with a single existing 192px PNG entry.
    png = (source / 'static/icons/icon-192.png').read_bytes()
    header = struct.pack('<3H4B2H2I', 0, 1, 1, 192, 192, 0, 0, 1, 32, len(png), 22)
    destination.write_bytes(header + png)


@contextmanager
def prepared_stage(ref, output, *, package=None, wheels=None):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='altera-installer-', dir=output) as temp:
        staging = Path(temp)
        stage_app(ref, staging / 'app')
        stage_python(staging, package)
        stage_wheels(staging / 'app', staging / 'wheels', cached=wheels)
        stage_icon(staging / 'app', staging / 'altera.ico')
        yield staging


def compile_setup(staging, output, compiler, *, tag=None, script=ROOT / 'installer/Altera.iss'):
    version_file = staging / 'app/core/version.py'
    match = re.search(r'^VERSION = "([^"]+)"$', version_file.read_text(encoding='utf-8'), re.M)
    if not match:
        raise ValueError('The packaged source has no Altera version.')
    version = match[1]
    version_key(version)
    if tag is not None and tag != f'v{version}':
        raise ValueError(f'The tag must be v{version} to match the packaged source.')
    result = output / setup_name(version)
    subprocess.run([str(compiler), '/Qp', f'/DAppVersion={version}', f'/DStaging={staging}',
                    f'/DOutput={output}', str(script)], check=True)
    (output / 'SHA256SUMS').write_text(f'{file_digest(result)}  {result.name}\n', encoding='ascii', newline='\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', default='HEAD', help='The commit or tag to package.')
    parser.add_argument('--tag', help='Require this tag to match the packaged Altera version.')
    parser.add_argument('--output', type=Path, default=ROOT / 'out/installer')
    parser.add_argument('--iscc', type=Path)
    parser.add_argument('--python-package', type=Path, help='Use an existing copy of the pinned NuGet package.')
    parser.add_argument('--wheels', type=Path, help='Use a prepared local wheelhouse.')
    args = parser.parse_args()
    if os.name != 'nt' or sys.version_info[:2] != (3, 12) or struct.calcsize('P') != 8:
        parser.error('Build on Windows with 64-bit Python 3.12 so the wheels match the bundled runtime.')
    try:
        compiler = find_iscc(args.iscc)
        options = {'package': args.python_package, 'wheels': args.wheels}
        with prepared_stage(args.ref, args.output.resolve(), **options) as staging:
            result = compile_setup(staging, args.output.resolve(), compiler, tag=args.tag)
        print(result)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Could not build Altera Setup. {error}\n')


if __name__ == '__main__':
    main()
