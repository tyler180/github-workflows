import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('promote', BASE / 'automation/promote.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        (self.repo / 'infrastructure').mkdir()
        (self.repo / 'infrastructure/kustomization.yaml').write_text(
            'apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources:\n  - existing.yaml\n')
        os.environ['GITOPS_REPOSITORY'] = 'tyler180/talos-gitops'
        self.image = 'ghcr.io/tyler180/jobtracker:v0.1.0@sha256:' + 'a' * 64

    def promote(self, image=None):
        module.promote(BASE / 'automation/tests/fixtures/deploy', self.repo, 'jobtracker', 'jobtracker', image or self.image)

    def test_first_release_and_idempotence(self):
        self.promote()
        registration = yaml.safe_load((self.repo / 'infrastructure/applications/jobtracker.yaml').read_text())
        self.assertNotIn('automated', registration['spec']['syncPolicy'])
        self.assertEqual(registration['spec']['source']['path'], 'applications/jobtracker')
        root = self.repo / 'infrastructure/kustomization.yaml'
        first = root.read_text()
        self.promote()
        self.assertEqual(root.read_text(), first)
        self.assertEqual(yaml.safe_load(first)['resources'], ['existing.yaml', 'applications/jobtracker.yaml'])

    def test_update_preserves_cluster_settings_and_storage(self):
        self.promote()
        app = self.repo / 'applications/jobtracker/app.yaml'
        docs = list(yaml.safe_load_all(app.read_text()))
        deployment = next(d for d in docs if d['kind'] == 'Deployment')
        deployment['spec']['template']['spec']['containers'][0]['resources']['limits']['memory'] = '512Mi'
        app.write_text(yaml.safe_dump_all(docs, sort_keys=False))
        pvc = next(d for d in docs if d['kind'] == 'PersistentVolumeClaim')
        image = 'ghcr.io/tyler180/jobtracker:v0.1.1@sha256:' + 'b' * 64
        self.promote(image)
        updated = list(yaml.safe_load_all(app.read_text()))
        container = next(d for d in updated if d['kind'] == 'Deployment')['spec']['template']['spec']['containers'][0]
        self.assertEqual(container['image'], image)
        self.assertEqual(container['resources']['limits']['memory'], '512Mi')
        self.assertEqual(next(d for d in updated if d['kind'] == 'PersistentVolumeClaim'), pvc)

    def test_rejects_namespace_change(self):
        self.promote()
        with self.assertRaises(ValueError):
            module.promote(BASE / 'automation/tests/fixtures/deploy', self.repo, 'jobtracker', 'other', self.image)

    def test_rejects_unpinned_image_and_path_traversal(self):
        with self.assertRaises(ValueError):
            self.promote('ghcr.io/tyler180/jobtracker:latest')
        with self.assertRaises(ValueError):
            module.promote(BASE / 'automation/tests/fixtures/deploy', self.repo, '../escape', 'jobtracker', self.image)

    def test_embedded_workflow_matches_tested_script(self):
        loader = yaml.BaseLoader
        workflow = yaml.load((BASE / '.github/workflows/reusable-release.yaml').read_text(), Loader=loader)
        step = next(s for s in workflow['jobs']['release']['steps'] if s.get('name') == 'Prepare GitOps proposal')
        embedded = step['run'].split("python3 - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
        self.assertEqual(embedded.rstrip(), (BASE / 'automation/promote.py').read_text().rstrip())


if __name__ == '__main__':
    unittest.main()
