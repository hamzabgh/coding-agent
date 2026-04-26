const vscode = require("vscode");
const path   = require("path");
const fs     = require("fs");
const https  = require("https");
const http   = require("http");

let sidebar = null;
let out     = null;
let bar     = null;
let hTimer  = null;

// pending preview per session: { token, project, changes }
let pending = null;

function activate(ctx) {
  out = vscode.window.createOutputChannel("Coding Agent");
  bar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  bar.text = "$(robot) agent.dev"; bar.command = "codingAgent.openPanel"; bar.show();
  ctx.subscriptions.push(bar);

  sidebar = new Sidebar(ctx.extensionUri);
  ctx.subscriptions.push(vscode.window.registerWebviewViewProvider("codingAgent.sidebarView", sidebar));

  ctx.subscriptions.push(vscode.window.onDidChangeActiveTextEditor(ed => {
    if (ed && sidebar) sidebar.post({ type:"activeFile", name:path.basename(ed.document.fileName), lang:ed.document.languageId });
  }));
  ctx.subscriptions.push(vscode.workspace.onDidChangeConfiguration(e => {
    if (e.affectsConfiguration("codingAgent")) { sidebar && sidebar.post({type:"configChanged",config:cfg()}); scheduleHealth(); }
  }));

  const cmds = {
    "codingAgent.openPanel":   () => vscode.commands.executeCommand("workbench.view.extension.codingAgentSidebar"),
    "codingAgent.improveFile": () => runOnActive("improve"),
    "codingAgent.fixBug": async () => {
      const d = await vscode.window.showInputBox({prompt:"Describe the bug (optional)",placeHolder:"e.g. TypeError on line 42"});
      if (d !== undefined) runOnActive("fix_bug", d);
    },
    "codingAgent.addFeature": async () => {
      const f = await vscode.window.showInputBox({prompt:"Feature to add",placeHolder:"e.g. Add rate limiting"});
      if (f) runOnActive("add_feature", f);
    },
    "codingAgent.refactor": () => runOnActive("refactor"),
    "codingAgent.explain":  () => runOnActive("explain"),
    "codingAgent.generate": async () => {
      const fn = await vscode.window.showInputBox({prompt:"Output file path",placeHolder:"utils/helpers.py"});
      if (!fn) return;
      const d = await vscode.window.showInputBox({prompt:"What should this file do?"});
      if (d) run("generate", fn, d);
    },
    "codingAgent.chat": async () => {
      const m = await vscode.window.showInputBox({prompt:"Ask the agent",placeHolder:"Fix the bug on line 42, then explain it"});
      if (m) runOnActive("chat", m);
    },
  };
  for (const [id, fn] of Object.entries(cmds))
    ctx.subscriptions.push(vscode.commands.registerCommand(id, fn));
}

// ── Config ────────────────────────────────────────────────────────────────────
function cfg() {
  const c = vscode.workspace.getConfiguration("codingAgent");
  return {
    mode:c.get("mode","server"), agentPath:c.get("agentPath","").trim(),
    pythonPath:c.get("pythonPath","python").trim(),
    serverUrl:c.get("serverUrl","http://localhost:8080").trim().replace(/\/$/,""),
    serverApiKey:c.get("serverApiKey","").trim(),
    backend:c.get("backend","ollama"),
    ollamaModel:c.get("ollamaModel","phi:latest"),
    vllmModel:c.get("vllmModel","Qwen/CodeQwen1.5-7B-Chat"),
  };
}

function projectRoot(fp) {
  const ws = vscode.workspace.workspaceFolders;
  if (!ws||!ws.length) return "";
  return (ws.find(w=>fp&&fp.startsWith(w.uri.fsPath))||ws[0]).uri.fsPath;
}

// ── Health ────────────────────────────────────────────────────────────────────
function scheduleHealth() { if(hTimer)clearTimeout(hTimer); hTimer=setTimeout(pollHealth,1200); }
function pollHealth() {
  const c=cfg();
  if (c.mode!=="server") {
    sidebar&&sidebar.post({type:"health",ok:true,mode:"cli",backend:c.backend,model:c.backend==="vllm"?c.vllmModel:c.ollamaModel});
    return;
  }
  apiGet(c.serverUrl+"/health",c.serverApiKey)
    .then(d=>sidebar&&sidebar.post({type:"health",ok:d.model_available!==false,mode:"server",backend:d.backend||c.backend,model:d.model||(c.backend==="vllm"?c.vllmModel:c.ollamaModel)}))
    .catch(()=>sidebar&&sidebar.post({type:"health",ok:false,mode:"server",backend:c.backend,model:"unreachable"}));
}

// ── Run ───────────────────────────────────────────────────────────────────────
function runOnActive(mode, msg="") {
  const ed = vscode.window.activeTextEditor;
  if (!ed) {
    vscode.window.showWarningMessage("Coding Agent: No active file. Click inside a code file first.");
    return;
  }
  // Reject output channels, untitled buffers, git diffs, etc.
  if (ed.document.uri.scheme !== "file") {
    vscode.window.showWarningMessage(
      "Coding Agent: Active panel is not a file on disk. Click inside a .py/.js/etc. file first."
    );
    return;
  }
  run(mode, ed.document.uri.fsPath, msg);
}

