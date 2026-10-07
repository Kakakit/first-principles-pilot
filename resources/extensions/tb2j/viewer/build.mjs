// Development only. The installed package ships viewer.html and needs no Node.
import {build} from 'esbuild';
import {readFile,writeFile,copyFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const base=fileURLToPath(new URL('.',import.meta.url));
const result=await build({entryPoints:[base+'main.js'],bundle:true,minify:true,write:false,format:'iife',target:'es2022'});
const template=await readFile(base+'shell.html','utf8');
await writeFile(base+'viewer.html',template.replace('/* BUNDLE */',result.outputFiles[0].text.replaceAll('</script','<\\/script')));
const require=createRequire(import.meta.url);
await copyFile(require.resolve('echarts/LICENSE'),base+'ECHARTS-LICENSE.txt');
await copyFile(require.resolve('zrender/LICENSE'),base+'ZRENDER-LICENSE.txt');
console.log('TB2J independent viewer built');
