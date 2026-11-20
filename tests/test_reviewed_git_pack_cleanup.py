import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from common.data_contracts import atomic_json,sha256_file
from scripts.cleanup_reviewed_git_packs import cleanup,validate_file


class CleanupTests(unittest.TestCase):
    def fixture(self,root):
        subprocess.run(['git','init','-q',str(root)],check=True)
        p=root/'.git/objects/pack/tmp_pack_AbC123';p.write_bytes(b'incomplete disposable pack')
        os.utime(p,(time.time()-3600,time.time()-3600));s=p.stat()
        row=dict(path=str(p),bytes=s.st_size,allocated_bytes=s.st_blocks*512,device=s.st_dev,inode=s.st_ino,mtime_ns=s.st_mtime_ns,ctime_ns=s.st_ctime_ns)
        plan=root/'proposal.json';atomic_json(plan,dict(root=str(p.parent),candidates=[row],minimum_age_seconds=1800))
        approval=root/'approval.json';atomic_json(approval,dict(authorized=True,proposal=str(plan),proposal_sha256=sha256_file(plan),candidate_count=1))
        return p,row,plan,approval

    def test_real_git_integrity_exact_delete_and_unapproved_garbage_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p,row,plan,approval=self.fixture(root)
            other=p.parent/'tmp_pack_Recent';other.write_bytes(b'not approved')
            blob=subprocess.run(['git','-C',str(root),'hash-object','-w','--stdin'],input=b'preserve ordinary object',capture_output=True,check=True).stdout.decode().strip()
            obj=root/'.git/objects'/blob[:2]/blob[2:];before=sha256_file(obj)
            with patch('scripts.cleanup_reviewed_git_packs.no_writer',return_value=[]):
                result=cleanup(root,plan,approval,root/'execution')
            self.assertEqual(result['deleted_count'],1);self.assertFalse(p.exists());self.assertTrue(other.exists())
            self.assertEqual(sha256_file(obj),before);self.assertTrue(result['references_unchanged'])

    def test_changed_inventory_or_approval_never_deletes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p,row,plan,approval=self.fixture(root);p.write_bytes(b'changed')
            with patch('scripts.cleanup_reviewed_git_packs.no_writer',return_value=[]):
                with self.assertRaisesRegex(ValueError,'changed'):cleanup(root,plan,approval,root/'execution')
            self.assertTrue(p.exists());self.assertTrue((root/'execution/failure.json').exists())
            atomic_json(approval,dict(authorized=False))
            with self.assertRaisesRegex(ValueError,'approval'):cleanup(root,plan,approval,root/'next')
            self.assertTrue(p.exists())

    def test_regular_pack_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p,row,plan,approval=self.fixture(root)
            with self.assertRaisesRegex(ValueError,'scope'):validate_file(dict(row,path=str(p.parent/'pack-regular.pack')),p.parent,1800)
            p.unlink();p.symlink_to(plan)
            with self.assertRaisesRegex(ValueError,'regular'):validate_file(row,p.parent,1800)


if __name__=='__main__':unittest.main()