function run(mode, fp, msg="") {
  const c = cfg();
  sidebar&&sidebar.post({type:"runStart",mode,file:path.basename(fp||"")});
  setBusy(mode);
  if (c.mode==="server") runServer(mode,fp,msg,c);
  else runCli(mode,fp,msg,c);
}

// ── CLI ───────────────────────────────────────────────────────────────────────
function runCli(mode, fp, msg, c) {
  if (!c.agentPath) {
    vscode.window.showErrorMessage("Coding Agent: agentPath not set.","Open Settings")
      .then(x=>x&&vscode.commands.executeCommand("workbench.action.openSettings","codingAgent.agentPath"));
    setReady(); return;
  }
  const mainPy=path.join(c.agentPath,"main.py");
  if (!fs.existsSync(mainPy)){vscode.window.showErrorMessage(`main.py not found at ${mainPy}`);setReady();return;}
  const root=projectRoot(fp);
  const rel=root&&fp?path.relative(root,fp):fp;
  // map server mode names to CLI names
  const cliMode={fix_bug:"fix",add_feature:"feature",improve:"improve",refactor:"refactor",explain:"explain",chat:"chat",generate:"generate"}[mode]||mode;
  const args=[mainPy,cliMode];
  if (fp&&mode!=="status") args.push(rel);
  if (msg) args.push(msg);
  if (root) args.push("--project",root);
  const name="Coding Agent";
  let term=vscode.window.terminals.find(t=>t.name===name&&t.exitStatus===undefined);
  if (!term) term=vscode.window.createTerminal({name,cwd:c.agentPath});
  term.show(true); term.sendText(`${c.pythonPath} ${args.map(q).join(" ")}`);
  log(`[CLI] ${mode} — ${path.basename(fp||"")}`);
}

// ── Server ────────────────────────────────────────────────────────────────────
function runServer(mode, fp, msg, c) {
  if (mode==="status"){vscode.env.openExternal(vscode.Uri.parse(c.serverUrl+"/health"));setReady();return;}
  const root=projectRoot(fp);
  const rel=root&&fp?path.relative(root,fp):fp;
  const body={project:root,file:rel,mode,message:msg||"",auto_apply:false};
  const hdrs={"Content-Type":"application/json"};
  if (c.serverApiKey) hdrs["Authorization"]=`Bearer ${c.serverApiKey}`;
  log(`[SERVER] POST /run  mode=${mode} file=${rel}`);

  apiPost(c.serverUrl+"/run",body,hdrs)
    .then(r => {
      setReady();
      log(`[SERVER] ✓ ${mode} — ${r.changes?.length||0} changes`);

      // Send full result to sidebar for display
      sidebar&&sidebar.post({type:"agentResult", result:r, mode, file:rel, project:root, serverUrl:c.serverUrl, apiKey:c.serverApiKey});

      // Show explanation in output channel for "explain" and chat answers
      if (r.analysis && (!r.changes || r.changes.length===0)) {
        out.appendLine(`\n── ${mode.toUpperCase()} ──────────────────────`);
        out.appendLine(r.analysis);
        if (r.plan&&r.plan.length) { out.appendLine("\nPlan:"); r.plan.forEach((p,i)=>out.appendLine(`  ${i+1}. ${p}`)); }
        out.appendLine("─".repeat(40)+"\n");
        out.show(true);
      }
    })
    .catch(err => {
      setReady();
      log(`[SERVER] ✗ ${mode}: ${err.message}`);
      out.show(true);
      vscode.window.showErrorMessage(`Coding Agent: ${err.message}`);
      sidebar&&sidebar.post({type:"runDone",mode,ok:false,error:err.message});
    });
}

// Called from sidebar when user clicks Accept
async function applyChanges(token, project, serverUrl, apiKey) {
  const hdrs={"Content-Type":"application/json"};
  if (apiKey) hdrs["Authorization"]=`Bearer ${apiKey}`;
  try {
    const r = await apiPost(serverUrl+"/apply",{preview_token:token,project},hdrs);
    vscode.window.showInformationMessage(`✓ Applied to: ${r.applied_files.join(", ")}`);
    sidebar&&sidebar.post({type:"applied",files:r.applied_files});
    log(`[SERVER] ✓ Applied: ${r.applied_files.join(", ")}`);
    // Refresh open editors
    for (const f of (r.applied_files||[])) {
      const abs=path.join(project,f);
      const docs=vscode.workspace.textDocuments.filter(d=>d.uri.fsPath===abs);
      for (const d of docs) {
        const ed=vscode.window.visibleTextEditors.find(e=>e.document===d);
        if (ed) await vscode.commands.executeCommand("workbench.action.files.revert",d.uri);
      }
    }
  } catch(e) {
    vscode.window.showErrorMessage(`Apply failed: ${e.message}`);
  }
}

async function rejectChanges(token, project, serverUrl, apiKey) {
  const hdrs={};
  if (apiKey) hdrs["Authorization"]=`Bearer ${apiKey}`;
  try { await apiDel(`${serverUrl}/preview/${token}`,hdrs); } catch{}
  sidebar&&sidebar.post({type:"rejected"});
  log(`[SERVER] Changes rejected by user.`);
}

