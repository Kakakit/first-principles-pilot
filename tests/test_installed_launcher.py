"""Installed startup contract: identity, shutdown gates and archive boundaries."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import socket
import tarfile
import tempfile
import unittest
from contextlib import closing
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value

launcher=module('installed_launcher','scripts/installed_launcher.py')
builder=module('build_installer','packaging/build_installer.py')
launch=module('installed_backend','launch.py')


class InstalledLauncherTests(unittest.TestCase):
    def test_platform_icons_are_valid(self):
        from PIL import Image
        for extension in ('png','ico','icns'):
            with Image.open(ROOT/'packaging/icons'/('fpilot.'+extension)) as image:
                image.load();self.assertGreaterEqual(min(image.size),256)
                if extension=='ico':self.assertIn((16,16),image.ico.sizes());self.assertIn((256,256),image.ico.sizes())
    @unittest.skipIf(os.name=='nt','macOS bundle symlink requires POSIX filesystem semantics')
    def test_macos_bundle_carries_icon_resource(self):
        import plistlib
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp);app=output/'FPilot';(app/'branding').mkdir(parents=True)
            import shutil
            shutil.copyfile(ROOT/'packaging/icons/fpilot.icns',app/'branding/fpilot.icns')
            (output/'build.json').write_text('{}')
            with patch.object(builder,'run'):builder.package_mac(app,'1.1.0-beta.1',output,'macos-arm64')
            contents=output/'dmg/First Principles Pilot.app/Contents'
            info=plistlib.loads((contents/'Info.plist').read_bytes())
            self.assertEqual((contents/'Resources'/info['CFBundleIconFile']).read_bytes(),(ROOT/'packaging/icons/fpilot.icns').read_bytes())
    @unittest.skipIf(os.name=='nt','POSIX TIME_WAIT reuse policy')
    def test_closed_listener_port_is_reusable_but_live_listener_is_not(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            listener.bind(('127.0.0.1',0));listener.listen()
            port=listener.getsockname()[1]
            self.assertFalse(launch.port_available(port))
            client=socket.create_connection(('127.0.0.1',port))
            accepted,_=listener.accept();accepted.close()
            self.assertEqual(client.recv(1),b'');client.close()
        self.assertTrue(launch.port_available(port))

    def test_read_only_dispatch_gate_includes_old_records_and_blocks_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller=launcher.Controller(Path(tmp))
            controller.workspace.mkdir()
            with closing(sqlite3.connect(controller.workspace/'execution.sqlite3')) as c, c:
                c.execute('CREATE TABLE attempts(state TEXT)')
                c.execute("INSERT INTO attempts VALUES('DISPATCHING')")
                c.executemany('INSERT INTO attempts VALUES(?)',[('COMPLETED',)]*200)
            controller.child=Mock();controller.child.poll.return_value=None
            with patch.object(controller,'stop') as stop:
                with self.assertRaisesRegex(ValueError,'回执'):controller.request('stop')
                stop.assert_not_called()
            self.assertFalse(controller.busy)
            self.assertFalse(controller.shutdown_file.exists())

    def test_missing_database_is_empty_but_invalid_database_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.assertEqual(launcher.dispatching_count(root),0)
            (root/'execution.sqlite3').write_bytes(b'invalid')
            with self.assertRaises(sqlite3.Error):launcher.dispatching_count(root)

    def test_stop_is_cooperative_and_preserves_user_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller=launcher.Controller(Path(tmp));controller.child=Mock()
            controller.child.poll.return_value=None
            child=controller.child
            child.wait.side_effect=lambda: self.assertTrue(controller.shutdown_file.exists())
            note=Path(tmp)/'project.json';note.write_text('unchanged')
            controller.stop()
            child.terminate.assert_not_called();child.kill.assert_not_called()
            self.assertEqual(note.read_text(),'unchanged');self.assertEqual(controller.phase,'stopped')

    def test_busy_restart_and_old_live_child_cannot_spawn(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller=launcher.Controller(Path(tmp));controller.busy=True
            controller.request('start')
            with self.assertRaisesRegex(ValueError,'上一项'):controller.request('restart')
            controller.child=Mock();controller.child.poll.return_value=None
            with patch.object(launcher.subprocess,'Popen') as spawn:
                with self.assertRaisesRegex(ValueError,'重复启动'):controller.start()
                spawn.assert_not_called()

    def test_settings_are_outside_application_and_utf8(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'FPILOT_HOME':str(Path(tmp)/'中文 数据')}):
            home=launcher.user_home()
            self.assertNotEqual(home,ROOT)
            launcher.atomic_json(home/'fpilot.local.json',{'workspace':str(home/'项目 数据'),'port':8877})
            controller=launcher.Controller(home)
            self.assertEqual(controller.workspace,home/'项目 数据');self.assertEqual(controller.port,8877)

    def test_archive_rejects_traversal_and_external_links_before_extraction(self):
        for name,link in (('../escape',None),('python/escape','/tmp/escape'),('python/escape','../../escape')):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);archive=root/'runtime.tar.gz'
                with tarfile.open(archive,'w:gz') as tar:
                    member=tarfile.TarInfo(name)
                    if link:member.type=tarfile.SYMTYPE;member.linkname=link;tar.addfile(member)
                    else:member.size=1;tar.addfile(member,io.BytesIO(b'x'))
                with self.assertRaisesRegex(ValueError,'Unsafe'):builder.safe_extract(archive,root/'unpacked')
                self.assertFalse((root/'unpacked').exists())


if __name__=='__main__':unittest.main()
