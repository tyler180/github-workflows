import copy
import unittest
import bootstrap as b
from test_bootstrap import repository


class Releases:
    def __init__(self, immutable=True, draft=False, annotated=False):
        self.immutable = immutable
        self.draft = draft
        self.annotated = annotated
        self.calls = []

    def call(self, method, path):
        assert method == 'GET'
        self.calls.append(path)
        if '/releases/tags/' in path:
            return {'tag_name': path.rsplit('/', 1)[1], 'draft': self.draft, 'immutable': self.immutable}
        commit = b.SOURCE_SHA if 'github-module/' in path else b.TOOLING_SHA
        return {'object': {'type': 'tag', 'sha': 'c'*40} if self.annotated and '/git/ref/' in path else {'type': 'commit', 'sha': commit}}


class ReleaseReferenceTests(unittest.TestCase):
    def test_full_sha_does_not_need_network(self):
        self.assertEqual(b.resolve_reference(None, 'tyler180/github-module', b.SOURCE_SHA), b.SOURCE_SHA)

    def test_lightweight_and_annotated_immutable_versions(self):
        for annotated in (False, True):
            self.assertEqual(b.resolve_reference(Releases(annotated=annotated), 'tyler180/github-module', 'v0.2.0-rc.1'), b.SOURCE_SHA)

    def test_mutable_and_draft_releases_rejected(self):
        for api in (Releases(immutable=False), Releases(draft=True)):
            with self.assertRaisesRegex(ValueError, 'immutable release'):
                b.resolve_reference(api, 'tyler180/github-module', 'v0.2.0')

    def test_floating_invalid_refs_rejected_before_api(self):
        for ref in ('main', 'v2', 'v2.2', 'latest', 'v01.2.0', 'v2.2.0/other', None):
            api = Releases()
            with self.assertRaises(ValueError):
                b.resolve_reference(api, 'tyler180/github-module', ref)
            self.assertFalse(api.calls)

    def test_ref_migration_preserves_normalized_config_and_requested_names(self):
        old = repository()
        new = copy.deepcopy(old)
        c = new['bootstrap']
        for name in ('source', 'workflow', 'hook'):
            c.pop(name + '_sha')
            c[name + '_ref'] = 'v0.2.0' if name == 'source' else 'v2.2.0-rc.1'
        before = b.profiles({'schema_version': 1, 'repositories': {'pilot': old}}, 'owner')[0][1]
        after = b.profiles({'schema_version': 1, 'repositories': {'pilot': new}}, 'owner', Releases())[0][1]
        self.assertEqual(before['bootstrap'], after['bootstrap'])
        self.assertEqual(after['_bootstrap_refs']['source_ref'], 'v0.2.0')
        self.assertIn('source_ref', new['bootstrap'])

    def test_mixed_aliases_and_unapproved_release_commit_rejected(self):
        repo = repository()
        repo['bootstrap']['source_ref'] = 'v0.2.0'
        with self.assertRaises(ValueError):
            b.profiles({'schema_version': 1, 'repositories': {'pilot': repo}}, 'owner', Releases())
        class WrongCommit(Releases):
            def call(self, method, path):
                value = super().call(method, path)
                if 'object' in value:
                    value['object']['sha'] = 'd' * 40
                return value
        repo = repository()
        c = repo['bootstrap']
        for name in ('source', 'workflow', 'hook'):
            c[name + '_ref'] = 'v0.2.0'
            c.pop(name + '_sha')
        with self.assertRaisesRegex(ValueError, 'not approved'):
            b.profiles({'schema_version': 1, 'repositories': {'pilot': repo}}, 'owner', WrongCommit())
