import copy
import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from backend import storage, workspace_checkpoints as policy
from test_node_editability import project


class WorkspaceCheckpointTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.store = storage.WorkspaceStore(self.root)
        self.workspace = {'activeId': 'p', 'projects': [project()]}
        self.revision = 0
        self.stamp = 1800000000

    def save(self, reason='autosave'):
        with patch.object(storage, 'datetime') as clock:
            clock.now.return_value = datetime.fromtimestamp(self.stamp, timezone.utc)
            result = self.store.save(self.workspace, self.revision, reason)
        self.revision = result['revision']
        return result

    def test_many_drafts_keep_latest_but_only_spaced_content_changes_make_checkpoints(self):
        self.save('migration'); initial = copy.deepcopy(self.workspace)
        for i in range(19):
            self.stamp += 30; self.workspace['projects'][0]['name'] = str(i); self.save()
        self.assertEqual(len(self.store.history()), 1)
        self.assertEqual(len(list((self.root/'snapshots').iterdir())), 2)
        self.assertEqual(self.store.read()['workspace'], self.workspace)
        self.assertEqual(self.store.read(1)['workspace'], initial)
        self.stamp += 30; self.workspace['projects'][0]['name'] = 'ten minutes'; self.save()
        self.assertEqual(len(self.store.history()), 2)
        self.assertGreater(self.revision, 19)

    def test_dragging_and_project_switching_are_durable_without_checkpoint_growth(self):
        self.save('migration')
        for i in range(5):
            self.stamp += 900
            self.workspace['projects'][0]['nodes'][0]['position'] = {'x': i*20, 'y': 10}
            self.workspace['projects'][0]['updatedAt'] = str(self.stamp)
            self.save()
        self.assertEqual(len(self.store.history()), 1)
        self.assertEqual(storage.WorkspaceStore(self.root).read()['workspace'], self.workspace)
        self.assertEqual(len(list((self.root/'snapshots').iterdir())), 2)

    def test_manual_checkpoint_preserves_label_and_reuses_saved_payload(self):
        self.save(); self.workspace['projects'][0]['name'] = '参数调整'; self.save()
        before = len(list((self.root/'snapshots').iterdir()))
        with patch.object(storage, 'datetime') as clock:
            clock.now.return_value = datetime.fromtimestamp(self.stamp+120, timezone.utc)
            self.store.checkpoint(self.revision, '测试窗口')
        self.assertEqual(len(list((self.root/'snapshots').iterdir())), before)
        self.assertEqual(self.store.history()[0]['label'], '测试窗口')
        self.assertEqual(policy.seconds(self.store.history()[0]['created_at']), self.stamp+120)
        self.assertEqual(self.store.read(self.revision)['workspace'], self.workspace)
        with self.assertRaises(storage.ConflictError): self.store.checkpoint(self.revision-1)

    def test_count_and_byte_caps_never_remove_current_or_originals(self):
        original = self.root/'originals'/('f'*64); original.write_bytes(b'original structure')
        with patch.object(policy, 'MAX_CHECKPOINTS', 3):
            for i in range(8):
                self.stamp += 601; self.workspace['projects'][0]['name'] = str(i); self.save()
        self.assertEqual(len(self.store.history()), 3)
        current_file = (self.root/'snapshots'/hashlib.sha256(json.dumps(self.workspace,ensure_ascii=False,separators=(',',':')).encode()).hexdigest())
        with patch.object(policy, 'MAX_BYTES', current_file.stat().st_size):
            self.store.checkpoint(self.revision)
        self.assertEqual(len(self.store.history()), 1)
        self.assertTrue(current_file.exists()); self.assertEqual(original.read_bytes(), b'original structure')

    def test_legacy_saves_are_spaced_and_orphans_are_reclaimed(self):
        self.save('migration')
        with self.store.connect() as db:
            db.execute('DELETE FROM workspace_checkpoints')
            for i in range(100):
                value = copy.deepcopy(self.workspace); value['projects'][0]['name'] = str(i)
                raw = json.dumps(value).encode(); digest = self.store.put_file('snapshots', raw)
                db.execute('INSERT INTO revisions(digest,created_at,reason) VALUES(?,?,?)', (digest, datetime.fromtimestamp(self.stamp+i*30,timezone.utc).isoformat(), 'autosave'))
            revision = db.execute('SELECT MAX(revision) FROM revisions').fetchone()[0]
            db.execute('UPDATE current SET revision=?', (revision,))
        self.revision = revision; self.stamp += 4000; self.workspace['projects'][0]['name'] = 'latest'; self.save()
        self.assertLessEqual(len(self.store.history()), 7)
        self.assertEqual(len(list((self.root/'snapshots').iterdir())), len(self.store.history()))
        self.assertEqual(self.store.read()['workspace'], self.workspace)

    def test_cleanup_failure_does_not_report_a_successful_save_as_failed(self):
        self.save(); self.workspace['projects'][0]['name'] = 'second'; self.save()
        self.workspace['projects'][0]['name'] = 'third'
        with patch.object(policy, 'collect', side_effect=PermissionError('busy')), self.assertLogs('backend.storage', level='WARNING'):
            self.save()
        self.assertEqual(self.store.read()['workspace'], self.workspace)
        self.workspace['projects'][0]['name'] = 'fourth'; self.save()
        self.assertEqual(len(list((self.root/'snapshots').iterdir())), 2)

    def test_reader_keeps_payload_until_done_while_another_store_prunes(self):
        self.save(); self.workspace['projects'][0]['name'] = 'second'; self.save()
        entered = threading.Event(); release = threading.Event(); saved = threading.Event(); errors = []
        real_read = Path.read_bytes
        def slow_read(path):
            if path.parent.name == 'snapshots' and threading.current_thread().name == 'reader':
                entered.set(); release.wait(3)
            return real_read(path)
        def reader():
            try: self.store.read()
            except Exception as exc: errors.append(exc)
        def writer():
            try:
                other = storage.WorkspaceStore(self.root)
                value = copy.deepcopy(self.workspace); value['projects'][0]['name'] = 'third'
                other.save(value, self.revision); saved.set()
            except Exception as exc: errors.append(exc)
        with patch.object(Path, 'read_bytes', slow_read):
            a = threading.Thread(target=reader,name='reader'); a.start(); self.assertTrue(entered.wait(2))
            b = threading.Thread(target=writer); b.start(); self.assertFalse(saved.wait(.1)); release.set()
            a.join(3); b.join(3)
        self.assertFalse(errors); self.assertTrue(saved.is_set())