// ── HTTP ──────────────────────────────────────────────────────────────────────
function apiPost(url,body,headers){
  return new Promise((res,rej)=>{
    const pl=JSON.stringify(body); const p=new URL(url); const lib=p.protocol==="https:"?https:http;
    const req=lib.request({hostname:p.hostname,port:p.port||(p.protocol==="https:"?443:80),path:p.pathname+p.search,method:"POST",headers:{...headers,"Content-Length":Buffer.byteLength(pl)}},r=>{
      let d=""; r.on("data",c=>d+=c); r.on("end",()=>{
        if(r.statusCode>=200&&r.statusCode<300){try{res(JSON.parse(d))}catch{res({ok:true})}}
        else{let x=d;try{x=JSON.parse(d).detail||d}catch{}rej(new Error(`HTTP ${r.statusCode}: ${x}`))}
      });
    });
    req.on("error",rej); req.write(pl); req.end();
  });
}
function apiGet(url,key){
  return new Promise((res,rej)=>{
    const p=new URL(url); const lib=p.protocol==="https:"?https:http;
    const h=key?{"Authorization":`Bearer ${key}`}:{};
    const req=lib.request({hostname:p.hostname,port:p.port||(p.protocol==="https:"?443:80),path:p.pathname+p.search,method:"GET",headers:h},r=>{
      let d=""; r.on("data",c=>d+=c); r.on("end",()=>{try{res(JSON.parse(d))}catch{rej(new Error("bad json"))}});
    });
    req.setTimeout(4000,()=>{req.destroy();rej(new Error("timeout"))}); req.on("error",rej); req.end();
  });
}
function apiDel(url,headers){
  return new Promise((res,rej)=>{
    const p=new URL(url); const lib=p.protocol==="https:"?https:http;
    const req=lib.request({hostname:p.hostname,port:p.port||(p.protocol==="https:"?443:80),path:p.pathname+p.search,method:"DELETE",headers},r=>{let d="";r.on("data",c=>d+=c);r.on("end",()=>res(d));});
    req.on("error",rej); req.end();
  });
}

function setBusy(m){bar.text=`$(sync~spin) agent: ${m}`;setTimeout(setReady,30000);}
function setReady(){bar.text="$(robot) agent.dev";}
function log(m){out.appendLine(`[${new Date().toLocaleTimeString()}] ${m}`);}
function q(a){return(a.includes(" ")||a.includes("\\"))?`"${a.replace(/"/g,'\\"')}"`:a;}

// ── Sidebar ───────────────────────────────────────────────────────────────────
class Sidebar {
  constructor(uri){this._view=null;}
  resolveWebviewView(v){
    this._view=v;
    v.webview.options={enableScripts:true};
    v.webview.html=getHtml();
    v.webview.onDidReceiveMessage(m=>{
      if(m.type==="ready"){
        this.post({type:"configChanged",config:cfg()}); scheduleHealth();
        const ed=vscode.window.activeTextEditor;
        if(ed)this.post({type:"activeFile",name:path.basename(ed.document.fileName),lang:ed.document.languageId});
      } else if(m.type==="command"){
        if(m.command==="generate"&&m.file&&m.message) run("generate",m.file,m.message);
        else if(m.command==="ping"){scheduleHealth();pollHealth();}
        else runOnActive(m.command,m.message||"");
      } else if(m.type==="accept"){
        applyChanges(m.token,m.project,m.serverUrl,m.apiKey);
      } else if(m.type==="reject"){
        rejectChanges(m.token,m.project,m.serverUrl,m.apiKey);
      } else if(m.type==="openSettings"){
        vscode.commands.executeCommand("workbench.action.openSettings","codingAgent");
      }
    });
  }
  post(d){if(this._view)this._view.webview.postMessage(d);}
}

