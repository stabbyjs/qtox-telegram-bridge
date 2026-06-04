import pytest

from bridge.file_transfer import (
    IncomingTransfer,
    OutgoingTransfer,
    TransferTooLarge,
    is_image,
    safe_filename,
    unique_path,
)


def test_is_image_by_extension():
    assert is_image("photo.JPG")
    assert is_image("a.png")
    assert not is_image("doc.pdf")
    assert not is_image("noext")


def test_safe_filename_strips_path():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("/abs/path/file.txt") == "file.txt"


def test_safe_filename_posix_keeps_backslashes_as_unsafe():
    # на POSIX обратный слэш не разделитель пути - он заменяется на '_'
    assert safe_filename("..\\..\\win.ini") == "_.._win.ini"


def test_safe_filename_empty_and_dots():
    assert safe_filename("") == "file.bin"
    assert safe_filename("...") == "file.bin"
    assert safe_filename(".hidden") == "hidden"


def test_safe_filename_replaces_unsafe_chars():
    out = safe_filename("a b;rm -rf.txt")
    assert "/" not in out and " " not in out and ";" not in out


def test_unique_path_is_unique(tmp_path):
    a = unique_path(str(tmp_path), "photo.jpg")
    b = unique_path(str(tmp_path), "photo.jpg")
    assert a != b
    assert a.endswith("_photo.jpg") and b.endswith("_photo.jpg")


def test_incoming_writes_and_completes(tmp_path):
    dest = tmp_path / "out.bin"
    t = IncomingTransfer(size=6, tmp_path=str(dest), max_bytes=1000)
    t.feed(0, b"abc")
    assert not t.is_complete
    t.feed(3, b"def")
    assert t.is_complete
    t.finish()
    assert dest.read_bytes() == b"abcdef"


def test_incoming_zero_length_chunk_completes(tmp_path):
    dest = tmp_path / "out.bin"
    t = IncomingTransfer(size=0, tmp_path=str(dest), max_bytes=1000)
    t.feed(0, b"hello")
    t.feed(5, b"")
    assert t.is_complete
    t.finish()
    assert dest.read_bytes() == b"hello"


def test_incoming_rejects_oversize_by_length(tmp_path):
    t = IncomingTransfer(size=10_000, tmp_path=str(tmp_path / "o.bin"), max_bytes=5)
    with pytest.raises(TransferTooLarge):
        t.feed(0, b"abcdef")


def test_incoming_rejects_far_position_sparse_attack(tmp_path):
    # SEC-003: маленький кусок на огромном смещении не должен пройти
    t = IncomingTransfer(size=0, tmp_path=str(tmp_path / "o.bin"), max_bytes=1000)
    with pytest.raises(TransferTooLarge):
        t.feed(2**40, b"x")


def test_incoming_rejects_negative_position(tmp_path):
    t = IncomingTransfer(size=0, tmp_path=str(tmp_path / "o.bin"), max_bytes=1000)
    with pytest.raises(TransferTooLarge):
        t.feed(-1, b"x")


def test_incoming_cleanup_removes_file(tmp_path):
    dest = tmp_path / "out.bin"
    t = IncomingTransfer(size=3, tmp_path=str(dest), max_bytes=1000)
    t.feed(0, b"abc")
    t.finish()
    assert dest.exists()
    t.cleanup()
    assert not dest.exists()


def test_outgoing_reads_chunks(tmp_path):
    src = tmp_path / "in.bin"
    src.write_bytes(b"0123456789")
    t = OutgoingTransfer(path=str(src))
    assert t.size == 10
    assert t.read_chunk(0, 4) == b"0123"
    assert t.read_chunk(8, 4) == b"89"
    assert t.read_chunk(3, 0) == b""
    t.close()
