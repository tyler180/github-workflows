import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'configure-go.py'
spec = importlib.util.spec_from_file_location('configure_go', SCRIPT)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class ConfigureGoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'go.mod').write_text('module example.com/fixture\n\ngo 1.20\n')

    def command(self, directory, source='package main\nfunc main() {}\n'):
        path = self.root / directory
        path.mkdir(parents=True, exist_ok=True)
        (path / 'main.go').write_text(source)

    def test_root_and_nested_discovery_ignore_comments_and_tests(self):
        self.command('.')
        self.command('cmd/server')
        lib = self.root / 'pkg/lib'
        lib.mkdir(parents=True)
        (lib / 'lib.go').write_text('package lib\n// func main() {}\n')
        (lib / 'lib_test.go').write_text('package lib\nfunc main() {}\n')
        self.assertEqual(tool.discover(self.root), ['.', './cmd/server'])

    def test_single_command_never_calls_ai(self):
        with patch.object(tool, 'ai_select', side_effect=AssertionError('unexpected API call')):
            self.assertEqual(tool.select(['./cmd/server'], ai=True)[0], './cmd/server')

    def test_multiple_requires_selection(self):
        with self.assertRaisesRegex(ValueError, 'Multiple executables'):
            tool.select(['./cmd/server', './cmd/migrate'])
        with self.assertRaisesRegex(ValueError, 'discovered'):
            tool.select(['./cmd/server'], explicit='./other')

    def test_ai_selection_and_uncertainty(self):
        candidates = ['./cmd/server', './cmd/migrate']
        with patch.object(tool, 'ai_select', return_value={'main_package': candidates[0], 'reason': 'README'}):
            self.assertEqual(tool.select(candidates, ai=True)[0], candidates[0])
        with patch.object(tool, 'ai_select', return_value={'main_package': None, 'reason': 'Unclear'}):
            with self.assertRaisesRegex(ValueError, 'could not identify'):
                tool.select(candidates, ai=True)

    def response(self, selection=None, status='completed', refusal=False):
        if selection is None:
            selection = {'main_package': './cmd/server', 'reason': 'README identifies server'}
        content = [{'type': 'refusal', 'refusal': 'No'}] if refusal else [
            {'type': 'output_text', 'text': json.dumps(selection)}]
        return {'status': status, 'output': [{'type': 'message', 'content': content}]}

    def test_api_schema_and_invalid_responses(self):
        from io import BytesIO
        candidates = ['./cmd/server', './cmd/migrate']
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-only'}):
            for data, succeeds in [(self.response(), True),
                                   (self.response({'main_package': './invented', 'reason': 'No'}), False),
                                   (self.response(status='incomplete'), False),
                                   (self.response(refusal=True), False)]:
                with self.subTest(data=data), patch.object(tool.urllib.request, 'urlopen',
                                                           return_value=BytesIO(json.dumps(data).encode())) as call:
                    if succeeds:
                        self.assertEqual(tool.ai_select(candidates, {}, 'fixture-model')['main_package'], candidates[0])
                        payload = json.loads(call.call_args.args[0].data)
                        self.assertFalse(payload['store'])
                        self.assertTrue(payload['text']['format']['strict'])
                        self.assertEqual(payload['text']['format']['schema']['properties']['main_package']['enum'], candidates + [None])
                    else:
                        with self.assertRaises(ValueError):
                            tool.ai_select(candidates, {}, 'fixture-model')

    def test_preview_builds_without_writing_and_write_is_idempotent(self):
        self.command('cmd/server')
        self.assertEqual(tool.main(['--repository', str(self.root)]), 0)
        self.assertFalse((self.root / 'Makefile').exists())
        args = ['--repository', str(self.root), '--write']
        self.assertEqual(tool.main(args), 0)
        original = (self.root / 'Makefile').read_text()
        self.assertEqual(tool.main(args), 0)
        self.assertEqual((self.root / 'Makefile').read_text(), original)
        self.assertIn('GO_MAIN := ./cmd/server', (self.root / '.go-main.mk').read_text())
        result = subprocess.run(['make', '-n', 'run'], cwd=self.root, capture_output=True, text=True, check=True)
        self.assertIn('go run ./cmd/server', result.stdout)

    def test_missing_main_function_fails_before_writes(self):
        self.command('cmd/broken', 'package main\nfunc other() {}\n')
        self.assertEqual(tool.main(['--repository', str(self.root), '--write']), 2)
        self.assertFalse((self.root / 'Makefile').exists())
        self.assertFalse((self.root / '.go-main.mk').exists())

    def test_library_does_not_invent_command(self):
        (self.root / 'lib.go').write_text('package lib\n')
        self.assertEqual(tool.discover(self.root), [])
        self.assertEqual(tool.main(['--repository', str(self.root), '--write']), 2)
        self.assertFalse((self.root / '.go-main.mk').exists())

    def test_removes_legacy_pilot_smoke_assertion(self):
        script = self.root / 'scripts/validate.sh'
        script.parent.mkdir()
        script.write_text(tool.VALIDATION.removesuffix('go build ./...\n') +
                          'test "$(go run . -name \'Go pilot\')" = \'Hello, Go pilot!\'\n')
        proposed = tool.changes(self.root, './cmd/server')
        self.assertEqual(proposed['scripts/validate.sh'], tool.VALIDATION)

    def test_preserves_custom_validation_and_makefile(self):
        (self.root / 'Makefile').write_text('run:\n\t./custom-launcher\n')
        with self.assertRaisesRegex(ValueError, 'Custom Makefile'):
            tool.changes(self.root, './cmd/server')
        (self.root / 'Makefile').unlink()
        script = self.root / 'scripts/validate.sh'
        script.parent.mkdir()
        script.write_text('# custom validation\n')
        with self.assertRaisesRegex(ValueError, 'Custom scripts'):
            tool.changes(self.root, './cmd/server')

    def test_symlinked_configuration_is_rejected(self):
        (self.root / 'real-make').write_text('run:\n\tgo run .\n')
        (self.root / 'Makefile').symlink_to('real-make')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            tool.changes(self.root, './cmd/server')

    def test_build_subprocess_never_gets_secrets(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-only', 'GH_TOKEN': 'test-only'}), \
                patch.object(tool.subprocess, 'run') as run:
            tool.go(self.root, 'list', './...')
            environment = run.call_args.kwargs['env']
            self.assertNotIn('OPENAI_API_KEY', environment)
            self.assertNotIn('GH_TOKEN', environment)
            self.assertEqual(environment['GOTOOLCHAIN'], 'local')

    def test_context_excludes_source_and_symlinked_docs(self):
        self.command('.')
        (self.root / 'README.md').write_text('a' * 20000)
        (self.root / 'Makefile').symlink_to('main.go')
        documentation = tool.context(self.root)
        self.assertEqual(len(documentation['README.md']), 12000)
        self.assertNotIn('Makefile', documentation)
        self.assertNotIn('main.go', documentation)


if __name__ == '__main__':
    unittest.main()
