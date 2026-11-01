"""Known fictional content registry for future live selections.

Content identity is server-computed SHA256, never a title or client metadata.
This does not authenticate historical import owners, publication or market facts.
"""
from __future__ import annotations
import hashlib
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict

# Audited producer tools/product_document_acceptance.py; no user-editable policy.
KNOWN_TEST_SHA256 = '18148ad136409c15ed833adc91cc9d1694dd16d8f4abd0d391bba2268f908808'
KNOWN_TEST_BYTES = 101
KNOWN_TEST_PRODUCER_SHA256 = 'a9ec7eecacde1b06d4cd39113fc7751bb3c768e720b180f7455ec2a1ccc25b79'
KNOWN_TEST_HASHES = (KNOWN_TEST_SHA256,)
ORIGINAL = re.compile(r'^docbundle://sha256/([a-f0-9]{64})/(original(?:\.[A-Za-z0-9]+))$')

class DocumentProvenanceView(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['document-content-purpose.v1'] = 'document-content-purpose.v1'
    document_id: str
    version_id: str
    original_file_hash: str
    classification: Literal['known_test_content', 'unverified', 'original_unverified']
    original_verified: bool
    known_content_id: Literal['automated-acceptance-fiction.v1'] | None = None
    historical_import_provenance: Literal['unknown'] = 'unknown'

def is_known_test_hash(value: object) -> bool:
    return type(value) is str and value in KNOWN_TEST_HASHES

def _verify_registered_original(version, store) -> None:
    ref = version.original_file_ref
    match = ORIGINAL.fullmatch(ref) if type(ref) is str else None
    if match is None or match[1] != version.original_file_hash:
        raise ValueError('library_test_original_binding_failed')
    # Use the adapter's original path guard and the same original-byte digest
    # contract as DocumentBundleStore.read, with the registered 101B bound.
    path = store.path_for(ref)
    before = path.stat()
    if before.st_size != KNOWN_TEST_BYTES:
        raise ValueError('library_test_original_unverified')
    with path.open('rb') as stream:
        original = stream.read(KNOWN_TEST_BYTES + 1)
    after = store.path_for(ref).stat()
    if ((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)
        != (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)
        or len(original) != KNOWN_TEST_BYTES or hashlib.sha256(original).hexdigest() != KNOWN_TEST_SHA256):
        raise ValueError('library_test_original_unverified')

def provenance_for(version, store) -> DocumentProvenanceView:
    classification = 'unverified'
    verified = False
    if is_known_test_hash(version.original_file_hash):
        try:
            _verify_registered_original(version, store)
        except (OSError, ValueError):
            classification = 'original_unverified'
        else:
            classification = 'known_test_content'; verified = True
    return DocumentProvenanceView(document_id=version.document_id,version_id=version.version_id,
        original_file_hash=version.original_file_hash,classification=classification,original_verified=verified,
        known_content_id='automated-acceptance-fiction.v1' if verified else None)

def require_market_document(version, store) -> None:
    if is_known_test_hash(version.original_file_hash):
        _verify_registered_original(version, store)
        raise ValueError('library_test_material_not_market_evidence')
