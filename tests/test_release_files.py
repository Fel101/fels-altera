"""A release's notes (scripts/release_notes.py), its files as checked before the draft is published
(scripts/check_release_files.py), and the workflow that builds and checks them."""
import hashlib
import re
import types
from pathlib import Path

import pytest

from core.version import UPDATE_REPOSITORY, VERSION
from scripts import check_release_files as files
from scripts import release_notes as notes
from scripts.windows_install import setup_name

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / '.github' / 'workflows'

CHANGELOG = """# Changelog

## 1.1.0 (2026-12-01)

What changed for you:

- **Voices:** a pal can speak with a voice from
  [Voice Lab](https://github.com/Fel101/fels-voice-lab), as [Voice](docs/VOICE.md#voice-lab) says.
- Pictures: ![home](docs/images/home.png)

| Table | stays |
| --- | --- |

See [the 1.0.0 notes](#100-2026-11-26).

## 1.0.0 (2026-11-26)

The first public release.
"""


def test_the_notes_are_the_versions_section_unwrapped_with_links_at_its_tag(tmp_path):
    changelog = tmp_path / 'CHANGELOG.md'
    changelog.write_text(CHANGELOG, encoding='utf-8')
    text = notes.notes('1.1.0', changelog=changelog, repository='Fel101/fels-altera')
    blob = 'https://github.com/Fel101/fels-altera/blob/v1.1.0'
    assert text.splitlines() == [
        'What changed for you:', '',
        '- **Voices:** a pal can speak with a voice from [Voice Lab](https://github.com/Fel101/fels-voice-lab), as '
        f'[Voice]({blob}/docs/VOICE.md#voice-lab) says.',
        '- Pictures: ![home](https://github.com/Fel101/fels-altera/raw/v1.1.0/docs/images/home.png)', '',
        '| Table | stays |', '| --- | --- |', '',
        f'See [the 1.0.0 notes]({blob}/CHANGELOG.md#100-2026-11-26).']


def test_the_notes_refuse_a_tag_or_heading_check_for_updates_could_not_read(tmp_path):
    changelog = tmp_path / 'CHANGELOG.md'
    changelog.write_text(CHANGELOG + '\n## 1.2.0\n\nNo date.\n\n## 1.3.0 (soon)\n\nx\n', encoding='utf-8')
    for version, tag, problem in (('1.1.0', 'v1.1.1', 'is not v1.1.0'), ('1.1', None, 'not a version'),
                                  ('9.9.9', None, 'no section for 9.9.9'), ('1.2.0', None, 'has no date'),
                                  ('1.3.0', None, "'soon' for its date")):
        with pytest.raises(notes.NotesError, match=problem):
            notes.notes(version, changelog=changelog, tag=tag)
    good, bad = tmp_path / 'n.md', tmp_path / 'bad.md'
    assert notes.main(['1.1.0', '--tag', 'v1.1.0', '--changelog', str(changelog), '--output', str(good)]) == 0
    assert notes.main(['1.1.0', '--tag', 'v2.0.0', '--changelog', str(changelog), '--output', str(bad)]) == 1
    assert good.read_text(encoding='utf-8').startswith('What changed for you:') and not bad.exists()


def test_this_versions_changelog_section_can_be_released():
    assert notes.notes(VERSION).strip()
    found = [notes.HEADING.fullmatch(line) for line in (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8').splitlines()
             if line.startswith('## ')]
    assert found and all(found) and all(match['date'] for match in found)


def release_folder(tmp_path, tag='v1.0.0'):
    folder = tmp_path / 'draft'
    folder.mkdir()
    name = files.release_name(tag)
    (folder / name).write_bytes(b'setup')
    (folder / files.SUMS).write_text(f"{hashlib.sha256(b'setup').hexdigest()}  {name}\n", encoding='utf-8')
    return folder


def test_a_release_holds_setup_and_its_checksum_and_nothing_else(tmp_path):
    folder = release_folder(tmp_path)
    assert [path.name for path in files.check_files(folder, 'v1.0.0')] == ['FelsAltera-Setup-1.0.0.exe', 'SHA256SUMS']
    assert files.release_name('v1.0.0') == setup_name('1.0.0')
    (folder / 'FelsAltera-Setup-1.0.0.exe').write_bytes(b'changed')
    with pytest.raises(files.ReleaseFilesError, match='changed after SHA256SUMS was written'):
        files.check_files(folder, 'v1.0.0')
    (folder / 'extra.txt').write_text('x')
    with pytest.raises(files.ReleaseFilesError, match='not expected extra.txt'):
        files.check_files(folder, 'v1.0.0')
    with pytest.raises(files.ReleaseFilesError, match='not a release tag'):
        files.check_files(folder, '1.0.0')


def test_sha256sums_names_setup_alone(tmp_path):
    folder = release_folder(tmp_path)
    (folder / files.SUMS).write_text(f"{'0' * 64}  other.exe\n", encoding='utf-8')
    with pytest.raises(files.ReleaseFilesError, match='one line'):
        files.check_files(folder, 'v1.0.0')


def test_each_file_must_be_attested_by_this_repositorys_release_workflow(tmp_path):
    ran = []
    paths = files.check_files(release_folder(tmp_path), 'v1.0.0')
    files.check_attestations(paths, 'Fel101/fels-altera', which=lambda name: 'gh',
                             run=lambda command: (ran.append(command), types.SimpleNamespace(returncode=0))[1])
    assert len(ran) == 2 and ran[0][-2:] == ['--signer-workflow', 'Fel101/fels-altera/.github/workflows/release.yml']
    with pytest.raises(files.ReleaseFilesError, match='no attestation'):
        files.check_attestations(paths, 'x/y', run=lambda command: types.SimpleNamespace(returncode=1),
                                 which=lambda name: 'gh')
    with pytest.raises(FileNotFoundError, match='gh'):
        files.check_attestations(paths, 'x/y', which=lambda name: None)


def test_the_release_workflow_tests_builds_drafts_checks_then_publishes():
    text = (WORKFLOWS / 'release.yml').read_text(encoding='utf-8')
    jobs = re.findall(r'^  (\w+):\n', text.split('\njobs:\n', 1)[1], re.MULTILINE)
    assert jobs == ['verify', 'draft', 'check', 'publish']
    assert 'uses: ./.github/workflows/checks.yml' in text
    assert 'workflow_call:' in (WORKFLOWS / 'checks.yml').read_text(encoding='utf-8')
    for needed in ('scripts/release_notes.py --tag', 'scripts/build_installer.py', 'actions/attest@', '--draft',
                   'scripts/check_release_files.py', '--draft=false'):
        assert needed in text, needed
    assert UPDATE_REPOSITORY == 'Fel101/fels-altera'
