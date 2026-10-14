import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('codex_review', Path(__file__).resolve().parents[2] / 'scripts/codex-review.py')
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


class ReviewTests(unittest.TestCase):
    def test_disabled_hook_never_runs_review(self):
        with patch.object(review, 'git', return_value=b'false'), patch.object(review, 'review') as run:
            self.assertEqual(review.main(['--hook']), 0)
            run.assert_not_called()

    def test_outage_is_advisory_only_in_hook(self):
        with patch.object(review, 'git', return_value=b'true'), patch.object(review, 'review', side_effect=ValueError('offline')):
            self.assertEqual(review.main(['--hook']), 0)
            self.assertEqual(review.main([]), 2)

    def test_oversized_diff_is_not_sent(self):
        with patch.object(review, 'git', side_effect=[b'a'*40, b'b'*40, b'x'*(review.MAX_DIFF_BYTES+1)]), patch.object(review.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(ValueError, 'exceeds'):
                review.review('main', 1)
            launch.assert_not_called()

    def test_empty_diff_does_not_consume_usage(self):
        with patch.object(review, 'git', side_effect=[b'a'*40, b'b'*40, b'']), patch.object(review.subprocess, 'Popen') as launch:
            review.review('main', 1)
            launch.assert_not_called()

    def test_invocation_is_isolated_and_output_is_visible(self):
        class Process:
            returncode = 0
            def communicate(self, payload, timeout):
                self.payload = payload
                Path(self.command[self.command.index('--output-last-message')+1]).write_text('A concrete finding')
        process = Process()
        def launch(command, **kwargs):
            process.command = command
            self.assertIn('--ignore-user-config', command)
            self.assertIn('--ephemeral', command)
            self.assertIn('read-only', command)
            self.assertIn('approval_policy="never"', command)
            self.assertNotIn('AWS_SECRET_ACCESS_KEY', kwargs['env'])
            self.assertFalse((Path(kwargs['cwd']) / '.git').exists())
            return process
        with patch.object(review, 'git', side_effect=[b'a'*40, b'b'*40, b'+candidate']), patch.object(review.subprocess, 'Popen', side_effect=launch), patch.dict(review.os.environ, {'AWS_SECRET_ACCESS_KEY': 'fixture'}):
            review.review('main', 10)
        self.assertIn(b'<untrusted_diff>', process.payload)

    def test_timeout_kills_review_process_group(self):
        from unittest.mock import Mock
        process = Mock(pid=123)
        process.communicate.side_effect = [subprocess.TimeoutExpired('codex', 1), (None, None)]
        with patch.object(review, 'git', side_effect=[b'a'*40, b'b'*40, b'+candidate']), patch.object(review.subprocess, 'Popen', return_value=process), patch.object(review.os, 'killpg') as kill:
            with self.assertRaisesRegex(ValueError, 'timed out'):
                review.review('main', 1)
            kill.assert_called_once_with(123, review.signal.SIGKILL)

    def test_staged_review_excludes_unstaged_edits_and_needs_no_upstream(self):
        with tempfile.TemporaryDirectory() as directory:
            def git(*args):
                return subprocess.run(['git', '-C', directory, *args], check=True, capture_output=True).stdout
            git('init')
            path = Path(directory, 'sample.txt')
            path.write_text('staged content\n')
            git('add', 'sample.txt')
            path.write_text('unstaged content\n')
            # Also covers an initial commit, where HEAD does not yet exist.
            with patch.object(review, 'git', side_effect=git), patch.object(review, 'run_review') as run:
                review.review(None, 10, staged=True)
                diff = run.call_args.args[0]
                self.assertIn(b'+staged content', diff)
                self.assertNotIn(b'unstaged content', diff)

    def test_staged_flag_selects_index(self):
        with patch.object(review, 'git', return_value=b'true'), patch.object(review, 'review') as run:
            self.assertEqual(review.main(['--hook', '--staged']), 0)
            self.assertTrue(run.call_args.kwargs['staged'])

    def test_real_git_revision_resolution_and_diff(self):
        with tempfile.TemporaryDirectory() as directory:
            run_git = subprocess.run
            def git(*args):
                return run_git(['git', '-C', directory, *args], check=True, capture_output=True).stdout
            git('init')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.com', 'commit', '--allow-empty', '-m', 'base')
            base = git('rev-parse', 'HEAD').decode().strip()
            Path(directory, 'sample.txt').write_text('committed\n')
            git('add', 'sample.txt')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.com', 'commit', '-m', 'change')
            Path(directory, 'sample.txt').write_text('uncommitted secret\n')
            with patch.object(review, 'git', side_effect=git):
                with self.assertRaises(subprocess.CalledProcessError):
                    review.review('--help', 1)
            self.assertIn(b'+committed', git('diff', base, 'HEAD', '--'))
            self.assertNotIn(b'uncommitted secret', git('diff', base, 'HEAD', '--'))


if __name__ == '__main__':
    unittest.main()
