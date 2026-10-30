
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const path = process.argv[2];
const script = fs.readFileSync(path, 'utf8');
const plan = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
class El {
  constructor(tag) {this.tag=tag;this.textContent='';this.children=[];this.listeners={};this.classList={add(){},remove(){},toggle(){}};this.dataset={};this.style={};this.value='';this.disabled=false;this.title='';}
  appendChild(c){this.children.push(c);return c;}
  replaceChildren(...x){this.children=[...x];}
  addEventListener(k,cb){this.listeners[k]=cb;}
  click(){if(this.listeners.click)this.listeners.click();}
}
const elements = {};
const document = {
  createElement: t => new El(t),
  getElementById: k => (elements[k] ||= new El('div')),
  querySelectorAll: ()=>[],
  addEventListener:(ev,cb)=>{if(ev==='DOMContentLoaded')document.onload=cb}
};
const location={hash:''};
const window={addEventListener(){}, confirm(){return false}};
const context = {document,location,window,console,fetch:async()=>({ok:true,json:async()=>({mode:'offline'})})};
vm.runInNewContext(script,context,{filename:path});
const file=(name,data)=>({name,size:Buffer.byteLength(data), text:async()=>data});
const flush=()=>new Promise(r=>setTimeout(r,20));
(async()=>{
  await document.onload();
  const summary={mode:'live_nebius',request_id:plan.request_id,plan_sha256:plan.plan_sha256,
    model:plan.model,plan_step_count:4,tools_invoked:2,independent_checks_run:3,
    independent_checks_passed:3,verification_status:'verified',audit_events:3,audit_sha256:'a'.repeat(64)};
  const evs=[
    {sequence:1,stage:'plan',status:'proposed',sha256:'c'.repeat(64)},
    {sequence:2,stage:'execution',status:'reported_complete',sha256:'b'.repeat(64)},
    {sequence:3,stage:'verification',status:'verified',sha256:summary.audit_sha256}
  ];
  const upload=async (...files)=>{
    elements['file-picker'].listeners.change({target:{files}});
    await flush();
  };
  await upload(file('run.json',JSON.stringify(summary)),file('run.jsonl',evs.map(x=>JSON.stringify(x)).join('\n')));
  assert.equal(elements['m-steps'].textContent,'4');
  assert.equal(elements['plan-label'].textContent,'Sin desglose');
  assert.equal(elements['evidence-items'].children.length,3);
  await upload(file('nemotron_live_plan_from_console.json',JSON.stringify(plan)));
  assert.match(elements['plan-label'].textContent, /4 pasos/);
  assert.match(elements['plan-label'].textContent,/externo/);
  assert.equal(elements['plan-steps'].children.length,5);
  assert.match(elements['plan-steps'].children[0].textContent,/Desglose adicional/);
  assert.equal(elements['m-verdict'].textContent,'Verificado');
  const bad={...plan,request_id:'chatcmpl-otro'};
  await upload(file('bad-plan.json', JSON.stringify(bad)));
  assert.match(elements['api-message'].textContent,/NO coincide/);
  assert.equal(elements['plan-steps'].children.length,5);
  await upload(file('new-summary.json',JSON.stringify({...summary,request_id:'otra-ejecucion',audit_sha256:'f'.repeat(64)})));
  assert.equal(elements['evidence-items'].children.length,1); // only 'Sin eventos'
  assert.equal(elements['plan-label'].textContent,'Sin desglose');
  await upload(file('old-log.jsonl',evs.map(x=>JSON.stringify(x)).join('\n')));
  assert.match(elements['api-message'].textContent,/huella final JSONL no coincide/);
  assert.equal(elements['evidence-items'].children.length,1);
  console.log('PASS 7 visual import assertions');
})().catch(e=>{console.error(e);process.exitCode=1});
