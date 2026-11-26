import asyncio
import io
import zipfile

import httpx
import pytest

from app import github
from app.config import positive_setting


class Chunks(httpx.AsyncByteStream):
    async def __aiter__(self):
        for _ in range(31 * 16):
            yield b"x" * 65536


def test_download_over_old_limit_and_cleanup(tmp_path, monkeypatch):
    factory = github.tempfile.SpooledTemporaryFile
    opened = []

    def temporary(**kwargs):
        f = factory(**kwargs, dir=tmp_path)
        opened.append(f)
        return f

    monkeypatch.setattr(github.tempfile, "SpooledTemporaryFile", temporary)
    monkeypatch.setattr(github, "MAX_DOWNLOAD", 200 * 1024 * 1024)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Chunks()))) as client:
            with await github.download(client, "example/repo", "a" * 40) as result:
                result.seek(0, 2)
                assert result.tell() == 31 * 1024 * 1024
            monkeypatch.setattr(github, "MAX_DOWNLOAD", 2 * 1024 * 1024)
            with pytest.raises(github.ScanError, match="2 MB.*SCAN_MAX_DOWNLOAD_MB"):
                await github.download(client, "example/repo", "a" * 40)
        assert all(f.closed for f in opened)

    asyncio.run(run())


def test_file_archive_and_excluded_binaries_do_not_use_text_budget(monkeypatch):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr("root/image.png", b"x" * 1024)
        z.writestr("root/a.txt", "hello")
    stream.seek(0)
    monkeypatch.setattr(github, "MAX_EXPANDED", 10)
    files, coverage = github.read_archive(stream)
    assert files == {"a.txt": "hello"}
    assert coverage.complete


@pytest.mark.parametrize("raw", ["0", "-1", "oops", "1025"])
def test_invalid_limits_rejected_without_printing_value(monkeypatch, raw):
    monkeypatch.setenv("SCAN_MAX_DOWNLOAD_MB", raw)
    with pytest.raises(ValueError, match="SCAN_MAX_DOWNLOAD_MB 必须是"):
        positive_setting("SCAN_MAX_DOWNLOAD_MB", 200, 1024)


def test_limits_read_configuration(monkeypatch):
    for key in ("MAX_DOWNLOAD", "MAX_EXPANDED", "MAX_ENTRIES", "MAX_FILES", "MAX_FILE_BYTES"):
        monkeypatch.setattr(github, key, getattr(github, key))
    monkeypatch.setenv("SCAN_MAX_DOWNLOAD_MB", "350")
    monkeypatch.setenv("SCAN_MAX_EXPANDED_MB", "768")
    github.configure_limits()
    assert github.MAX_DOWNLOAD == 350 * 1024 * 1024
    assert github.MAX_EXPANDED == 768 * 1024 * 1024
