import assert from 'node:assert/strict';
import test from 'node:test';
import {changeAssignment,simpleIncar} from '../src/incarDocument';
import {meshFields} from '../src/InputFilePreview';

test('INCAR form edits retain comments, custom tags and line endings',()=>{
 const raw='# ENCUT = 100\r\n  ENCUT = 420 ; CUSTOM = file   ! note\r\n';
 const next=changeAssignment(raw,'ENCUT','500');
 assert.equal(next,raw.replace('420','500'));
 assert.equal(simpleIncar(next)?.CUSTOM,'file');
 assert.equal(changeAssignment(next,'LDAU','false'),next+'LDAU = .FALSE.\n');
 assert.equal(simpleIncar('VALUE = "multiline\nvalue"'),null);
});
test('KPOINTS editor maps shifts and reciprocal path labels back to the form',()=>{
 assert.equal(meshFields('grid\n0\nGamma\n4 4 6\n0 0.5 0',false).mesh_shift,'0 0.5 0');
 assert.equal(meshFields('path\n30\nLine-mode\nReciprocal\n0 0 0 ! G\n0.5 0 0 ! X',true).path,'0 0 0 G\n0.5 0 0 X');
 assert.throws(()=>meshFields('grid\n0\nGamma\n0 4 4',false));
});
test('explicit weighted points keep their exact text and declared row count',()=>{
 const points='nonuniform\n2\nReciprocal\n0 0 0 1 ! G\n.25 0 .5 3\n';
 assert.equal(meshFields(points,false).weighted_kpoints,points);
 assert.throws(()=>meshFields(points.replace('2\n','3\n'),false));
 assert.throws(()=>meshFields(points.replace('.5 3','.5 NaN'),true));
});
