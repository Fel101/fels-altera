"""The suite cannot discover and unload an installed personal model service."""
from core import fels_suite, llm


def test_fake_picture_pipeline_cannot_reach_lm_studio_or_ollama(monkeypatch, tmp_path):
    # Even an available CLI directory is ineligible to default test discovery.
    binary = tmp_path / 'lms.exe'
    binary.write_bytes(b'synthetic unexecutable CLI marker')
    monkeypatch.setenv('PATH', str(tmp_path))
    def forbidden(*args, **kwargs):
        raise AssertionError('A test attempted actual provider control')
    monkeypatch.setattr(fels_suite.subprocess, 'run', forbidden)
    monkeypatch.setattr(fels_suite.http.client, 'HTTPConnection', forbidden)
    assert fels_suite._find_lms() is None
    assert llm.unload_models() is False
