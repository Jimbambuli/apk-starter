"""APK Signature Scheme v2 check (standard library only): the signer's certificate and the RSA signature over the
digests of the APK's contents, as apksigner verifies them. Used for downloads whose signer is known in advance
(Aurora Store from F-Droid), so that a changed or exchanged file is refused.

https://source.android.com/docs/security/features/apksigning/v2"""
import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path

from .errors import ApkError
from .i18n import _

MAGIC = b"APK Sig Block 42"
V2_ID = 0x7109871A
CHUNK = 1 << 20
# signature algorithm ids: RSASSA-PKCS1-v1_5 with SHA2-256 / SHA2-512 (others are not accepted here)
RSA_PKCS1 = {0x0103: ("sha256", bytes.fromhex("3031300d060960864801650304020105000420")),
             0x0104: ("sha512", bytes.fromhex("3051300d060960864801650304020305000440"))}
DIGEST_OF = {0x0103: 0x0103, 0x0104: 0x0104}
MAX_APK = 2 << 30


@dataclass(frozen=True)
class Signer:
    cert_sha256: str


def _bad(reason: str) -> ApkError:
    return ApkError(_("the signature of the APK is not valid: {reason}").format(reason=reason))


def _lp(b: bytes, o: int) -> tuple[bytes, int]:
    """A length-prefixed (uint32 little endian) item at offset o."""
    if o + 4 > len(b):
        raise _bad("truncated")
    n = struct.unpack_from("<I", b, o)[0]
    if o + 4 + n > len(b):
        raise _bad("truncated")
    return b[o + 4:o + 4 + n], o + 4 + n


def _seq(b: bytes) -> list[bytes]:
    items, o = [], 0
    while o < len(b):
        item, o = _lp(b, o)
        items.append(item)
    return items


def _der(b: bytes, o: int) -> tuple[int, int, int]:
    """(tag, start of content, end of content) of the DER element at o."""
    tag = b[o]
    n = b[o + 1]
    o += 2
    if n & 0x80:
        k = n & 0x7F
        if k == 0 or k > 4:
            raise _bad("certificate")
        n = int.from_bytes(b[o:o + k], "big")
        o += k
    if o + n > len(b):
        raise _bad("certificate")
    return tag, o, o + n


def _children(b: bytes, start: int, end: int) -> list[tuple[int, int, int]]:
    out, o = [], start
    while o < end:
        tag, s, e = _der(b, o)
        out.append((tag, s, e))
        o = e
    return out


RSA_OID = bytes.fromhex("2a864886f70d010101")


def _der_start(b: bytes, start: int, index: int) -> int:
    """Offset of the header of the index-th child (after an optional [0] version) of the element content at start."""
    o = start
    first = True
    k = 0
    while True:
        tag, _s, e = _der(b, o)
        if first and tag == 0xA0:
            o, first = e, False
            continue
        first = False
        if k == index:
            return o
        o, k = e, k + 1


def rsa_public_key(cert: bytes, spki: list[bytes] | None = None) -> tuple[int, int]:
    """(modulus, exponent) from an X.509 certificate with an RSA key."""
    try:
        _t, s, e = _der(cert, 0)
        tbs_tag, ts, te = _children(cert, s, e)[0]
        fields = _children(cert, ts, te)
        if fields and fields[0][0] == 0xA0:             # [0] version
            fields = fields[1:]
        _t, ss, se = fields[5]                          # serial, algorithm, issuer, validity, subject, SPKI
        if spki is not None:
            spki.append(cert[_der_start(cert, ts, 5):se])
        alg, key = _children(cert, ss, se)
        _t, as_, ae = alg
        oid = _children(cert, as_, ae)[0]
        if cert[oid[1]:oid[2]] != RSA_OID:
            raise _bad("not an RSA key")
        _t, ks, ke = key                                # BIT STRING: unused-bits byte, then RSAPublicKey
        rsa = cert[ks + 1:ke]
        _t, rs, re_ = _der(rsa, 0)
        (_t1, ns, ne), (_t2, es, ee) = _children(rsa, rs, re_)
        return int.from_bytes(rsa[ns:ne], "big"), int.from_bytes(rsa[es:ee], "big")
    except (IndexError, ValueError):
        raise _bad("certificate") from None