// ── HTML ──────────────────────────────────────────────────────────────────────
function getHtml(){
return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&family=Inter:wght@400;500&display=swap');
:root{
  --bg:#0d0f12;--bg1:#12151a;--bg2:#171b22;--bg3:#1e222c;--bg4:#252a36;--bg5:#2d3240;
  --b:rgba(255,255,255,0.07);--b2:rgba(255,255,255,0.13);
  --t0:#e8eaf0;--t1:#8b91a8;--t2:#4a5068;--t3:#2e3347;
  --gr:#4ade80;--gr2:#16a34a;--grs:rgba(74,222,128,0.08);
  --bl:#60a5fa;--bls:rgba(96,165,250,0.08);
  --am:#fbbf24;--ams:rgba(251,191,36,0.08);
  --re:#f87171;--res:rgba(248,113,113,0.08);
  --pu:#a78bfa;--pus:rgba(167,139,250,0.08);
  --cy:#22d3ee;--cys:rgba(34,211,238,0.08);
  --mono:'JetBrains Mono',monospace;--ui:'Inter',system-ui,sans-serif;
}
*{margin:0;padding:0;box-sizing:border-box;}
body{background:var(--bg1);color:var(--t0);font-family:var(--ui);font-size:11px;overflow-x:hidden;}
.topbar{display:flex;align-items:center;gap:8px;padding:10px 12px;background:var(--bg);border-bottom:1px solid var(--b);}
.brand{display:flex;align-items:center;gap:7px;flex:1;}
.bico{width:18px;height:18px;border:1px solid var(--gr2);border-radius:3px;display:flex;align-items:center;justify-content:center;}
.bico svg{width:9px;height:9px;}
.bnm{font-family:var(--mono);font-size:10px;font-weight:500;color:var(--t0);letter-spacing:.03em;}
.bnm em{color:var(--t2);font-style:normal;}
.gbtn{width:22px;height:22px;border:1px solid var(--b);border-radius:3px;background:transparent;color:var(--t2);cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .15s;}
.gbtn:hover{background:var(--bg3);color:var(--t1);}
.ipanel{background:var(--bg);border-bottom:1px solid var(--b);padding:9px 12px;display:flex;flex-direction:column;gap:6px;}
.irow{display:flex;align-items:center;gap:7px;}
.dot{width:5px;height:5px;border-radius:50%;flex-shrink:0;}
.dot.on{background:var(--gr);box-shadow:0 0 0 2px rgba(74,222,128,0.15);}
.dot.off{background:var(--re);}
.dot.chk{background:var(--am);animation:pulse 1.2s ease infinite;}
.ilbl{font-family:var(--mono);font-size:9px;color:var(--t2);letter-spacing:.08em;text-transform:uppercase;width:38px;flex-shrink:0;}
.ival{font-family:var(--mono);font-size:10px;color:var(--t1);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.ival.hi{color:var(--gr);}
.pill{font-family:var(--mono);font-size:9px;font-weight:500;letter-spacing:.05em;padding:1px 7px;border-radius:2px;border:1px solid;flex-shrink:0;}
.pill.ollama{color:var(--bl);border-color:rgba(96,165,250,.3);background:var(--bls);}
.pill.vllm{color:var(--pu);border-color:rgba(167,139,250,.3);background:var(--pus);}
.pill.cli{color:var(--am);border-color:rgba(251,191,36,.3);background:var(--ams);}
.frow{display:flex;align-items:center;gap:7px;padding:7px 12px;background:var(--bg2);border-bottom:1px solid var(--b);min-height:30px;}
.fic{font-family:var(--mono);font-size:9px;color:var(--t2);}
.fn{font-family:var(--mono);font-size:10px;color:var(--t1);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.fn strong{color:var(--t0);font-weight:500;}
.ltag{font-family:var(--mono);font-size:8px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;padding:1px 5px;border-radius:2px;background:var(--bg4);color:var(--t2);border:1px solid var(--b);flex-shrink:0;}
.shdr{display:flex;align-items:center;padding:7px 12px 5px;cursor:pointer;user-select:none;}
.shdr:hover .slbl{color:var(--t1);}
.slbl{font-family:var(--mono);font-size:9px;font-weight:500;letter-spacing:.14em;text-transform:uppercase;color:var(--t2);flex:1;transition:color .12s;}
.chev{font-size:9px;color:var(--t3);transition:transform .18s;}
.chev.open{transform:rotate(0);}.chev.closed{transform:rotate(-90deg);}
.sbody.collapsed{display:none;}
.alist{padding:2px 8px 6px;display:flex;flex-direction:column;gap:1px;}
.abtn{display:flex;align-items:center;gap:9px;padding:7px 8px;background:transparent;border:none;color:var(--t1);font-family:var(--ui);font-size:11px;cursor:pointer;text-align:left;transition:background .1s,color .1s;border-radius:4px;width:100%;}
.abtn:hover{background:var(--bg3);color:var(--t0);}
.abtn:active{transform:scale(.99);}
.aico{width:24px;height:24px;border-radius:3px;display:flex;align-items:center;justify-content:center;flex-shrink:0;}
.amet{display:flex;flex-direction:column;gap:1px;}
.anm{font-size:11px;font-weight:500;line-height:1;}
.asub{font-family:var(--mono);font-size:8.5px;color:var(--t2);line-height:1;}
.abtn:hover .asub{color:var(--t1);}
.abtn.improve .aico{background:var(--grs);color:var(--gr);}
.abtn.fix     .aico{background:var(--res);color:var(--re);}
.abtn.feature .aico{background:var(--bls);color:var(--bl);}
.abtn.refactor .aico{background:var(--pus);color:var(--pu);}
.abtn.explain  .aico{background:var(--cys);color:var(--cy);}
.sep{height:1px;background:var(--b);margin:4px 0;}
.genwrap{padding:2px 8px 8px;}
.gencard{background:var(--bg);border:1px solid var(--b);border-radius:4px;overflow:hidden;}
.gfield{padding:7px 9px;border-bottom:1px solid var(--b);}
.glbl{font-family:var(--mono);font-size:8px;font-weight:500;letter-spacing:.12em;text-transform:uppercase;color:var(--t2);margin-bottom:4px;display:block;}
.ginp{width:100%;padding:5px 7px;background:var(--bg3);border:1px solid var(--b);border-radius:3px;color:var(--t0);font-family:var(--mono);font-size:10px;outline:none;transition:border-color .12s;}
.ginp:focus{border-color:rgba(74,222,128,.4);background:var(--bg4);}
.ginp::placeholder{color:var(--t2);}
.gtxt{width:100%;padding:5px 7px;background:var(--bg3);border:1px solid var(--b);border-radius:3px;color:var(--t0);font-family:var(--mono);font-size:10px;outline:none;resize:vertical;min-height:48px;line-height:1.5;transition:border-color .12s;}
.gtxt:focus{border-color:rgba(74,222,128,.4);background:var(--bg4);}
.gtxt::placeholder{color:var(--t2);}
.grun{width:100%;padding:8px 10px;background:var(--bg2);border:none;border-top:1px solid var(--b);color:var(--gr);font-family:var(--mono);font-size:10px;font-weight:500;letter-spacing:.04em;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px;transition:background .12s;}
.grun:hover{background:var(--grs);}
.cwrap{padding:2px 8px 8px;}
.crow{display:flex;background:var(--bg);border:1px solid var(--b);border-radius:4px;overflow:hidden;}
.cinp{flex:1;padding:7px 9px;background:transparent;border:none;color:var(--t0);font-family:var(--mono);font-size:10px;outline:none;}
.cinp::placeholder{color:var(--t2);}
.csnd{width:32px;flex-shrink:0;background:transparent;border:none;border-left:1px solid var(--b);color:var(--t2);cursor:pointer;font-size:13px;transition:color .12s,background .12s;}
.csnd:hover{color:var(--gr);background:var(--grs);}
.lwrap{padding:2px 8px 8px;}
.lcard{background:var(--bg);border:1px solid var(--b);border-radius:4px;}
.linner{padding:7px 9px;font-family:var(--mono);font-size:9.5px;line-height:1.9;max-height:80px;overflow-y:auto;}
.ll{display:flex;gap:7px;align-items:baseline;animation:fi .15s ease;}
.lts{color:var(--t3);flex-shrink:0;}.lcmd{flex-shrink:0;}.lf{color:var(--t1);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;}
.lok{color:var(--gr);}.lerr{color:var(--re);}.ldim{color:var(--t2);}

/* ── REVIEW PANEL ── */
.review-panel{margin:0;background:var(--bg);border-top:2px solid var(--am);display:none;flex-direction:column;}
.review-panel.visible{display:flex;}
.review-hdr{display:flex;align-items:center;gap:8px;padding:8px 12px;background:rgba(251,191,36,0.06);border-bottom:1px solid var(--b);}
.review-icon{width:14px;height:14px;border-radius:50%;border:1.5px solid var(--am);display:flex;align-items:center;justify-content:center;flex-shrink:0;}
.review-icon svg{width:7px;height:7px;}
.review-title{font-family:var(--mono);font-size:10px;font-weight:500;color:var(--am);flex:1;letter-spacing:.02em;}
.review-body{padding:10px 12px;display:flex;flex-direction:column;gap:8px;max-height:220px;overflow-y:auto;}
.analysis-box{background:var(--bg2);border:1px solid var(--b);border-radius:3px;padding:8px 10px;}
.analysis-lbl{font-family:var(--mono);font-size:8px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--t2);margin-bottom:4px;}
.analysis-txt{font-size:11px;color:var(--t1);line-height:1.55;}
.plan-list{display:flex;flex-direction:column;gap:2px;}
.plan-item{display:flex;gap:6px;align-items:baseline;}
.plan-num{font-family:var(--mono);font-size:9px;color:var(--t3);flex-shrink:0;width:14px;}
.plan-txt{font-size:11px;color:var(--t1);line-height:1.4;}
.diff-file{margin-top:2px;}
.diff-fname{font-family:var(--mono);font-size:9px;color:var(--t2);margin-bottom:3px;letter-spacing:.03em;}
.diff-box{background:var(--bg);border:1px solid var(--b);border-radius:3px;overflow:hidden;font-family:var(--mono);font-size:9px;line-height:1.7;max-height:120px;overflow-y:auto;}
.diff-line{padding:0 8px;white-space:pre;}
.diff-line.add{background:rgba(74,222,128,0.07);color:#4ade80;}
.diff-line.rem{background:rgba(248,113,113,0.07);color:#f87171;}
.diff-line.hdr{color:var(--t3);}
.diff-line.ctx{color:var(--t2);}
.summary-row{font-family:var(--mono);font-size:10px;color:var(--t1);padding:4px 0;}
.action-row{display:flex;gap:6px;padding:8px 12px;border-top:1px solid var(--b);background:var(--bg1);}
.acc-btn{flex:1;padding:8px 10px;background:var(--gr2);border:none;border-radius:4px;color:#000;font-family:var(--mono);font-size:10px;font-weight:500;cursor:pointer;transition:opacity .12s;letter-spacing:.03em;}
.acc-btn:hover{opacity:.85;}
.rej-btn{flex:1;padding:8px 10px;background:var(--bg3);border:1px solid var(--b);border-radius:4px;color:var(--t1);font-family:var(--mono);font-size:10px;cursor:pointer;transition:background .12s;letter-spacing:.03em;}
.rej-btn:hover{background:var(--bg4);}

.footer{display:flex;gap:3px;padding:7px 8px;border-top:1px solid var(--b);}
.fbtn{flex:1;padding:5px 0;background:var(--bg2);border:1px solid var(--b);border-radius:3px;color:var(--t2);font-family:var(--mono);font-size:9px;cursor:pointer;text-align:center;transition:all .1s;letter-spacing:.03em;}
.fbtn:hover{background:var(--bg4);color:var(--t1);border-color:var(--b2);}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
@keyframes fi{from{opacity:0;transform:translateY(2px)}to{opacity:1;transform:translateY(0)}}
::-webkit-scrollbar{width:2px;}::-webkit-scrollbar-track{background:transparent;}::-webkit-scrollbar-thumb{background:var(--bg5);}
</style>
</head>
<body>

<div class="topbar">
  <div class="brand">
    <div class="bico"><svg viewBox="0 0 9 9" fill="none"><path d="M1.5 7.5L4.5 2L7.5 7.5H1.5Z" fill="#4ade80"/></svg></div>
    <span class="bnm">agent<em>.dev</em></span>
  </div>
  <button class="gbtn" onclick="openSettings()" title="Settings">
    <svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor"><path d="M8 10.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zm5.83-2c.04-.26.07-.52.07-.5s-.03-.24-.07-.5l1.17-.9a.27.27 0 0 0 .07-.35l-1.11-1.92a.27.27 0 0 0-.33-.12l-1.38.55a4 4 0 0 0-.93-.54L11.1.36A.27.27 0 0 0 10.83.1H8.17a.27.27 0 0 0-.27.23l-.21 1.46a4 4 0 0 0-.93.54L5.38 1.8a.27.27 0 0 0-.33.12L3.94 3.84a.27.27 0 0 0 .07.35l1.17.9c-.04.26-.08.52-.08.5s.04.24.08.5l-1.17.9a.27.27 0 0 0-.07.35l1.11 1.92c.07.13.22.17.33.12l1.38-.55c.29.21.6.39.93.54l.21 1.46c.04.13.14.23.27.23h2.66c.13 0 .24-.1.27-.23l.21-1.46c.33-.15.64-.33.93-.54l1.38.55c.11.05.26.01.33-.12l1.11-1.92a.27.27 0 0 0-.07-.35l-1.17-.9z"/></svg>
  </button>
</div>

<div class="ipanel">
  <div class="irow"><span class="dot chk" id="connDot"></span><span class="ilbl">mode</span><span class="ival" id="modeVal">—</span><span class="pill ollama" id="bePill">ollama</span></div>
  <div class="irow"><span style="width:5px;flex-shrink:0"></span><span class="ilbl">model</span><span class="ival hi" id="modelVal">connecting...</span></div>
  <div class="irow"><span style="width:5px;flex-shrink:0"></span><span class="ilbl">status</span><span class="ival" id="statusVal">—</span></div>
</div>

<div class="frow">
  <span class="fic">›</span>
  <span class="fn" id="fileEl"><span style="color:var(--t2)">no file open</span></span>
  <span class="ltag" id="langTag" style="display:none"></span>
</div>

<!-- REVIEW PANEL (hidden until agent returns changes) -->
<div class="review-panel" id="reviewPanel">
  <div class="review-hdr">
    <div class="review-icon"><svg viewBox="0 0 8 8" fill="#fbbf24"><path d="M4 1v3.5M4 6v.5" stroke="#fbbf24" stroke-width="1.5" stroke-linecap="round"/></svg></div>
    <span class="review-title" id="reviewTitle">Review changes</span>
  </div>
  <div class="review-body" id="reviewBody"></div>
  <div class="action-row">
    <button class="acc-btn" onclick="doAccept()">✓ Accept &amp; Apply</button>
    <button class="rej-btn" onclick="doReject()">✗ Reject</button>
  </div>
</div>

<div style="padding-top:6px">
  <div class="shdr" onclick="tog('actB',this)"><span class="slbl">actions</span><span class="chev open">▾</span></div>
  <div class="sbody" id="actB">
    <div class="alist">
      <button class="abtn improve" onclick="doCmd('improve')">
        <div class="aico"><svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1l1.9 3.8 4.2.6-3 2.9.7 4.1L8 10.3l-3.8 2 .7-4.1L2 5.4l4.2-.6L8 1z"/></svg></div>
        <div class="amet"><span class="anm">Improve</span><span class="asub">fix · clean · optimize</span></div>
      </button>
      <button class="abtn fix" onclick="doP('fix_bug','Describe the bug (optional)','opt')">
        <div class="aico"><svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor"><path d="M11.5 1a1.5 1.5 0 0 1 0 3h-1v1.1A5 5 0 0 1 13 10H3a5 5 0 0 1 2.5-4.9V4h-1a1.5 1.5 0 0 1 0-3h7zm-5 12.5a1.5 1.5 0 0 0 3 0V13H6.5v.5z"/></svg></div>
        <div class="amet"><span class="anm">Fix Bug</span><span class="asub">targeted repair</span></div>
      </button>
      <button class="abtn feature" onclick="doP('add_feature','Feature to add','req')">
        <div class="aico"><svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor"><path d="M8 2v12M2 8h12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg></div>
        <div class="amet"><span class="anm">Add Feature</span><span class="asub">extend · integrate</span></div>
      </button>
      <button class="abtn refactor" onclick="doCmd('refactor')">
        <div class="aico"><svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor"><path d="M1 4h10M1 8h12M1 12h8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></div>
        <div class="amet"><span class="anm">Refactor</span><span class="asub">structure · naming</span></div>
      </button>
      <button class="abtn explain" onclick="doCmd('explain')">
        <div class="aico"><svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor"><circle cx="8" cy="8" r="6.5" stroke="currentColor" stroke-width="1.5" fill="none"/><path d="M8 7v4M8 5v1" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></div>
        <div class="amet"><span class="anm">Explain</span><span class="asub">understand any code</span></div>
      </button>
    </div>
  </div>
</div>

<div class="sep"></div>
<div>
  <div class="shdr" onclick="tog('genB',this)"><span class="slbl">generate</span><span class="chev open">▾</span></div>
  <div class="sbody" id="genB">
    <div class="genwrap"><div class="gencard">
      <div class="gfield"><label class="glbl">output path</label><input class="ginp" type="text" id="gf" placeholder="utils/helpers.py" autocomplete="off"/></div>
      <div class="gfield" style="border-bottom:none"><label class="glbl">description</label><textarea class="gtxt" id="gd" placeholder="What should this file do?"></textarea></div>
      <button class="grun" onclick="doGen()"><svg width="8" height="8" viewBox="0 0 8 8" fill="currentColor"><polygon points="0,0 8,4 0,8"/></svg> run generation</button>
    </div></div>
  </div>
</div>

<div class="sep"></div>
<div>
  <div class="shdr" onclick="tog('chatB',this)"><span class="slbl">chat</span><span class="chev open">▾</span></div>
  <div class="sbody" id="chatB">
    <div class="cwrap"><div class="crow">
      <input class="cinp" type="text" id="ci" placeholder="Fix the bug in line 42..." onkeydown="if(event.key==='Enter')doChat()" autocomplete="off"/>
      <button class="csnd" onclick="doChat()">↵</button>
    </div></div>
  </div>
</div>

<div class="sep"></div>
<div>
  <div class="shdr" onclick="tog('logB',this)"><span class="slbl">activity</span><span class="chev open">▾</span></div>
  <div class="sbody" id="logB">
    <div class="lwrap"><div class="lcard"><div class="linner" id="logEl">
      <div class="ll"><span class="lts">--:--:--</span><span class="ldim">session started</span></div>
    </div></div></div>
  </div>
</div>

<div class="footer">
  <button class="fbtn" onclick="openSettings()">settings</button>
  <button class="fbtn" onclick="doPing()">ping</button>
  <button class="fbtn" onclick="clearLog()">clear</button>
</div>

<script>
const vscode = acquireVsCodeApi();
window.addEventListener('load', () => vscode.postMessage({type:'ready'}));
const COLS={improve:'#4ade80',fix_bug:'#f87171',add_feature:'#60a5fa',refactor:'#a78bfa',explain:'#22d3ee',generate:'#fbbf24',chat:'#8b91a8'};

let _pending = null; // { token, project, serverUrl, apiKey }

window.addEventListener('message', e => {
  const m = e.data;

  if (m.type==='activeFile'){
    document.getElementById('fileEl').innerHTML='<strong>'+m.name+'</strong>';
    const lt=document.getElementById('langTag');
    lt.textContent=m.lang||''; lt.style.display=m.lang?'':'none';
  }
  if (m.type==='configChanged'){
    const c=m.config;
    document.getElementById('modeVal').textContent=c.mode||'—';
    const bp=document.getElementById('bePill');
    const be=c.mode==='cli'?'cli':(c.backend||'ollama');
    bp.textContent=be; bp.className='pill '+be;
    document.getElementById('modelVal').textContent=(c.backend==='vllm'?c.vllmModel:c.ollamaModel)||'—';
  }
  if (m.type==='health'){
    document.getElementById('connDot').className='dot '+(m.ok?'on':'off');
    document.getElementById('statusVal').textContent=m.ok?'connected':'unreachable';
    if(m.model) document.getElementById('modelVal').textContent=m.model;
    if(m.backend){ const bp=document.getElementById('bePill'); const be=m.mode==='cli'?'cli':m.backend; bp.textContent=be; bp.className='pill '+be; }
    if(m.mode) document.getElementById('modeVal').textContent=m.mode;
  }
  if (m.type==='runStart'){
    addLog(m.mode,m.file||'',null);
    document.getElementById('statusVal').textContent='thinking...';
    document.getElementById('connDot').className='dot chk';
    hideReview();
  }
  if (m.type==='agentResult'){
    document.getElementById('connDot').className='dot on';
    const r=m.result;
    if (r.changes && r.changes.length>0 && r.preview_token){
      _pending={token:r.preview_token, project:m.project, serverUrl:m.serverUrl, apiKey:m.apiKey};
      showReview(r, m.mode);
      document.getElementById('statusVal').textContent='awaiting review';
      addLog(m.mode, m.file||'', 'pending');
    } else {
      document.getElementById('statusVal').textContent='ready';
      addLog(m.mode, m.file||'', 'ok');
    }
  }
  if (m.type==='applied'){
    hideReview();
    document.getElementById('statusVal').textContent='ready';
    addLog('applied', (m.files||[]).join(', '), 'ok');
  }
  if (m.type==='rejected'){
    hideReview();
    document.getElementById('statusVal').textContent='ready';
    addLog('rejected','—',null);
  }
  if (m.type==='runDone'){
    document.getElementById('connDot').className='dot on';
    document.getElementById('statusVal').textContent=m.ok?'ready':'error';
    if(!m.ok) addLog(m.mode||'?',m.error||'failed','err');
  }
});

function showReview(r, mode){
  const panel=document.getElementById('reviewPanel');
  const body=document.getElementById('reviewBody');
  const title=document.getElementById('reviewTitle');

  title.textContent = r.changes.length+' change'+(r.changes.length===1?'':'s')+' proposed';

  let html='';

  // Analysis
  if(r.analysis){
    html+=\`<div class="analysis-box"><div class="analysis-lbl">analysis</div><div class="analysis-txt">\${esc(r.analysis)}</div></div>\`;
  }

  // Plan
  if(r.plan&&r.plan.length){
    html+='<div class="plan-list">';
    r.plan.forEach((p,i)=>{html+=\`<div class="plan-item"><span class="plan-num">\${i+1}.</span><span class="plan-txt">\${esc(p)}</span></div>\`;});
    html+='</div>';
  }

  // Diffs
  r.changes.forEach(c=>{
    if(!c.diff_lines||!c.diff_lines.length) return;
    html+=\`<div class="diff-file"><div class="diff-fname">→ \${esc(c.file)}\${c.reason?' <span style="color:var(--t3)">— '+esc(c.reason)+'</span>':''}</div><div class="diff-box">\`;
    c.diff_lines.slice(0,60).forEach(line=>{
      const cls=line.startsWith('+')?'add':line.startsWith('-')?'rem':line.startsWith('@@')?'hdr':'ctx';
      if(cls==='ctx'&&(line.startsWith('---')||line.startsWith('+++'))) return; // skip file headers
      html+=\`<div class="diff-line \${cls}">\${esc(line)}</div>\`;
    });
    if(c.diff_lines.length>60) html+=\`<div class="diff-line ctx">  ... \${c.diff_lines.length-60} more lines</div>\`;
    html+='</div></div>';
  });

  // Summary
  if(r.summary) html+=\`<div class="summary-row">\${esc(r.summary)}</div>\`;

  body.innerHTML=html;
  panel.classList.add('visible');
}

function hideReview(){
  document.getElementById('reviewPanel').classList.remove('visible');
  document.getElementById('reviewBody').innerHTML='';
  _pending=null;
}

function doAccept(){
  if(!_pending) return;
  vscode.postMessage({type:'accept',token:_pending.token,project:_pending.project,serverUrl:_pending.serverUrl,apiKey:_pending.apiKey});
}
function doReject(){
  if(!_pending) return;
  vscode.postMessage({type:'reject',token:_pending.token,project:_pending.project,serverUrl:_pending.serverUrl,apiKey:_pending.apiKey});
}

function doCmd(cmd,msg){ vscode.postMessage({type:'command',command:cmd,message:msg||''}); }
function doP(cmd,label,mode){
  const msg=prompt(label+(mode==='req'?' (required):':' — Enter to skip:'));
  if(mode==='req'&&!msg) return;
  doCmd(cmd,msg||'');
}
function doGen(){
  const f=document.getElementById('gf').value.trim();
  const d=document.getElementById('gd').value.trim();
  if(!f){alert('Enter an output path.');return;}
  if(!d){alert('Enter a description.');return;}
  vscode.postMessage({type:'command',command:'generate',file:f,message:d});
}
function doChat(){
  const el=document.getElementById('ci');
  const msg=el.value.trim();
  if(!msg) return;
  doCmd('chat',msg); el.value='';
}
function doPing(){
  document.getElementById('connDot').className='dot chk';
  document.getElementById('statusVal').textContent='pinging...';
  vscode.postMessage({type:'command',command:'ping'});
}
function openSettings(){vscode.postMessage({type:'openSettings'});}

function addLog(cmd,file,status){
  const log=document.getElementById('logEl');
  const ts=new Date().toLocaleTimeString('en',{hour12:false});
  const col=COLS[cmd]||'#8b91a8';
  const st=status==='ok'?' <span class="lok">✓</span>':status==='err'?' <span class="lerr">✗</span>':status==='pending'?' <span style="color:var(--am)">●</span>':'';
  const e=document.createElement('div');
  e.className='ll';
  e.innerHTML='<span class="lts">'+ts+'</span><span class="lcmd" style="color:'+col+'">'+cmd+'</span><span class="lf"> '+esc(file)+st+'</span>';
  log.appendChild(e); log.scrollTop=log.scrollHeight;
}
function clearLog(){document.getElementById('logEl').innerHTML='<div class="ll"><span class="lts">--:--:--</span><span class="ldim">cleared</span></div>';}
function tog(id,hdr){const b=document.getElementById(id);const ch=hdr.querySelector('.chev');const closed=b.classList.toggle('collapsed');ch.className='chev '+(closed?'closed':'open');}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
</script>
</body>
</html>`;
}

function deactivate(){if(hTimer)clearTimeout(hTimer);}
module.exports={activate,deactivate};