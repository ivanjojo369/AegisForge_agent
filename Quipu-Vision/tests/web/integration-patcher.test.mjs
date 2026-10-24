/** Regressions for reproducible, non-destructive AegisForge web integration. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,existsSync} from 'node:fs';
import {join,dirname,resolve} from 'node:path';
import {tmpdir} from 'node:os';
import {spawnSync} from 'node:child_process';
const repoRoot = resolve(import.meta.dirname, '../../../');
const source = resolve(import.meta.dirname, '../../');
const installer = join(source,'tools','integrate_web.py');
const make = (root,path,content) => {
 const f=join(root,path);mkdirSync(dirname(f),{recursive:true});writeFileSync(f,content);return f;
};
function fixture(){
 const root=mkdtempSync(join(tmpdir(),'qv-web-test-'));
 make(root,'Quipu-Vision/tools/integrate_web.py',readFileSync(installer));
 for(const n of ['image-quality.js','decision-trace.js','evidence-overlay.js']) make(root,'Quipu-Vision/integrations/aegisforge/web/vision/'+n,readFileSync(join(source,'integrations/aegisforge/web/vision',n)));
 make(root,'Quipu-Vision/integrations/aegisforge/web/evaluation-lab.html',readFileSync(join(source,'integrations/aegisforge/web/evaluation-lab.html')));
 make(root,'src/aegisforge/web/vision/index.html',`<!doctype html><head>
  <script src="/demo/vision/app.js" defer></script></head>
<form>
            <p id="form-feedback" class="form-feedback" role="status" aria-live="polite">Select two images to begin.</p></form>
            <section class="result-subsection" aria-labelledby="evidence-title">
<ul id="events-list"></ul>
              <details class="raw-result"><summary>View structured JSON response</summary><pre id="raw-json" tabindex="0"></pre></details></section>
<span>Local-first visual inspection · OpenCV 5</span>`);
 make(root,'src/aegisforge/web/vision/app.js',`(() => { "use strict";
const $ = (id) => document.getElementById(id);
const elements = {events:{replaceChildren(){}},regionsBody:{replaceChildren(){}},form:{addEventListener(){}}};
function clearResult(){
    elements.regionsBody.replaceChildren();
    elements.events.replaceChildren();
}
function renderEvents(events, warnings) {
    elements.events.replaceChildren();
}
async function validateDimensions(files) {}
async function buildRequest(files) {}
async function jsonRequest(){}
function selectedImages(){return []}
function renderResult(){}
function updateButton(){}
let busy=false,ready=true,requestController=null;
  elements.form.addEventListener("submit", async (event) => {
    const files = selectedImages();
      await validateDimensions(files);
      const request = await buildRequest(files);
      renderResult(artifact);
  });
})();`);
 make(root,'src/aegisforge/web/vision/styles.css',':root { --line: #123; }\n');
 make(root,'src/aegisforge/a2a_server.py',`from pathlib import Path
_VISION_WEB_ASSETS = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
}
def _vision_web_asset(filename):
    if filename not in _VISION_WEB_ASSETS: return None
    candidates = (
        Path(__file__).resolve().parent / "web" / "vision" / filename,
        Path.cwd() / "src" / "aegisforge" / "web" / "vision" / filename,
    )
    return next((c for c in candidates if c.is_file()),None)
def fake_routes():
    return [
            Route("/demo/vision/styles.css", lambda _request: _vision_web_response("styles.css"), methods=["GET"]),
    ]
`);
 return root;
}
function run(root,...args) {
 const tool=process.env.PYTHON||'python';
 const result=spawnSync(tool,[installer,'--root',root,...args],{encoding:'utf8'});
 return {status:result.status, output:result.stdout+result.stderr};
}
test('dry-run does not modify host or create runtime assets',()=>{
 const root=fixture();const path='src/aegisforge/web/vision/index.html';const before=readFileSync(join(root,path),'utf8');const r=run(root);
 assert.equal(r.status,0,r.output);assert.match(r.output,/Dry-run/);assert.equal(readFileSync(join(root,path),'utf8'),before);
 assert.equal(existsSync(join(root,'src/aegisforge/web/vision/image-quality.js')),false);
});
test('apply and check are idempotent and preserve existing anchors',()=>{
 const root=fixture();const install=run(root,'--apply');assert.equal(install.status,0,install.output);
 assert.equal(run(root,'--check').status,0);
 const html=readFileSync(join(root,'src/aegisforge/web/vision/index.html'),'utf8');
 for(const id of ['qv-quality-panel','qv-overlay-panel','qv-trace-panel']) assert.ok(html.includes(id));
 const app=readFileSync(join(root,'src/aegisforge/web/vision/app.js'),'utf8');
 assert.ok(app.includes('await reportImageQuality(files);'));
 assert.ok(app.includes('void renderWebEvidence(artifact);'));
 const js=spawnSync('node',['--check','-'],{input:app,encoding:'utf8'});
 assert.equal(js.status,0,js.stderr);
 assert.ok(existsSync(join(root,'src/aegisforge/web/evaluation-lab.html')));
 for(const n of ['image-quality.js','decision-trace.js','evidence-overlay.js']) assert.ok(existsSync(join(root,'src/aegisforge/web/vision',n)));
 const unchanged=readFileSync(join(root,'src/aegisforge/web/vision/index.html'));
 const again=run(root,'--apply');assert.equal(again.status,0,again.output);assert.deepEqual(readFileSync(join(root,'src/aegisforge/web/vision/index.html')),unchanged);
 assert.match(again.output,/already current/);
});
test('incompatible host fails closed without partial changes',()=>{
 const root=fixture();const app=join(root,'src/aegisforge/web/vision/app.js');const content=readFileSync(app,'utf8');
 writeFileSync(app,content.replace('      renderResult(artifact);','      doSomethingElse(artifact);'));
 const html=join(root,'src/aegisforge/web/vision/index.html');const original=readFileSync(html,'utf8');
 const r=run(root,'--apply');assert.equal(r.status,2,r.output);assert.equal(readFileSync(html,'utf8'),original);
 assert.equal(existsSync(join(root,'src/aegisforge/web/vision/image-quality.js')),false);
});

test('ambiguous clearResult context remains a hard error without writes',()=>{
 const root=fixture();
 const app=join(root,'src/aegisforge/web/vision/app.js');
 const original=readFileSync(app,'utf8');
 const pair='    elements.regionsBody.replaceChildren();\n    elements.events.replaceChildren();';
 assert.equal(original.split(pair).length - 1, 1);
 writeFileSync(app,original.replace('function renderEvents(events, warnings) {\n    elements.events.replaceChildren();',
   'function renderEvents(events, warnings) {\n' + pair));
 const html=join(root,'src/aegisforge/web/vision/index.html');
 const originalHtml=readFileSync(html,'utf8');
 const result=run(root,'--apply');
 assert.equal(result.status,2,result.output);
 assert.match(result.output,/expected exactly one anchor, found 2/);
 assert.equal(readFileSync(html,'utf8'),originalHtml);
 assert.equal(existsSync(join(root,'src/aegisforge/web/vision/image-quality.js')),false);
});
