"""Options wheel dashboard (Stage 4) — a self-contained page for the put-seller
bot. Renders account, wheel status per underlying, open positions, the bot's
last decisions, and a daily realized-P&L calendar. Reads the /api/options/* and
/api/alpaca/* endpoints client-side."""


def render_options_dashboard():
    return r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Options Wheel — Strategy Factory</title>
<style>
:root{--bg:#0d1117;--card:#161b22;--card2:#1c2230;--border:#2a3140;--text:#e6edf3;--muted:#8b949e;--green:#3fb950;--red:#f85149;--blue:#58a6ff;--amber:#d29922}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,Segoe UI,Roboto,sans-serif;font-size:14px}
.wrap{max-width:1100px;margin:0 auto;padding:20px}
.top{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:16px}
h1{font-size:20px;font-weight:600;margin:0}
.sub{color:var(--muted);font-size:12px}
.badge{padding:4px 10px;border-radius:20px;font-size:12px;font-weight:600}
.badge.dry{background:rgba(210,153,34,.15);color:var(--amber);border:1px solid rgba(210,153,34,.4)}
.badge.live{background:rgba(63,185,80,.15);color:var(--green);border:1px solid rgba(63,185,80,.4)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:18px}
.stat{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
.stat .l{color:var(--muted);font-size:12px;margin-bottom:4px}
.stat .v{font-size:20px;font-weight:600;font-variant-numeric:tabular-nums}
.section{margin-bottom:22px}
.section h2{font-size:15px;font-weight:600;margin:0 0 10px;color:var(--text)}
.wheelgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.wcard{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
.wcard .sym{font-weight:600;font-size:16px}
.wcard .state{display:inline-block;font-size:11px;padding:2px 8px;border-radius:12px;margin-left:8px}
.st-flat{background:rgba(139,148,158,.15);color:var(--muted)}
.st-put{background:rgba(88,166,255,.15);color:var(--blue)}
.st-shares{background:rgba(210,153,34,.15);color:var(--amber)}
.st-call{background:rgba(63,185,80,.15);color:var(--green)}
.wcard .dec{color:var(--muted);font-size:12px;margin-top:8px;line-height:1.5}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:9px 12px;font-size:13px;border-bottom:1px solid var(--border)}
th{color:var(--muted);font-weight:500;font-size:12px}
tr:last-child td{border-bottom:none}
.pos{color:var(--green)}.neg{color:var(--red)}.mut{color:var(--muted)}
.empty{color:var(--muted);padding:14px;text-align:center;font-size:13px}
.cal{display:grid;grid-template-columns:repeat(7,1fr);gap:6px}
.cal .h{color:var(--muted);font-size:11px;text-align:center;padding:4px 0}
.cell{background:var(--card);border:1px solid var(--border);border-radius:8px;min-height:58px;padding:6px 4px;text-align:center}
.cell.we{background:transparent;border-style:dashed;opacity:.4}
.cell .d{color:var(--muted);font-size:11px}
.cell .p{font-size:13px;font-weight:600;font-variant-numeric:tabular-nums;margin-top:4px}
.cell.g{background:rgba(63,185,80,.08);border-color:rgba(63,185,80,.25)}
.cell.r{background:rgba(248,81,73,.08);border-color:rgba(248,81,73,.25)}
.cal-nav{display:flex;align-items:center;gap:12px;margin-bottom:8px}
.cal-nav button{background:var(--card2);border:1px solid var(--border);color:var(--text);border-radius:6px;padding:3px 10px;cursor:pointer}
.err{color:var(--red);font-size:12px;margin-top:6px}
.refresh{background:var(--card2);border:1px solid var(--border);color:var(--text);border-radius:6px;padding:5px 12px;cursor:pointer;font-size:12px}
</style></head>
<body><div class="wrap">
<div class="top">
  <div><h1>Options Wheel <span id="mode" class="badge dry">DRY-RUN</span></h1>
  <div class="sub" id="updated">loading…</div></div>
  <button class="refresh" onclick="loadAll()">Refresh</button>
</div>
<div class="stats" id="stats"></div>
<div class="section"><h2>Wheel status</h2><div class="wheelgrid" id="wheel"></div></div>
<div class="section"><h2>Open positions</h2><div id="positions"></div></div>
<div class="section"><h2>Last cycle decisions</h2><div id="decisions"></div></div>
<div class="section"><h2>Daily realized P&amp;L</h2>
  <div class="cal-nav"><button onclick="calPrev()">&#9664;</button><b id="calLabel"></b><button onclick="calNext()">&#9654;</button></div>
  <div class="cal" id="cal"></div>
</div>
</div>
<script>
var UNDER=["SOFI","PFE","T","F"];
var _calData={},_calY=new Date().getFullYear(),_calM=new Date().getMonth();
function money(n){n=Number(n||0);var s=n<0?'-':'+';return s+'$'+Math.abs(n).toFixed(2);}
async function j(u){try{var r=await fetch(u,{credentials:'include'});return await r.json();}catch(e){return {error:String(e)};}}
function occParse(s){var m=/^([A-Z]+)(\d{6})([CP])(\d{8})$/.exec(s||'');if(!m)return null;return{root:m[1],exp:'20'+m[2].slice(0,2)+'-'+m[2].slice(2,4)+'-'+m[2].slice(4,6),type:m[3]=='P'?'put':'call',strike:parseInt(m[4])/1000};}

async function loadAll(){
  var acct=await j('/api/alpaca/account');
  var desk=await j('/api/options/desk-state');
  var posR=await j('/api/alpaca/positions');
  var positions=Array.isArray(posR)?posR:(posR.positions||[]);
  var realized=await j('/api/options/realized-by-day');
  renderMode(desk);
  renderStats(acct,realized);
  renderWheel(desk,positions);
  renderPositions(positions);
  renderDecisions(desk);
  _calData=(realized&&realized.days)||{};calRender();
  document.getElementById('updated').textContent='Updated '+new Date().toLocaleTimeString()+(desk.timestamp?(' · last cycle '+String(desk.timestamp).slice(0,16).replace('T',' ')):'');
}
function renderMode(desk){var m=document.getElementById('mode');var live=desk&&desk.dry_run===false;m.textContent=live?'LIVE':'DRY-RUN';m.className='badge '+(live?'live':'dry');}
function renderStats(a,realized){
  var tot=(realized&&realized.totals&&realized.totals.realized_net)||0;
  var s=[['Equity','$'+Number(a.equity||0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})],
    ['Buying power','$'+Number(a.buying_power||0).toLocaleString(undefined,{maximumFractionDigits:0})],
    ['Cash','$'+Number(a.cash||0).toLocaleString(undefined,{maximumFractionDigits:0})],
    ['Realized P&L (options)',money(tot),tot]];
  document.getElementById('stats').innerHTML=s.map(function(x){var c=x.length>2?(x[2]>=0?'pos':'neg'):'';return '<div class="stat"><div class="l">'+x[0]+'</div><div class="v '+c+'">'+x[1]+'</div></div>';}).join('');
}
function stateFor(u,positions){
  var sp=0,sh=0,sc=0;
  positions.forEach(function(p){var o=occParse(p.symbol);var q=Number(p.qty||0);
    if(o&&o.root==u){if(o.type=='put'&&q<0)sp++;if(o.type=='call'&&q<0)sc++;}
    else if((p.symbol||'').toUpperCase()==u)sh+=q;});
  if(sp>0)return['short put','st-put'];if(sc>0)return['covered call','st-call'];if(sh>=100)return['holding shares','st-shares'];return['flat','st-flat'];
}
function renderWheel(desk,positions){
  var acts=(desk&&desk.actions)||[];
  document.getElementById('wheel').innerHTML=UNDER.map(function(u){
    var st=stateFor(u,positions);
    var a=acts.filter(function(x){return x.symbol==u;})[0]||{};
    var dec=a.action?('<b>'+a.action.replace('_',' ')+'</b> — '+(a.reason||'')):'—';
    if(a.action=='sell_put'||a.action=='sell_call')dec='<b>'+a.action.replace('_',' ')+'</b> $'+a.strike+' @ '+a.limit_price+' ('+(a.expiration||'')+', &Delta;'+(a.delta||'')+')';
    return '<div class="wcard"><span class="sym">'+u+'</span><span class="state '+st[1]+'">'+st[0]+'</span><div class="dec">'+dec+'</div></div>';
  }).join('');
}
function renderPositions(positions){
  var opt=positions.filter(function(p){return occParse(p.symbol);});
  var el=document.getElementById('positions');
  if(!opt.length){el.innerHTML='<div class="empty">No open option positions.</div>';return;}
  var rows=opt.map(function(p){var o=occParse(p.symbol);var q=Number(p.qty||0);var pl=Number(p.unrealized_pl||0);
    var dte=Math.round((new Date(o.exp)-new Date())/86400000);
    return '<tr><td>'+o.root+'</td><td>'+o.type+'</td><td>$'+o.strike+'</td><td>'+o.exp+' ('+dte+'d)</td><td>'+q+'</td><td>'+(Number(p.avg_entry_price||0)).toFixed(2)+'</td><td>'+(Number(p.current_price||0)).toFixed(2)+'</td><td class="'+(pl>=0?'pos':'neg')+'">'+money(pl)+'</td></tr>';}).join('');
  el.innerHTML='<table><tr><th>Under</th><th>Type</th><th>Strike</th><th>Expiry</th><th>Qty</th><th>Entry</th><th>Now</th><th>P/L</th></tr>'+rows+'</table>';
}
function renderDecisions(desk){
  var acts=(desk&&desk.actions)||[];var el=document.getElementById('decisions');
  if(desk&&desk.errors&&desk.errors.length){el.innerHTML='<div class="err">Errors: '+desk.errors.join('; ')+'</div>';}
  if(!acts.length){el.innerHTML+='<div class="empty">No decisions yet — run a cycle.</div>';return;}
  var rows=acts.map(function(a){var t=a.action||'';var det=a.strike?('$'+a.strike+' @ '+a.limit_price+' '+(a.expiration||'')):'';
    return '<tr><td>'+(a.symbol||'')+'</td><td>'+t.replace('_',' ')+'</td><td>'+det+'</td><td class="mut">'+(a.reason||'')+'</td></tr>';}).join('');
  el.innerHTML='<table><tr><th>Under</th><th>Decision</th><th>Detail</th><th>Reason</th></tr>'+rows+'</table>';
}
function calPrev(){_calM--;if(_calM<0){_calM=11;_calY--;}calRender();}
function calNext(){_calM++;if(_calM>11){_calM=0;_calY++;}calRender();}
function calRender(){
  var mn=['January','February','March','April','May','June','July','August','September','October','November','December'];
  document.getElementById('calLabel').textContent=mn[_calM]+' '+_calY;
  var el=document.getElementById('cal');var h=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  var html=h.map(function(x){return '<div class="h">'+x+'</div>';}).join('');
  var first=new Date(_calY,_calM,1).getDay();var dim=new Date(_calY,_calM+1,0).getDate();
  for(var e=0;e<first;e++)html+='<div class="cell we"></div>';
  for(var d=1;d<=dim;d++){
    var ds=_calY+'-'+String(_calM+1).padStart(2,'0')+'-'+String(d).padStart(2,'0');
    var dow=new Date(_calY,_calM,d).getDay();var we=(dow===0||dow===6);
    var rec=(!we)?_calData[ds]:null;var cls='cell'+(we?' we':'');var inner='<div class="d">'+d+'</div>';
    if(rec){var n=Number(rec.realized_net||0);cls+=n>0?' g':(n<0?' r':'');inner+='<div class="p '+(n>=0?'pos':'neg')+'">'+money(n)+'</div>';}
    html+='<div class="'+cls+'">'+inner+'</div>';
  }
  el.innerHTML=html;
}
loadAll();
setInterval(loadAll,60000);
</script></body></html>"""
