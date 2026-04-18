package com.jarvis.android.system.builder

import com.jarvis.android.domain.model.AppTemplate
import com.jarvis.android.domain.model.AppType
import com.jarvis.android.domain.model.TemplateCategory
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Built-in app templates for the JARVIS App Builder.
 *
 * All WebView templates are fully self-contained HTML5 files with:
 *   - Dark JARVIS-themed styling (obsidian + gold)
 *   - No external dependencies (works fully offline)
 *   - Responsive layout for phone screens
 *
 * Shell templates follow POSIX sh syntax and work under Bash/Busybox.
 */
@Singleton
class AppTemplateRegistry @Inject constructor() {

    fun getAll(): List<AppTemplate> = TEMPLATES

    fun getById(id: String): AppTemplate? = TEMPLATES.find { it.id == id }

    fun getByCategory(category: TemplateCategory): List<AppTemplate> =
        TEMPLATES.filter { it.category == category }

    companion object {
        val TEMPLATES: List<AppTemplate> = listOf(
            calculator(),
            notepad(),
            timer(),
            systemDashboard(),
            todoList(),
            unitConverter(),
            systemInfoScript(),
            networkScanScript(),
        )
    }
}

// ── WebView templates ─────────────────────────────────────────────────────────

private fun calculator() = AppTemplate(
    id          = "tpl_calculator",
    name        = "Calculator",
    description = "Scientific calculator with history log and dark JARVIS theme",
    category    = TemplateCategory.UTILITY,
    type        = AppType.WEBVIEW,
    tags        = listOf("math", "calculator", "utility"),
    sourceCode  = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>Calculator</title>
<style>
  :root { --gold:#C9A84C; --bg:#0A0A0A; --surface:#141414; --surface2:#1E1E1E; --text:#F0EDE8; --dim:#8A8070; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--text); font-family:system-ui,sans-serif; min-height:100vh; display:flex; flex-direction:column; align-items:center; justify-content:center; padding:16px; }
  h1 { color:var(--gold); font-size:14px; letter-spacing:2px; text-transform:uppercase; margin-bottom:12px; }
  .calc { width:100%; max-width:360px; background:var(--surface); border:1px solid #3D2E0F; border-radius:16px; overflow:hidden; }
  .display { background:var(--bg); padding:16px; text-align:right; min-height:80px; display:flex; flex-direction:column; justify-content:flex-end; }
  .expr { color:var(--dim); font-size:13px; min-height:18px; }
  .result { color:var(--text); font-size:32px; font-weight:300; overflow:hidden; text-overflow:ellipsis; }
  .history { background:var(--surface2); padding:8px 12px; max-height:80px; overflow-y:auto; font-size:11px; color:var(--dim); }
  .history div { padding:2px 0; border-bottom:1px solid #1a1a1a; }
  .btns { display:grid; grid-template-columns:repeat(4,1fr); gap:1px; background:#0A0A0A; }
  button { background:var(--surface2); border:none; color:var(--text); font-size:18px; padding:18px 0; cursor:pointer; transition:background .1s; }
  button:active { background:#2a2a2a; }
  .op  { color:var(--gold); }
  .eq  { background:var(--gold); color:#0A0A0A; font-weight:bold; }
  .eq:active { background:#b8922a; }
  .clr { color:#CF4A3C; }
</style>
</head>
<body>
<h1>JARVIS Calc</h1>
<div class="calc">
  <div class="history" id="hist"></div>
  <div class="display"><div class="expr" id="expr"></div><div class="result" id="res">0</div></div>
  <div class="btns">
    <button class="clr" onclick="clr()">AC</button>
    <button class="op" onclick="app('±')">±</button>
    <button class="op" onclick="app('%')">%</button>
    <button class="op" onclick="app('/')">÷</button>
    <button onclick="app('7')">7</button><button onclick="app('8')">8</button><button onclick="app('9')">9</button>
    <button class="op" onclick="app('*')">×</button>
    <button onclick="app('4')">4</button><button onclick="app('5')">5</button><button onclick="app('6')">6</button>
    <button class="op" onclick="app('-')">−</button>
    <button onclick="app('1')">1</button><button onclick="app('2')">2</button><button onclick="app('3')">3</button>
    <button class="op" onclick="app('+')">+</button>
    <button onclick="app('0')" style="grid-column:span 2">0</button>
    <button onclick="app('.')">.</button>
    <button class="eq" onclick="calc()">=</button>
  </div>
</div>
<script>
let expr='',hist=[];
const R=document.getElementById('res'),E=document.getElementById('expr'),H=document.getElementById('hist');
function app(v){
  if(v==='±'){expr=expr?String(-parseFloat(expr)):expr;R.textContent=expr||'0';return;}
  if(v==='%'){try{expr=String(eval(expr)/100);}catch(e){}R.textContent=expr;return;}
  expr+=v;R.textContent=expr;E.textContent='';
}
function calc(){
  try{
    const result=Function('"use strict";return('+expr+')')();
    E.textContent=expr+'=';
    hist.unshift(expr+'= '+result);if(hist.length>10)hist.pop();
    H.innerHTML=hist.map(h=>'<div>'+h+'</div>').join('');
    expr=String(result);R.textContent=expr;
  }catch(e){R.textContent='Error';expr='';}
}
function clr(){expr='';R.textContent='0';E.textContent='';}
document.addEventListener('keydown',e=>{
  if('0123456789.+-*/%'.includes(e.key))app(e.key);
  else if(e.key==='Enter')calc();
  else if(e.key==='Backspace'){expr=expr.slice(0,-1);R.textContent=expr||'0';}
  else if(e.key==='Escape')clr();
});
</script>
</body></html>""",
)

private fun notepad() = AppTemplate(
    id          = "tpl_notepad",
    name        = "Notepad",
    description = "Minimal dark notepad with auto-save to localStorage and export",
    category    = TemplateCategory.PRODUCTIVITY,
    type        = AppType.WEBVIEW,
    tags        = listOf("notes", "text", "productivity"),
    sourceCode  = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Notepad</title>
<style>
  :root{--gold:#C9A84C;--bg:#0A0A0A;--surface:#141414;--text:#F0EDE8;--dim:#8A8070;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;height:100vh;display:flex;flex-direction:column;}
  header{background:var(--surface);border-bottom:1px solid #3D2E0F;padding:12px 16px;display:flex;align-items:center;gap:12px;}
  h1{color:var(--gold);font-size:14px;letter-spacing:2px;text-transform:uppercase;flex:1;}
  button{background:#1E1E1E;border:1px solid #3D2E0F;color:var(--gold);padding:6px 12px;border-radius:6px;font-size:12px;cursor:pointer;}
  button:active{background:#3D2E0F;}
  .status{font-size:11px;color:var(--dim);}
  textarea{flex:1;background:var(--bg);color:var(--text);border:none;outline:none;padding:16px;font-size:14px;line-height:1.6;resize:none;font-family:inherit;}
  textarea::selection{background:#3D2E0F;}
</style>
</head>
<body>
<header>
  <h1>JARVIS Notes</h1>
  <span class="status" id="st">Saved</span>
  <button onclick="exportNote()">Export</button>
  <button onclick="clearNote()">Clear</button>
</header>
<textarea id="ed" placeholder="Start typing…" spellcheck="true"></textarea>
<script>
const ed=document.getElementById('ed'),st=document.getElementById('st');
ed.value=localStorage.getItem('jarvis_note')||'';
let timer;
ed.addEventListener('input',()=>{
  st.textContent='Unsaved';
  clearTimeout(timer);
  timer=setTimeout(()=>{localStorage.setItem('jarvis_note',ed.value);st.textContent='Saved ✓';},800);
});
function exportNote(){
  const a=document.createElement('a');
  a.href='data:text/plain;charset=utf-8,'+encodeURIComponent(ed.value);
  a.download='note_'+new Date().toISOString().slice(0,10)+'.txt';
  a.click();
}
function clearNote(){
  if(confirm('Clear all text?')){ed.value='';localStorage.removeItem('jarvis_note');st.textContent='Cleared';}
}
</script>
</body></html>""",
)

private fun timer() = AppTemplate(
    id          = "tpl_timer",
    name        = "Timer & Stopwatch",
    description = "Countdown timer and stopwatch with lap tracking",
    category    = TemplateCategory.UTILITY,
    type        = AppType.WEBVIEW,
    tags        = listOf("timer", "stopwatch", "time"),
    sourceCode  = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>Timer</title>
<style>
  :root{--gold:#C9A84C;--bg:#0A0A0A;--surface:#141414;--surface2:#1E1E1E;--text:#F0EDE8;--dim:#8A8070;--green:#3CAF6E;--red:#CF4A3C;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;min-height:100vh;padding:16px;display:flex;flex-direction:column;align-items:center;}
  h1{color:var(--gold);font-size:13px;letter-spacing:2px;text-transform:uppercase;margin:16px 0 24px;}
  .tabs{display:flex;gap:4px;margin-bottom:24px;}
  .tab{padding:8px 20px;border-radius:20px;border:none;font-size:13px;cursor:pointer;background:var(--surface2);color:var(--dim);}
  .tab.active{background:#4A3A1E;color:var(--gold);border:1px solid var(--gold);}
  .display{font-size:64px;font-weight:100;letter-spacing:4px;margin:24px 0;font-variant-numeric:tabular-nums;}
  .btns{display:flex;gap:12px;margin-bottom:24px;}
  .btn{width:72px;height:72px;border-radius:50%;border:2px solid;font-size:13px;font-weight:600;cursor:pointer;background:transparent;}
  .btn-go{border-color:var(--green);color:var(--green);}
  .btn-stop{border-color:var(--red);color:var(--red);}
  .btn-reset{border-color:var(--dim);color:var(--dim);}
  .btn-lap{border-color:var(--gold);color:var(--gold);}
  .btn:active{opacity:.7;}
  .laps{width:100%;max-width:320px;max-height:200px;overflow-y:auto;}
  .lap{display:flex;justify-content:space-between;padding:8px 12px;border-bottom:1px solid #1a1a1a;font-size:13px;color:var(--dim);}
  input[type=number]{background:var(--surface2);border:1px solid #3D2E0F;color:var(--text);padding:8px 12px;border-radius:8px;font-size:24px;width:100px;text-align:center;outline:none;}
  .timer-set{display:flex;align-items:center;gap:8px;margin-bottom:16px;color:var(--dim);font-size:13px;}
  #page{display:none;} #page.active{display:flex;flex-direction:column;align-items:center;}
</style>
</head>
<body>
<h1>JARVIS Timer</h1>
<div class="tabs">
  <button class="tab active" onclick="show('sw')">Stopwatch</button>
  <button class="tab" onclick="show('tm')">Timer</button>
</div>
<div id="sw" class="page active">
  <div class="display" id="sw-d">00:00.00</div>
  <div class="btns">
    <button class="btn btn-go" id="sw-go" onclick="swToggle()">START</button>
    <button class="btn btn-lap" onclick="lap()">LAP</button>
    <button class="btn btn-reset" onclick="swReset()">RESET</button>
  </div>
  <div class="laps" id="laps"></div>
</div>
<div id="tm" class="page">
  <div class="timer-set">
    <input type="number" id="tm-m" value="5" min="0" max="99"> m
    <input type="number" id="tm-s" value="0" min="0" max="59"> s
  </div>
  <div class="display" id="tm-d">05:00</div>
  <div class="btns">
    <button class="btn btn-go" id="tm-go" onclick="tmToggle()">START</button>
    <button class="btn btn-reset" onclick="tmReset()">RESET</button>
  </div>
</div>
<script>
let swMs=0,swInt,swLaps=[],lapMs=0;
let tmMs=0,tmInt,tmRunning=false;
function fmt(ms){const t=ms/10,cs=Math.floor(t%100),s=Math.floor(t/100%60),m=Math.floor(t/6000);return String(m).padStart(2,'0')+':'+String(s).padStart(2,'0')+'.'+String(cs).padStart(2,'0');}
function fmtS(ms){const s=Math.floor(ms/1000),m=Math.floor(s/60);return String(m).padStart(2,'0')+':'+String(s%60).padStart(2,'0');}
function show(t){document.querySelectorAll('.page,.tab').forEach(e=>e.classList.remove('active'));document.getElementById(t).classList.add('active');document.querySelectorAll('.tab')[t==='sw'?0:1].classList.add('active');}
// Stopwatch
let swLast;
function swToggle(){if(swInt){clearInterval(swInt);swInt=null;document.getElementById('sw-go').textContent='START';}else{swLast=Date.now()-swMs*10;swInt=setInterval(()=>{swMs=Math.floor((Date.now()-swLast)/10);document.getElementById('sw-d').textContent=fmt(swMs*10);},10);document.getElementById('sw-go').textContent='STOP';}}
function lap(){if(!swInt)return;const cur=swMs*10-lapMs;swLaps.unshift({n:swLaps.length+1,lap:cur,total:swMs*10});lapMs=swMs*10;document.getElementById('laps').innerHTML=swLaps.map(l=>'<div class="lap"><span>Lap '+l.n+'</span><span>'+fmt(l.lap)+'</span><span>'+fmt(l.total)+'</span></div>').join('');}
function swReset(){clearInterval(swInt);swInt=null;swMs=0;lapMs=0;swLaps=[];document.getElementById('sw-d').textContent='00:00.00';document.getElementById('sw-go').textContent='START';document.getElementById('laps').innerHTML='';}
// Timer
function tmToggle(){if(tmRunning){clearInterval(tmInt);tmRunning=false;document.getElementById('tm-go').textContent='RESUME';}else{if(!tmMs){const m=parseInt(document.getElementById('tm-m').value)||0,s=parseInt(document.getElementById('tm-s').value)||0;tmMs=(m*60+s)*1000;}if(tmMs<=0)return;tmRunning=true;document.getElementById('tm-go').textContent='PAUSE';tmInt=setInterval(()=>{tmMs-=100;document.getElementById('tm-d').textContent=fmtS(Math.max(0,tmMs));if(tmMs<=0){clearInterval(tmInt);tmRunning=false;document.getElementById('tm-go').textContent='START';if(navigator.vibrate)navigator.vibrate([300,100,300]);}},100);}}
function tmReset(){clearInterval(tmInt);tmRunning=false;tmMs=0;document.getElementById('tm-d').textContent=fmtS((parseInt(document.getElementById('tm-m').value||5)*60+parseInt(document.getElementById('tm-s').value||0))*1000);document.getElementById('tm-go').textContent='START';}
</script>
</body></html>""",
)

private fun systemDashboard() = AppTemplate(
    id          = "tpl_sysdash",
    name        = "System Dashboard",
    description = "Real-time battery, storage, and device info dashboard",
    category    = TemplateCategory.SYSTEM,
    type        = AppType.WEBVIEW,
    tags        = listOf("system", "battery", "info", "dashboard"),
    sourceCode  = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>System Dashboard</title>
<style>
  :root{--gold:#C9A84C;--bg:#0A0A0A;--surface:#141414;--surface2:#1E1E1E;--text:#F0EDE8;--dim:#8A8070;--green:#3CAF6E;--red:#CF4A3C;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;padding:16px;}
  h1{color:var(--gold);font-size:13px;letter-spacing:2px;text-transform:uppercase;margin-bottom:20px;text-align:center;}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;}
  .card{background:var(--surface);border:1px solid #3D2E0F;border-radius:12px;padding:14px;}
  .card h2{font-size:10px;color:var(--dim);letter-spacing:1px;text-transform:uppercase;margin-bottom:10px;}
  .val{font-size:28px;font-weight:300;color:var(--text);}
  .sub{font-size:11px;color:var(--dim);margin-top:4px;}
  .bar{height:6px;background:#1E1E1E;border-radius:3px;margin-top:8px;overflow:hidden;}
  .bar-fill{height:100%;border-radius:3px;background:var(--gold);transition:width .5s;}
  .bar-fill.warn{background:var(--red);}
  .full{grid-column:span 2;}
  .row{display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #1a1a1a;font-size:12px;}
  .row:last-child{border-bottom:none;}
  .row span:last-child{color:var(--gold);}
</style>
</head>
<body>
<h1>System Dashboard</h1>
<div class="grid" id="dash">
  <div class="card"><h2>Loading…</h2></div>
</div>
<script>
function fmt(b){return b>=1e9?(b/1e9).toFixed(1)+' GB':b>=1e6?(b/1e6).toFixed(0)+' MB':(b/1e3).toFixed(0)+' KB';}
function upd(){
  const nav=navigator,perf=performance;
  const mem=perf.memory||{};
  const ua=nav.userAgent;
  const rows=[
    {t:'Browser',v:ua.includes('Chrome')?'Chrome':(ua.includes('Firefox')?'Firefox':'WebView')},
    {t:'Platform',v:nav.platform||'Android'},
    {t:'Cores',v:nav.hardwareConcurrency||'?'},
    {t:'Language',v:nav.language},
    {t:'Online',v:nav.onLine?'Yes':'No'},
    {t:'Touch',v:'ontouchstart' in window?'Yes':'No'},
    {t:'Screen',v:screen.width+'×'+screen.height},
    {t:'Pixel Ratio',v:window.devicePixelRatio+'x'},
  ];
  const memUsed=mem.usedJSHeapSize||0,memTotal=mem.jsHeapSizeLimit||0;
  const memPct=memTotal>0?Math.round(memUsed/memTotal*100):0;
  document.getElementById('dash').innerHTML=
    '<div class="card"><h2>JS Heap</h2><div class="val">'+memPct+'%</div>'+
    '<div class="sub">'+(memUsed?fmt(memUsed)+' / '+fmt(memTotal):'N/A')+'</div>'+
    '<div class="bar"><div class="bar-fill'+(memPct>80?' warn':'')+'" style="width:'+memPct+'%"></div></div></div>'+
    '<div class="card"><h2>Screen</h2><div class="val">'+screen.width+'<span style="font-size:14px">px</span></div>'+
    '<div class="sub">'+screen.height+'px tall · '+window.devicePixelRatio+'x DPR</div></div>'+
    '<div class="card full"><h2>Device Info</h2>'+rows.map(r=>'<div class="row"><span>'+r.t+'</span><span>'+r.v+'</span></div>').join('')+'</div>';
}
upd();setInterval(upd,3000);
</script>
</body></html>""",
)

private fun todoList() = AppTemplate(
    id          = "tpl_todo",
    name        = "To-Do List",
    description = "Minimal task manager with priorities and persistence",
    category    = TemplateCategory.PRODUCTIVITY,
    type        = AppType.WEBVIEW,
    tags        = listOf("todo", "tasks", "productivity"),
    sourceCode  = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>To-Do</title>
<style>
  :root{--gold:#C9A84C;--bg:#0A0A0A;--surface:#141414;--surface2:#1E1E1E;--text:#F0EDE8;--dim:#8A8070;--green:#3CAF6E;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;padding:16px;max-width:480px;margin:auto;}
  h1{color:var(--gold);font-size:13px;letter-spacing:2px;text-transform:uppercase;margin-bottom:16px;}
  .add-row{display:flex;gap:8px;margin-bottom:16px;}
  input{flex:1;background:var(--surface2);border:1px solid #3D2E0F;color:var(--text);padding:10px 14px;border-radius:10px;font-size:14px;outline:none;}
  input:focus{border-color:var(--gold);}
  select{background:var(--surface2);border:1px solid #3D2E0F;color:var(--gold);padding:10px 8px;border-radius:10px;font-size:13px;outline:none;}
  button.add{background:var(--gold);border:none;color:#0A0A0A;padding:10px 16px;border-radius:10px;font-weight:700;font-size:14px;cursor:pointer;}
  .task{display:flex;align-items:center;gap:10px;padding:12px;background:var(--surface);border:1px solid #1E1E1E;border-radius:10px;margin-bottom:8px;}
  .task.done .label{text-decoration:line-through;opacity:.4;}
  .dot{width:10px;height:10px;border-radius:50%;flex-shrink:0;}
  .p1{background:#CF4A3C;} .p2{background:#E0A030;} .p3{background:var(--green);}
  .label{flex:1;font-size:14px;line-height:1.4;}
  .del{background:none;border:none;color:var(--dim);font-size:18px;cursor:pointer;padding:0 4px;line-height:1;}
  .del:hover{color:#CF4A3C;}
  .empty{text-align:center;color:var(--dim);font-size:13px;padding:32px;}
  .filter{display:flex;gap:6px;margin-bottom:12px;}
  .ftab{background:var(--surface2);border:1px solid #1E1E1E;color:var(--dim);padding:5px 12px;border-radius:16px;font-size:12px;cursor:pointer;}
  .ftab.on{background:#4A3A1E;color:var(--gold);border-color:var(--gold);}
</style>
</head>
<body>
<h1>JARVIS Tasks</h1>
<div class="add-row">
  <input id="inp" placeholder="New task…" onkeydown="if(event.key==='Enter')add()">
  <select id="pri"><option value="1">!!!  High</option><option value="2" selected>!!  Med</option><option value="3">!  Low</option></select>
  <button class="add" onclick="add()">+</button>
</div>
<div class="filter">
  <button class="ftab on" onclick="setFilter('all',this)">All</button>
  <button class="ftab" onclick="setFilter('open',this)">Open</button>
  <button class="ftab" onclick="setFilter('done',this)">Done</button>
</div>
<div id="list"></div>
<script>
let tasks=JSON.parse(localStorage.getItem('jv_tasks')||'[]'),filter='all';
function save(){localStorage.setItem('jv_tasks',JSON.stringify(tasks));}
function add(){const v=document.getElementById('inp').value.trim();if(!v)return;tasks.unshift({id:Date.now(),text:v,pri:+document.getElementById('pri').value,done:false});save();render();document.getElementById('inp').value='';}
function toggle(id){const t=tasks.find(x=>x.id===id);if(t){t.done=!t.done;save();render();}}
function del(id){tasks=tasks.filter(x=>x.id!==id);save();render();}
function setFilter(f,el){filter=f;document.querySelectorAll('.ftab').forEach(e=>e.classList.remove('on'));el.classList.add('on');render();}
function render(){
  const vis=tasks.filter(t=>filter==='all'||(filter==='open'&&!t.done)||(filter==='done'&&t.done));
  document.getElementById('list').innerHTML=vis.length?vis.map(t=>'<div class="task'+(t.done?' done':'')+'"><div class="dot p'+t.pri+'" onclick="toggle('+t.id+')" style="cursor:pointer"></div><span class="label" onclick="toggle('+t.id+')" style="cursor:pointer">'+t.text+'</span><button class="del" onclick="del('+t.id+')">\xD7</button></div>').join(''):'<div class="empty">No tasks here</div>';
}
render();
</script>
</body></html>""",
)

private fun unitConverter() = AppTemplate(
    id          = "tpl_units",
    name        = "Unit Converter",
    description = "Convert between common units: length, weight, temperature, data",
    category    = TemplateCategory.UTILITY,
    type        = AppType.WEBVIEW,
    tags        = listOf("units", "converter", "math"),
    sourceCode  = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Unit Converter</title>
<style>
  :root{--gold:#C9A84C;--bg:#0A0A0A;--surface:#141414;--surface2:#1E1E1E;--text:#F0EDE8;--dim:#8A8070;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;padding:16px;max-width:440px;margin:auto;}
  h1{color:var(--gold);font-size:13px;letter-spacing:2px;text-transform:uppercase;margin-bottom:16px;text-align:center;}
  .cats{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:20px;}
  .cat{background:var(--surface2);border:1px solid #1E1E1E;color:var(--dim);padding:6px 14px;border-radius:16px;font-size:12px;cursor:pointer;}
  .cat.on{background:#4A3A1E;color:var(--gold);border-color:var(--gold);}
  .row{display:flex;gap:10px;align-items:center;margin-bottom:12px;}
  input{flex:1;background:var(--surface2);border:1px solid #3D2E0F;color:var(--text);padding:10px 14px;border-radius:10px;font-size:18px;outline:none;}
  input:focus{border-color:var(--gold);}
  select{background:var(--surface2);border:1px solid #3D2E0F;color:var(--gold);padding:10px 8px;border-radius:10px;font-size:13px;outline:none;flex-shrink:0;}
  .result{background:var(--surface);border:1px solid #3D2E0F;border-radius:10px;padding:12px 16px;text-align:center;color:var(--gold);font-size:22px;}
</style>
</head>
<body>
<h1>Unit Converter</h1>
<div class="cats" id="cats"></div>
<div class="row"><input id="val" type="number" value="1" oninput="conv()"><select id="from" onchange="conv()"></select></div>
<div class="row" style="justify-content:center"><span style="color:var(--dim);font-size:20px">→</span></div>
<div class="row" style="justify-content:flex-end"><select id="to" onchange="conv()"></select></div>
<div class="result" id="res">-</div>
<script>
const C={
  Length:{m:1,km:1e3,cm:0.01,mm:0.001,ft:0.3048,in:0.0254,mi:1609.34,yd:0.9144},
  Weight:{kg:1,g:0.001,lb:0.453592,oz:0.0283495,t:1000,mg:1e-6},
  Temperature:{C:null,F:null,K:null},
  Data:{B:1,KB:1024,MB:1048576,GB:1073741824,TB:1099511627776,bit:0.125},
  Speed:{mps:1,'km/h':1/3.6,mph:0.44704,knot:0.514444},
  Area:{m2:1,km2:1e6,cm2:1e-4,ft2:0.0929,acre:4046.86,ha:10000},
};
let cat='Length';
function setcat(c,el){cat=c;document.querySelectorAll('.cat').forEach(e=>e.classList.remove('on'));el.classList.add('on');const u=Object.keys(C[c]);['from','to'].forEach((id,i)=>{const s=document.getElementById(id);s.innerHTML=u.map(x=>'<option>'+x+'</option>').join('');s.selectedIndex=i<u.length-1?i:0;});conv();}
document.getElementById('cats').innerHTML=Object.keys(C).map(k=>'<button class="cat'+(k==='Length'?' on':'')+'" onclick="setcat(\''+k+'\',this)">'+k+'</button>').join('');
setcat('Length',document.querySelector('.cat.on'));
function conv(){
  const v=parseFloat(document.getElementById('val').value),f=document.getElementById('from').value,t=document.getElementById('to').value;
  if(isNaN(v)){document.getElementById('res').textContent='-';return;}
  let r;
  if(cat==='Temperature'){
    const toC={C:x=>x,F:x=>(x-32)*5/9,K:x=>x-273.15};
    const fromC={C:x=>x,F:x=>x*9/5+32,K:x=>x+273.15};
    r=fromC[t](toC[f](v));
  }else{r=v*C[cat][f]/C[cat][t];}
  document.getElementById('res').textContent=+r.toPrecision(6)+' '+t;
}
</script>
</body></html>""",
)

// ── Shell script templates ─────────────────────────────────────────────────────

private fun systemInfoScript() = AppTemplate(
    id          = "tpl_sysinfo_sh",
    name        = "System Info Script",
    description = "Comprehensive device info: CPU, RAM, battery, storage, kernel",
    category    = TemplateCategory.SYSTEM,
    type        = AppType.SHELL,
    tags        = listOf("system", "info", "shell"),
    sourceCode  = """#!/system/bin/sh
# JARVIS System Info — generated by App Builder
# Works on rooted Android with /system/bin/sh

RESET='\033[0m'; GOLD='\033[38;5;178m'; DIM='\033[2m'; BOLD='\033[1m'; GREEN='\033[32m'; RED='\033[31m'

hr() { printf '%s\n' "──────────────────────────────────────────"; }
section() { echo ""; printf "${'$'}{GOLD}${'$'}{BOLD}▸ %s${'$'}{RESET}\n" "${'$'}1"; hr; }

section "DEVICE"
echo "  Model      : $(getprop ro.product.model 2>/dev/null)"
echo "  Brand      : $(getprop ro.product.brand 2>/dev/null)"
echo "  Android    : $(getprop ro.build.version.release) (SDK $(getprop ro.build.version.sdk))"
echo "  Kernel     : $(uname -r 2>/dev/null)"
echo "  Arch       : $(uname -m 2>/dev/null)"
echo "  Build      : $(getprop ro.build.id 2>/dev/null)"
echo "  Serial     : $(getprop ro.serialno 2>/dev/null | head -c 8)..."

section "CPU"
if [ -f /proc/cpuinfo ]; then
  grep "Hardware" /proc/cpuinfo 2>/dev/null | head -1 | sed 's/.*: /  Hardware   : /'
  grep "processor" /proc/cpuinfo 2>/dev/null | wc -l | xargs -I{} echo "  Cores      : {}"
  [ -f /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq ] && \
    echo "  Max Freq   : $(($(cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq 2>/dev/null)/1000)) MHz"
fi

section "MEMORY"
if [ -f /proc/meminfo ]; then
  TOTAL=$(grep MemTotal /proc/meminfo | awk '{print int(${'$'}2/1024)" MB"}')
  FREE=$(grep MemAvailable /proc/meminfo | awk '{print int(${'$'}2/1024)" MB"}')
  echo "  Total      : ${'$'}TOTAL"
  echo "  Available  : ${'$'}FREE"
fi

section "BATTERY"
BAT=/sys/class/power_supply/battery
[ -d ${'$'}BAT ] && {
  [ -f ${'$'}BAT/capacity ]    && echo "  Level      : $(cat ${'$'}BAT/capacity)%"
  [ -f ${'$'}BAT/status ]      && echo "  Status     : $(cat ${'$'}BAT/status)"
  [ -f ${'$'}BAT/health ]      && echo "  Health     : $(cat ${'$'}BAT/health)"
  [ -f ${'$'}BAT/voltage_now ] && echo "  Voltage    : $(($(cat ${'$'}BAT/voltage_now)/1000)) mV"
  [ -f ${'$'}BAT/temp ]        && echo "  Temp       : $(($(cat ${'$'}BAT/temp)/10))°C"
}

section "STORAGE"
df -h /data 2>/dev/null | tail -1 | awk '{printf "  /data      : %s used of %s (%s)\n",${'$'}3,${'$'}2,${'$'}5}'
df -h /sdcard 2>/dev/null | tail -1 | awk '{printf "  /sdcard    : %s used of %s (%s)\n",${'$'}3,${'$'}2,${'$'}5}'

section "NETWORK"
ip addr show wlan0 2>/dev/null | grep "inet " | awk '{print "  WiFi IP    :", ${'$'}2}'
ip addr show rmnet0 2>/dev/null | grep "inet " | awk '{print "  Mobile IP  :", ${'$'}2}'
getprop gsm.operator.alpha 2>/dev/null | xargs -I{} echo "  Carrier    : {}"

echo ""
printf "${'$'}{DIM}JARVIS App Builder — $(date)${'$'}{RESET}\n\n"
""",
)

private fun networkScanScript() = AppTemplate(
    id          = "tpl_netscan_sh",
    name        = "Network Scanner",
    description = "Scan the local subnet for live hosts using ping sweep",
    category    = TemplateCategory.DEVELOPER,
    type        = AppType.SHELL,
    tags        = listOf("network", "scan", "ping", "shell"),
    sourceCode  = """#!/system/bin/sh
# JARVIS Network Scanner — generated by App Builder
# Scans the local /24 subnet with ping. Requires network access.

GOLD='\033[38;5;178m'; DIM='\033[2m'; GREEN='\033[32m'; RESET='\033[0m'; BOLD='\033[1m'

# Detect local IP and subnet
LOCAL_IP=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[\d.]+' | head -1)
if [ -z "${'$'}LOCAL_IP" ]; then
  LOCAL_IP=$(ip addr show wlan0 2>/dev/null | grep "inet " | awk '{print ${'$'}2}' | cut -d/ -f1)
fi

if [ -z "${'$'}LOCAL_IP" ]; then
  echo "Error: could not determine local IP. Are you on a network?" >&2
  exit 1
fi

SUBNET=$(echo ${'$'}LOCAL_IP | cut -d. -f1-3)
echo ""
printf "${'$'}{GOLD}${'$'}{BOLD}JARVIS Network Scanner${'$'}{RESET}\n"
printf "${'$'}{DIM}Scanning %s.0/24 …${'$'}{RESET}\n\n" "${'$'}SUBNET"

FOUND=0
START=$(date +%s)

for i in $(seq 1 254); do
  IP="${'$'}SUBNET.${'$'}i"
  # Send 1 ping with 200ms timeout
  if ping -c 1 -W 1 "${'$'}IP" >/dev/null 2>&1; then
    HOSTNAME=$(nslookup ${'$'}IP 2>/dev/null | grep "name = " | awk '{print ${'$'}NF}' | sed 's/\.$//')
    [ -z "${'$'}HOSTNAME" ] && HOSTNAME="-"
    printf "${'$'}{GREEN}  %-16s  %s${'$'}{RESET}\n" "${'$'}IP" "${'$'}HOSTNAME"
    FOUND=$((FOUND+1))
  fi
done

END=$(date +%s)
ELAPSED=$((END-START))
echo ""
printf "${'$'}{DIM}Scan complete in %ds — %d hosts found${'$'}{RESET}\n\n" "${'$'}ELAPSED" "${'$'}FOUND"
""",
)
