"""APK Signature Scheme v2 check against a real signed APK (Aurora Store from F-Droid, path in
APK_STARTER_SIGNED_APK; skipped without it) and against changed copies of it."""
import os
import shutil
from pathlib import Path

import pytest

from apk_starter import apksig
from apk_starter.errors import ApkError

SIGNED = os.environ.get("APK_STARTER_SIGNED_APK", "")
AURORA_SIGNER = "5c83c7672b929955dc0a1db89a5e6ae4389e2eae7ec939956041694e5815f532"

needs_apk = pytest.mark.skipif(not os.path.isfile(SIGNED), reason="no signed APK to check against")


@needs_apk
def test_real_apk_signer():
    assert apksig.verify(Path(SIGNED)).cert_sha256 == AURORA_SIGNER


def _changed(tmp_path: Path, where: float) -> Path:
    p = tmp_path / "x.apk"
    shutil.copyfile(SIGNED, p)
    data = bytearray(p.read_bytes())
    i = int(len(data) * where)
    data[i] ^= 0xFF
    p.write_bytes(bytes(data))
    return p


@needs_apk
@pytest.mark.parametrize("where", [0.01, 0.5])
def test_changed_contents_refused(tmp_path, where):
    with pytest.raises(ApkError):
        apksig.verify(_changed(tmp_path, where))


@needs_apk
def test_changed_central_directory_refused(tmp_path):
    with pytest.raises(ApkError):
        apksig.verify(_changed(tmp_path, 0.9995))


def test_unsigned_zip_refused(tmp_path):
    import zipfile
    p = tmp_path / "u.apk"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("AndroidManifest.xml", b"x")
    with pytest.raises(ApkError):
        apksig.verify(p)


def test_not_a_zip_refused(tmp_path):
    p = tmp_path / "n.apk"
    p.write_bytes(b"hello")
    with pytest.raises(ApkError):
        apksig.verify(p)


# -- the signer an update is compared by (Android: INSTALL_FAILED_UPDATE_INCOMPATIBLE) ---------------------------
from apkgen import axml_manifest, fake_cert, make_apk, pkcs7, sign_block  # noqa: E402
import hashlib  # noqa: E402


def _apk(tmp_path, name):
    return make_apk(tmp_path / f"{name}.apk", manifest=axml_manifest())


@pytest.mark.parametrize("scheme", [2, 3])
def test_signers_from_the_signing_block(tmp_path, scheme):
    cert = fake_cert("A")
    p = sign_block(_apk(tmp_path, "a"), cert, scheme)
    assert apksig.signers(p) == frozenset({hashlib.sha256(cert).hexdigest()})


def test_signers_of_a_v1_signature(tmp_path):
    cert = fake_cert("B")
    p = make_apk(tmp_path / "b.apk", manifest=axml_manifest(), files={"META-INF/CERT.RSA": pkcs7(cert)})
    assert apksig.signers(p) == frozenset({hashlib.sha256(cert).hexdigest()})


def test_unsigned_has_no_signers(tmp_path):
    assert apksig.signers(_apk(tmp_path, "u")) == frozenset()


@needs_apk
def test_signers_of_a_real_apk():
    assert apksig.signers(Path(SIGNED)) == frozenset({AURORA_SIGNER})
