"""Database handles must close before returning, including on rollback/error.

Retain the connections so garbage collection cannot hide a missing close on
POSIX. Renaming the database additionally exercises native Windows sharing.
"""
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from backend import analysis_datasets, execution, execution_cancel, file_transfers
from backend import history_index, node_editability, recycle

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sqlite_launcher', ROOT / 'scripts/installed_launcher.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class SqliteLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / '中文 # workspace'
        self.root.mkdir()

    def seed(self, name, statements):
        connection = sqlite3.connect(self.root / name)
        try:
            with connection:
                for sql, parameters in statements:
                    connection.execute(sql, parameters)
        finally:
            connection.close()

    @contextmanager
    def tracked(self):
        connect = sqlite3.connect
        opened = []
        def retain(*args, **kwargs):
            connection = connect(*args, **kwargs)
            opened.append(connection)
            return connection
        try:
            with patch.object(sqlite3, 'connect', side_effect=retain):
                yield opened
            self.assertTrue(opened, 'the operation must actually open a database')
            for connection in opened:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute('SELECT 1')
        finally:
            for connection in opened:
                connection.close()

    def assert_relocatable(self, path):
        moved = path.with_suffix('.moved')
        path.rename(moved)
        moved.rename(path)

    def test_launcher_closes_dispatch_check_and_invalid_database(self):
        self.seed('execution.sqlite3', [
            ('CREATE TABLE attempts(state TEXT)', ()),
            ('INSERT INTO attempts VALUES (?)', ('DISPATCHING',)),
        ])
        with self.tracked():
            self.assertEqual(launcher.dispatching_count(self.root), 1)
            self.assert_relocatable(self.root / 'execution.sqlite3')
        (self.root / 'execution.sqlite3').write_bytes(b'invalid')
        with self.tracked():
            with self.assertRaises(sqlite3.DatabaseError):
                launcher.dispatching_count(self.root)

    def test_history_index_commits_and_releases_file(self):
        record = self.root / 'analyses' / 'a' / 'analysis.json'
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({'id': 'a', 'project_id': 'p'}), encoding='utf-8')
        with self.tracked():
            self.assertEqual(history_index.paths(self.root, 'analyses', project_id='p'), [record])
            self.assert_relocatable(self.root / 'history-index.sqlite3')
        # Reopening must see committed metadata without parsing unchanged JSON.
        with patch.object(Path, 'read_text', side_effect=AssertionError('metadata not committed')):
            self.assertEqual(history_index.paths(self.root, 'analyses', project_id='p'), [record])

    def test_history_index_closes_on_scan_error(self):
        (self.root / 'analyses').mkdir()
        with self.tracked():
            with patch.object(history_index.os, 'scandir', side_effect=OSError('scan failed')):
                with self.assertRaisesRegex(OSError, 'scan failed'):
                    history_index.paths(self.root, 'analyses')

    def test_transfer_transaction_commits_rolls_back_and_closes(self):
        with patch.object(execution, 'ROOT', self.root):
            with self.tracked():
                with file_transfers._db() as db:
                    db.execute('INSERT INTO jobs VALUES (?,?)', ('keep', '{}'))
                with self.assertRaisesRegex(RuntimeError, 'abort'):
                    with file_transfers._db() as db:
                        db.execute('INSERT INTO jobs VALUES (?,?)', ('discard', '{}'))
                        raise RuntimeError('abort')
                self.assert_relocatable(self.root / 'file-transfers/index.sqlite3')
            with file_transfers._db() as db:
                self.assertEqual(db.execute('SELECT id FROM jobs').fetchall(), [('keep',)])

    def test_dataset_and_node_readers_close_uri_connections(self):
        self.seed('index.sqlite3', [
            ('CREATE TABLE revisions(revision INTEGER, digest TEXT)', ()),
            ('CREATE TABLE current(id INTEGER, revision INTEGER)', ()),
        ])
        self.seed('execution.sqlite3', [('CREATE TABLE attempts(body TEXT)', ())])
        with patch.object(analysis_datasets, 'ROOT', self.root):
            with self.tracked() as connections:
                self.assertEqual(analysis_datasets.context(), ({}, {}))
                self.assertEqual(len(connections), 2)
            with self.tracked():
                node_editability.usage(self.root, 'p')
        for name in ('index.sqlite3', 'execution.sqlite3'):
            self.assert_relocatable(self.root / name)

    def test_recycle_reads_missing_table_and_commits_pause(self):
        plan = {'id': 'chain', 'state': 'ACTIVE', 'project_id': 'p'}
        self.seed('execution.sqlite3', [
            ('CREATE TABLE chain_runs(id TEXT, state TEXT, body TEXT)', ()),
            ('INSERT INTO chain_runs VALUES (?,?,?)', ('chain', 'ACTIVE', json.dumps(plan))),
        ])
        with patch.object(recycle, 'ROOT', self.root):
            with self.tracked():
                self.assertEqual(recycle._rows('missing_table'), [])
                recycle._pause_dependents('projects', 'p', 'local source removed')
            self.assertEqual(recycle._rows('chain_runs')[0]['state'], 'PAUSED')
        self.assert_relocatable(self.root / 'execution.sqlite3')

    def test_cancellation_journal_commits_and_closes(self):
        with patch.object(execution, 'ROOT', self.root):
            with self.tracked():
                execution_cancel.journal({'id': 'attempt', 'cancellation': {'state': 'UNKNOWN'}}, 'CANCEL_RECEIPT')
            db = execution.db()
            try:
                row = db.execute('SELECT event,details FROM execution_events').fetchone()
                self.assertEqual(row[0], 'CANCEL_RECEIPT')
                self.assertEqual(json.loads(row[1])['state'], 'UNKNOWN')
            finally:
                db.close()
        self.assert_relocatable(self.root / 'execution.sqlite3')


if __name__ == '__main__':
    unittest.main()
