import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.history_index import paths


class HistoryIndexTests(unittest.TestCase):
    def test_filters_changes_deletion_and_rebuild(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);folder=root/'analyses-v2';folder.mkdir()
            for key,owner in [('a','p'),('b','q')]:
                (folder/key).mkdir();(folder/key/'analysis.json').write_text(json.dumps({'id':key,'project_id':owner,'sources':[{'attempt_id':'run-'+key}]}))
            self.assertEqual(len(paths(root,'analyses-v2')),2)
            # Unchanged history should not reread JSON files to filter by project.
            with patch.object(Path,'read_text',side_effect=AssertionError('unchanged details must not be parsed')):
                self.assertEqual(paths(root,'analyses-v2',project_id='p'),[folder/'a'/'analysis.json'])
                self.assertEqual(paths(root,'analyses-v2',attempt_id='run-b'),[folder/'b'/'analysis.json'])
            (folder/'a'/'analysis.json').write_text(json.dumps({'id':'a','project_id':'q'}))
            self.assertEqual(paths(root,'analyses-v2',project_id='p'),[])
            (folder/'b'/'analysis.json').unlink();self.assertEqual(len(paths(root,'analyses-v2')),1)
            (root/'history-index.sqlite3').unlink();self.assertEqual(len(paths(root,'analyses-v2')),1)
    def test_malformed_details_are_not_served_from_stale_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=root/'analyses-v2'/'a'/'analysis.json';path.parent.mkdir(parents=True)
            path.write_text('{"id":"a"}');self.assertEqual(len(paths(root,'analyses-v2')),1)
            path.write_text('invalid');self.assertEqual(paths(root,'analyses-v2'),[])


if __name__=='__main__':unittest.main()
