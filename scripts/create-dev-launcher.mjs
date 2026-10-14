// Generate exactly one local Mac entry; source checkouts contain no app bundle.
import {mkdir,readFile,copyFile,chmod,utimes} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
if(process.platform!=='darwin')throw new Error('Mac 开发入口仅在 macOS 上生成；Windows 使用安装快捷方式。');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const contents=path.join(root,'First Principles 工作台.app','Contents');
let existing='';try{existing=await readFile(path.join(contents,'Info.plist'),'utf8');}catch(error){if(error.code!=='ENOENT')throw error;}
if(existing&&!existing.includes('local.fpilot.workbench-control'))throw new Error('同名应用不属于工作台，未覆盖。');
await mkdir(path.join(contents,'MacOS'),{recursive:true});await mkdir(path.join(contents,'Resources'),{recursive:true});
await copyFile(path.join(root,'packaging/macos/dev-launcher/Info.plist'),path.join(contents,'Info.plist'));
await copyFile(path.join(root,'packaging/macos/dev-launcher/launcher'),path.join(contents,'MacOS/launcher'));
await copyFile(path.join(root,'packaging/icons/fpilot.icns'),path.join(contents,'Resources/fpilot.icns'));
await chmod(path.join(contents,'MacOS/launcher'),0o755);
const now=new Date();await utimes(contents,now,now);await utimes(path.dirname(contents),now,now);
console.log('已生成：'+path.dirname(contents));
