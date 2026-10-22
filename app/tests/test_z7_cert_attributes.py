"""Certificate attributes (backlog row 25): key usages and extended key usages on a request."""
import pytest
from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID as EKU

from labape import certs


def test_the_default_needs_no_template_and_the_rest_needs_the_switch():
    assert certs.check_uses([], False) == [] and certs.check_uses(["server_auth", "client_auth"], False) == ["server_auth", "client_auth"]
    assert certs.sign_vars([]) is None and certs.sign_vars(["client_auth", "server_auth"]) is None
    with pytest.raises(certs.CertError, match="turns them on"):
        certs.check_uses(["code_signing"], False)
    with pytest.raises(certs.CertError, match="turns them on"):
        certs.check_uses(["server_auth"], False)            # only server auth is not what the CA gives by default
    with pytest.raises(certs.CertError, match="unknown attribute"):
        certs.check_uses(["nope"], True)
    assert certs.check_uses(["signing", "client_auth", "client_auth"], True) == ["client_auth", "signing"]


def test_the_template_data():
    assert certs.sign_vars(["client_auth", "signing"]) == {"eku": ["clientAuth"], "ku": ["digitalSignature", "contentCommitment"]}
    assert certs.sign_vars(["code_signing"]) == {"eku": ["codeSigning"], "ku": ["digitalSignature"]}


def test_the_csr_carries_the_usages_and_uses_are_read_back_from_a_certificate():
    key_pem, csr_pem = certs.make_request("a.lab.test", ["a.lab.test"], [], ["client_auth", "code_signing", "signing"])
    csr = x509.load_pem_x509_csr(csr_pem.encode())
    assert set(csr.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value) == {EKU.CLIENT_AUTH, EKU.CODE_SIGNING}
    # a certificate with those extensions reads back as the same attributes
    import datetime as dt
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "a.lab.test")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(7).not_valid_before(now).not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.ExtendedKeyUsage([EKU.CLIENT_AUTH, EKU.CODE_SIGNING]), False)
            .add_extension(x509.KeyUsage(True, True, False, False, False, False, False, False, False), True)
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    assert certs.uses_of(cert) == ["client_auth", "code_signing", "signing"]
    certs.verify_uses(pem, ["client_auth", "code_signing", "signing"])
    with pytest.raises(certs.CertError, match="did not apply"):
        certs.verify_uses(pem, ["email_protection"], )
