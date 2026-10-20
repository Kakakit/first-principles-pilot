// Development only. All runtime resources are embedded in viewer.html.
import {build} from 'esbuild';
import {readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const base=new URL('.',import.meta.url);
const result=await build({entryPoints:[fileURLToPath(new URL('main.js',base))],bundle:true,minify:true,write:false,format:'iife',target:'es2022'});
const vendor=await readFile(new URL('vendor/3Dmol-2.5.3.min.js',base),'utf8'),shell=await readFile(new URL('shell.html',base),'utf8');
const safe=s=>s.replaceAll('</script','<\\/script');
await writeFile(new URL('viewer.html',base),shell.replace('/* VENDOR */',()=>safe(vendor)).replace('/* BUNDLE */',()=>safe(result.outputFiles[0].text)));
console.log('Independent polyhedron viewer built');
