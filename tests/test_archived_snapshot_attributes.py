"""Bounded full SHA re-read for observed NFS post-write attribute refresh."""
import hashlib
import pytest
from scripts.audit_main_training import Snapshot
from scripts.freeze_independent_prefix import ArchivedSnapshot


@pytest.mark.parametrize('fault',['one_refresh','repeated_refresh','changed_bytes','replaced_inode'])
def test_attribute_refresh_does_not_skip_content_or_identity_check(tmp_path,monkeypatch,fault):
    source=str(tmp_path/'original');blob=hashlib.sha256(source.encode()).hexdigest()
    (tmp_path/'blobs').mkdir();p=tmp_path/'blobs'/blob;p.write_bytes(b'proof')
    digest=hashlib.sha256(b'proof').hexdigest()
    manifest={'files':{source:dict(blob=blob,bytes=5,sha256=digest)}}
    original=Snapshot.stamp(p);count=0
    def stamp(path):
        nonlocal count
        count+=1;s=list(original)
        if count>1:s[-2]+=7000;s[-1]+=7000
        if fault=='repeated_refresh' and count>2:s[-1]+=7000
        if fault=='replaced_inode' and count>1:s[1]+=1
        if fault=='changed_bytes' and count==2:p.write_bytes(b'wrong')
        return tuple(s)
    monkeypatch.setattr(Snapshot,'stamp',staticmethod(stamp))
    snap=ArchivedSnapshot(tmp_path,manifest)
    if fault=='one_refresh':
        assert snap.raw(source)==b'proof' and count==3
    else:
        with pytest.raises(ValueError,match='Archived evidence changed'):snap.raw(source)
        assert count<=3
