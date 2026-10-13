"""Local record replacement is atomic and portable to Windows."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend import execution_control as control


class LocalJsonTests(unittest.TestCase):
    def test_unicode_existing_record_can_be_replaced_and_no_temp_remains(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'中文 记录/state.json'
            control.write_json(p,{'state':'旧记录'})
            control.write_json(p,{'state':'新记录'})
            self.assertEqual(json.loads(p.read_text(encoding='utf-8')),{'state':'新记录'})
            self.assertEqual(list(p.parent.iterdir()),[p])

    def test_replace_failure_preserves_old_record_and_cleans_closed_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'state.json';p.write_text('original')
            with patch.object(control.os,'replace',side_effect=PermissionError('busy')):
                with self.assertRaises(PermissionError):control.write_json(p,{'state':'new'})
            self.assertEqual(p.read_text(),'original');self.assertEqual(list(p.parent.iterdir()),[p])


if __name__=='__main__':unittest.main()