def _verify_rsa(cert: bytes, algorithm: int, message: bytes, signature: bytes) -> None:
    hash_name, prefix = RSA_PKCS1[algorithm]
    n, e = rsa_public_key(cert)
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        raise _bad("signature length")
    em = pow(int.from_bytes(signature, "big"), e, n).to_bytes(k, "big")
    t = prefix + hashlib.new(hash_name, message).digest()
    if k < len(t) + 11 or em != b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t:
        raise _bad("RSA signature")


def _content_digest(data: memoryview, sections: list[tuple[int, int]], cd_offset_fix: tuple[int, int],
                    hash_name: str) -> bytes:
    """The v2 top-level digest: each section in 1 MiB chunks (0xa5, length, data), then 0x5a, count, chunk digests.
    The offset of the central directory in the EOCD counts as the start of the signing block."""
    digests = []
    eocd_start, sig_block_start = cd_offset_fix
    for start, end in sections:
        for o in range(start, end, CHUNK):
            chunk = bytes(data[o:min(o + CHUNK, end)])
            if start == eocd_start:
                chunk = chunk[:16] + struct.pack("<I", sig_block_start) + chunk[20:]
            digests.append(hashlib.new(hash_name, b"\xa5" + struct.pack("<I", len(chunk)) + chunk).digest())
    return hashlib.new(hash_name, b"\x5a" + struct.pack("<I", len(digests)) + b"".join(digests)).digest()


def verify(path: Path) -> Signer:
    """Checks the v2 signature (one signer, RSA) and returns the signer; raises ApkError if it is missing or wrong."""
    data = Path(path).read_bytes()
    if len(data) > MAX_APK:
        raise _bad("too large")
    eocd = data.rfind(b"PK\x05\x06", max(0, len(data) - 0x10000 - 22))
    if eocd < 0 or eocd + 22 > len(data):
        raise _bad("not a ZIP file")
    cd_offset = struct.unpack_from("<I", data, eocd + 16)[0]
    if cd_offset < 32 or data[cd_offset - 16:cd_offset] != MAGIC:
        raise _bad("no APK signing block")
    size = struct.unpack_from("<Q", data, cd_offset - 24)[0]
    block_start = cd_offset - size - 8
    if block_start < 0 or struct.unpack_from("<Q", data, block_start)[0] != size:
        raise _bad("APK signing block")
    o, v2 = block_start + 8, None
    while o < cd_offset - 24:
        n, pid = struct.unpack_from("<QI", data, o)
        if n < 4 or o + 8 + n > cd_offset - 24:
            raise _bad("APK signing block")
        if pid == V2_ID:
            v2 = data[o + 12:o + 8 + n]
        o += 8 + n
    if v2 is None:
        raise _bad("no v2 signature")
    signers = _seq(_lp(v2, 0)[0])
    if len(signers) != 1:
        raise _bad("not exactly one signer")
    signed_data, o = _lp(signers[0], 0)
    signatures, o = _lp(signers[0], o)
    public_key, _o = _lp(signers[0], o)
    digests_seq, o = _lp(signed_data, 0)
    certs = _seq(_lp(signed_data, o)[0])
    if not certs:
        raise _bad("no certificate")
    cert = certs[0]
    best = None
    for sig in _seq(signatures):
        alg = struct.unpack_from("<I", sig, 0)[0]
        if alg in RSA_PKCS1:
            best = (alg, _lp(sig, 4)[0])
    if best is None:
        raise _bad("no supported signature algorithm")
    alg, signature = best
    spki: list[bytes] = []
    rsa_public_key(cert, spki)
    if spki[0] != public_key:
        raise _bad("the public key is not the certificate's")
    _verify_rsa(cert, alg, signed_data, signature)
    want = None
    for d in _seq(digests_seq):
        if struct.unpack_from("<I", d, 0)[0] == DIGEST_OF[alg]:
            want = _lp(d, 4)[0]
    if want is None:
        raise _bad("no content digest")
    view = memoryview(data)
    got = _content_digest(view, [(0, block_start), (cd_offset, eocd), (eocd, len(data))], (eocd, block_start),
                          RSA_PKCS1[alg][0])
    if got != want:
        raise _bad("the contents were changed")
    return Signer(hashlib.sha256(cert).hexdigest())


