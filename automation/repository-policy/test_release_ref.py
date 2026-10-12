import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('release_ref',Path(__file__).with_name('release_ref.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
SHA='a'*40

class ReleaseTests(unittest.TestCase):
    def test_full_sha_still_supported(self):
        with patch.object(r,'run',return_value=SHA): self.assertEqual(r.resolve(SHA,'.'),SHA)
    def test_wrong_sha_rejected(self):
        with patch.object(r,'run',return_value='b'*40):
            with self.assertRaises(ValueError): r.resolve(SHA,'.')
    def test_branches_floating_tags_and_injection_rejected(self):
        for ref in ['main','v1','v2.1','v2.1.0;echo unsafe','../v1.0.0','v01.0.0']:
            with self.subTest(ref=ref),self.assertRaises(ValueError):r.resolve(ref,'.')
    def responses(self,immutable=True,draft=False,tag_sha=SHA):
        return [SHA,json.dumps({'tag_name':'v2.1.0-rc.2','draft':draft,'immutable':immutable}),json.dumps({'object':{'type':'commit','sha':tag_sha}})]
    def test_immutable_release_resolves(self):
        with patch.object(r,'run',side_effect=self.responses()):self.assertEqual(r.resolve('v2.1.0-rc.2','.'),SHA)
    def test_mutable_and_draft_releases_rejected(self):
        for immutable,draft in [(False,False),(True,True)]:
            with patch.object(r,'run',side_effect=self.responses(immutable,draft)):
                with self.assertRaises(ValueError):r.resolve('v2.1.0-rc.2','.')
    def test_tag_checkout_mismatch_rejected(self):
        with patch.object(r,'run',side_effect=self.responses(tag_sha='b'*40)):
            with self.assertRaises(ValueError):r.resolve('v2.1.0-rc.2','.')
    def test_annotated_tag_resolves_to_commit(self):
        responses=self.responses();responses[2]=json.dumps({'object':{'type':'tag','sha':'c'*40}})
        responses.append(json.dumps({'object':{'type':'commit','sha':SHA}}))
        with patch.object(r,'run',side_effect=responses):self.assertEqual(r.resolve('v2.1.0-rc.2','.'),SHA)

if __name__=='__main__':unittest.main()
