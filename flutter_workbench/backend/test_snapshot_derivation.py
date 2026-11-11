import copy,json,hashlib,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import sync_source as sync
from snapshot_derivation import transplant,replay

class SnapshotDerivationTests(unittest.TestCase):
 def test_exact_function_delta_leaves_unrelated_retained_code(self):
  base='MODE = "retained"\n\ndef entry():\n    return 1\n'
  before=base.replace('retained','parallel')
  after=before.replace('return 1','return helper()')+'\ndef helper():\n    return 2\n'
  out=transplant(base,before,after,['entry','helper'])
  self.assertIn('MODE = "retained"',out);ns={};exec(out,ns);self.assertEqual(ns['entry'](),2)
  for changed in (base.replace('return 1','return 3'),base+'\ndef helper():\n    return 9\n'):
   with self.assertRaises(ValueError):transplant(changed,before,after,['entry','helper'])
 def test_duplicate_decorated_missing_and_unchanged_functions_are_rejected(self):
  before='def entry():\n    return 1\n';after=before.replace('1','2')
  for names in ([],['entry','entry'],['missing']):
   with self.assertRaises(ValueError):transplant(before,before,after,names)
  with self.assertRaises(ValueError):transplant(before,before,before,['entry'])
  with self.assertRaises(ValueError):transplant('@wrapped\n'+before,before,after,['entry'])
 def test_verified_provenance_adoption_and_tampering_guards(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)/'backend';source=Path(tmp)/'source';source.mkdir();root.mkdir();name='cam_core/example.py'
   texts=['MODE="old"\ndef entry():\n    return 1\n','MODE="parallel"\ndef entry():\n    return 1\n','MODE="parallel"\ndef entry():\n    return 2\n']
   identities=['a'*16,'b'*16,'c'*16];records=[]
   for identity,text in zip(identities,texts):
    folder=root/'snapshots'/identity/'core';(folder/'cam_core').mkdir(parents=True);(folder/name).write_text(text,encoding='utf-8')
    r=dict(snapshot_id=identity,snapshot_directory=f'snapshots/{identity}/core',files={name:sync.digest(folder/name)})
    (folder.parent/'manifest.json').write_text(json.dumps(r),encoding='utf-8');records.append(r)
   result=transplant(*texts,['entry']);identity='d'*16;folder=root/'snapshots'/identity/'core';(folder/'cam_core').mkdir(parents=True);(folder/name).write_bytes(result.encode('utf-8'))
   r=dict(snapshot_id=identity,snapshot_directory=f'snapshots/{identity}/core',source=str(source),files={name:sync.digest(folder/name)},source_files=records[2]['files'],ui_source_files={},retained_files={},derived_files={name:dict(base_snapshot=identities[0],upstream_before=identities[1],source_snapshot=identities[2],functions=['entry'])})
   manifest=folder.parent/'manifest.json';manifest.write_text(json.dumps(r),encoding='utf-8');(root/'source_manifest.json').write_text(json.dumps(records[0]),encoding='utf-8')
   self.assertEqual(replay(root,r,name),result.encode())
   with patch.object(sync,'ROOT',root),patch.object(sync,'inventory',return_value=records[2]['files']),patch.object(sync,'ui_inventory',return_value={}):
    self.assertEqual(sync.adopt(manifest)['adopted'],identity)
    # Even updated candidate hashes cannot conceal arbitrary edits outside copied functions.
    bad=copy.deepcopy(r);(folder/name).write_text(result.replace('old','tampered'),encoding='utf-8');bad['files'][name]=sync.digest(folder/name)
    with self.assertRaisesRegex(ValueError,'replayed'):sync.verify(bad)
    (folder/name).write_bytes(result.encode('utf-8'))
    (root/records[1]['snapshot_directory']/name).write_text('tampered input',encoding='utf-8')
    with self.assertRaisesRegex(ValueError,'input hash'):sync.verify(r)

if __name__=='__main__':unittest.main()