V3_ID, V31_ID = 0xF05368C0, 0x1B93AD61


def _signing_block(f) -> dict[int, bytes]:
    """The id-value pairs of the APK signing block, read by seeking (big APKs are not read whole)."""
    f.seek(0, 2)
    end = f.tell()
    f.seek(max(0, end - 0x10000 - 22))
    tail = f.read()
    eocd = tail.rfind(b"PK\x05\x06")
    if eocd < 0 or eocd + 22 > len(tail):
        return {}
    cd_offset = struct.unpack_from("<I", tail, eocd + 16)[0]
    if cd_offset < 32:
        return {}
    f.seek(cd_offset - 24)
    foot = f.read(24)
    if foot[8:] != MAGIC:
        return {}
    size = struct.unpack_from("<Q", foot, 0)[0]
    if size > cd_offset or size > MAX_APK:
        return {}
    f.seek(cd_offset - size - 8)
    block = f.read(size - 16)
    pairs, o = {}, 8
    while o + 12 <= len(block):
        n, pid = struct.unpack_from("<QI", block, o)
        if n < 4 or o + 8 + n > len(block):
            break
        pairs[pid] = block[o + 12:o + 8 + n]
        o += 8 + n
    return pairs


def _block_signers(value: bytes) -> list[bytes]:
    """The first certificate of each signer of a v2/v3 block (the signer's own; the rest is its chain)."""
    out = []
    for signer in _seq(_lp(value, 0)[0]):
        signed_data = _lp(signer, 0)[0]
        o = _lp(signed_data, 0)[1]              # digests
        certs = _seq(_lp(signed_data, o)[0])
        if certs:
            out.append(certs[0])
    return out


def _pkcs7_certs(der: bytes) -> list[bytes]:
    """Certificates of a v1 signature file (PKCS #7 SignedData, [0] certificates)."""
    _t, s, e = _der(der, 0)
    content = _children(der, s, e)              # contentType, [0] content
    if len(content) < 2 or content[1][0] != 0xA0:
        return []
    _t, s, e = _der(der, content[1][1])         # SignedData
    for tag, cs, ce in _children(der, s, e):
        if tag == 0xA0:
            out, o = [], cs
            while o < ce:
                end = _der(der, o)[2]
                out.append(der[o:end])
                o = end
            return out
    return []


def signers(path: Path) -> frozenset[str]:
    """SHA-256 of the signing certificates, as Android compares an update with the installed app: from the v3
    block, else v2, else the v1 signature files; empty if the APK is not signed. Only read, not verified."""
    import zipfile
    try:
        with open(path, "rb") as f:
            pairs = _signing_block(f)
        for pid in (V3_ID, V2_ID):
            if pid in pairs:
                return frozenset(hashlib.sha256(c).hexdigest() for c in _block_signers(pairs[pid]))
        with zipfile.ZipFile(path) as z:
            certs = []
            for name in z.namelist():
                up = name.upper()
                if up.startswith("META-INF/") and up.count("/") == 1 and up.endswith((".RSA", ".DSA", ".EC")):
                    found = _pkcs7_certs(z.read(name))
                    certs += found[:1]
            return frozenset(hashlib.sha256(c).hexdigest() for c in certs)
    except (ApkError, IndexError, struct.error, ValueError, zipfile.BadZipFile) as e:
        raise _bad(str(e) or "signing block") from None
