"""Options wheel dashboard (Stage 4) — a professional single-page terminal for
the cash-secured put seller. Live price ticker (1s), account KPIs, per-name
wheel status, open positions, the bot's decisions, and a daily realized-P&L
calendar with monthly KPIs. Reads /api/options/* and /api/alpaca/* client-side."""


def render_options_dashboard():
    return r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Options Wheel — Strategy Factory</title>
<style>
:root{--bg:#0b0e14;--card:#151a23;--card2:#1b2230;--border:#232b39;--text:#e6edf3;--muted:#8b94a3;--faint:#5b6472;--green:#3fb950;--red:#f85149;--blue:#58a6ff;--amber:#e3b341}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,sans-serif;font-size:14px;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:20px 22px 60px}
.tabnum{font-variant-numeric:tabular-nums}
.pos{color:var(--green)}.neg{color:var(--red)}.mut{color:var(--muted)}
.top{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:18px}
h1{font-size:19px;font-weight:600;margin:0;letter-spacing:.2px}
.sub{color:var(--muted);font-size:12px;margin-top:3px;display:flex;align-items:center;gap:7px}
.dot{width:7px;height:7px;border-radius:50%;background:var(--green);display:inline-block;box-shadow:0 0 0 0 rgba(63,185,80,.5);animation:pulse 1.6s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(63,185,80,.45)}70%{box-shadow:0 0 0 6px rgba(63,185,80,0)}100%{box-shadow:0 0 0 0 rgba(63,185,80,0)}}
.badge{padding:4px 11px;border-radius:20px;font-size:11px;font-weight:700;letter-spacing:.4px}
.badge.dry{background:rgba(227,179,65,.14);color:var(--amber);border:1px solid rgba(227,179,65,.4)}
.badge.live{background:rgba(63,185,80,.14);color:var(--green);border:1px solid rgba(63,185,80,.4)}
.ticker{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:18px}
.tk{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:11px 14px;transition:background .35s}
.tk.up{background:rgba(63,185,80,.12)}.tk.down{background:rgba(248,81,73,.12)}
.tk .s{font-size:12px;color:var(--muted);font-weight:600;letter-spacing:.4px}
.tk .p{font-size:22px;font-weight:600;margin-top:2px}
.tk .c{font-size:12px;margin-top:1px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:24px}
.stat{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px 16px}
.stat .l{color:var(--muted);font-size:12px;margin-bottom:6px}
.stat .v{font-size:23px;font-weight:600}
.section{margin-bottom:26px}
.section h2{font-size:13px;font-weight:600;margin:0 0 12px;color:var(--muted);text-transform:uppercase;letter-spacing:.7px}
.wheelgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
.wcard{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px 16px}
.wcard .hd{display:flex;align-items:center;justify-content:space-between}
.wcard .sym{font-weight:600;font-size:17px}
.wcard .px{font-size:15px;font-weight:600;font-variant-numeric:tabular-nums}
.state{display:inline-block;font-size:10.5px;font-weight:700;letter-spacing:.4px;padding:3px 9px;border-radius:12px;text-transform:uppercase}
.st-flat{background:rgba(139,148,163,.14);color:var(--muted)}
.st-put{background:rgba(88,166,255,.15);color:var(--blue)}
.st-shares{background:rgba(227,179,65,.15);color:var(--amber)}
.st-call{background:rgba(63,185,80,.15);color:var(--green)}
.wcard .dec{color:var(--muted);font-size:12px;margin-top:10px;line-height:1.55}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden}
th,td{text-align:left;padding:10px 13px;font-size:13px;border-bottom:1px solid var(--border)}
th{color:var(--muted);font-weight:500;font-size:11.5px;text-transform:uppercase;letter-spacing:.4px}
tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.empty{color:var(--muted);padding:16px;text-align:center;font-size:13px;background:var(--card);border:1px solid var(--border);border-radius:12px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;margin-bottom:14px}
.kpi{background:var(--card2);border-radius:10px;padding:10px 12px}
.kpi .l{color:var(--faint);font-size:11px;text-transform:uppercase;letter-spacing:.4px}
.kpi .v{font-size:17px;font-weight:600;margin-top:3px;font-variant-numeric:tabular-nums}
.cal{display:grid;grid-template-columns:repeat(7,1fr);gap:6px}
.cal .h{color:var(--faint);font-size:11px;text-align:center;padding:3px 0}
.cell{background:var(--card);border:1px solid var(--border);border-radius:8px;min-height:60px;padding:6px 5px;text-align:center}
.cell.we{background:transparent;border-style:dashed;opacity:.35}
.cell.today{border-color:var(--blue)}
.cell .d{color:var(--faint);font-size:11px}
.cell .p{font-size:13px;font-weight:600;margin-top:5px;font-variant-numeric:tabular-nums}
.cell .t{font-size:10px;color:var(--faint);margin-top:2px}
.cell.g{background:rgba(63,185,80,.09);border-color:rgba(63,185,80,.3)}
.cell.r{background:rgba(248,81,73,.09);border-color:rgba(248,81,73,.3)}
.calnav{display:flex;align-items:center;gap:12px;margin-bottom:10px}
.calnav button,.rf{background:var(--card2);border:1px solid var(--border);color:var(--text);border-radius:7px;padding:5px 11px;cursor:pointer;font-size:12px}
.calnav button:hover,.rf:hover{border-color:var(--faint)}
.err{color:var(--red);font-size:12px;margin-bottom:8px}
</style></head>
<body><div class="wrap">
<div class="top">
  <div><h1>Options Wheel <span id="mode" class="badge dry">DRY-RUN</span></h1>
  <div class="sub"><span class="dot"></span><span id="updated">connecting…</span></div></div>
  <button class="rf" onclick="loadAll();loadPrices()">Refresh now</button>
</div>
<div class="ticker" id="ticker"></div>
<div class="stats" id="stats"></div>
<div class="section"><h2>Wheel status</h2><div class="wheelgrid" id="wheel"></div></div>
<div class="section"><h2>Open positions</h2><div id="positions"></div></div>
<div class="section"><h2>Bot decisions — last cycle</h2><div id="decisions"></div></div>
<div class="section"><h2>Daily realized P&amp;L</h2>
  <div class="kpis" id="kpis"></div>
  <div class="calnav"><button onclick="calPrev()">&#9664;</button><b id="calLabel"></b><button onclick="calNext()">&#9654;</button></div>
  <div class="cal" id="cal"></div>
</div>
</div>
<script>
var UNDER=["SOFI","PFE","T","F"];
var _prices={},_px={},_calData={},_positions=[],_desk={};
var _calY=new Date().getFullYear(),_calM=new Date().getMonth();
function money(n){n=Number(n||0);return (n<0?'-':'+')+'$'+Math.abs(n).toFixed(2);}
function money0(n){return '$'+Number(n||0).toLocaleString(undefined,{maximumFractionDigits:0});}
async function j(u){try{var r=await fetch(u,{credentials:'include'});return await r.json();}catch(e){return {error:String(e)};}}
function occ(s){var m=/^([A-Z]+)(\d{6})([CP])(\d{8})$/.exec(s||'');if(!m)return null;return{root:m[1],exp:'20'+m[2].slice(0,2)+'-'+m[2].slice(2,4)+'-'+m[2].slice(4,6),type:m[3]=='P'?'put':'call',strike:parseInt(m[4])/1000};}

async function loadPrices(){
  var q=await j('/api/options/quotes');var qs=(q&&q.quotes)||{};_prices=qs;
  document.getElementById('ticker').innerHTML=UNDER.map(function(u){
    var d=qs[u]||{};var p=d.price;var c=d.change_pct;
    var cls='tk';if(p!=null&&_px[u]!=null){if(p>_px[u])cls='tk up';else if(p<_px[u])cls='tk down';}
    if(p!=null)_px[u]=p;
    var ch=(c!=null)?('<span class="'+(c>=0?'pos':'neg')+'">'+(c>=0?'+':'')+c.toFixed(2)+'%</span>'):'<span class="mut">—</span>';
    return '<div class="'+cls+'" id="tk-'+u+'"><div class="s">'+u+'</div><div class="p tabnum">'+(p!=null?('$'+p.toFixed(2)):'—')+'</div><div class="c">'+ch+'</div></div>';
  }).join('');
  renderWheel();
}
async function loadAll(){
  var acct=await j('/api/alpaca/account');
  _desk=await j('/api/options/desk-state');
  var posR=await j('/api/alpaca/positions');_positions=Array.isArray(posR)?posR:(posR.positions||[]);
  var realized=await j('/api/options/realized-by-day');_calData=(realized&&realized.days)||{};
  renderMode();renderStats(acct,realized);renderWheel();renderPositions();renderDecisions();calRender();
  var lc=_desk.timestamp?(' · bot cycle '+String(_desk.timestamp).slice(0,16).replace('T',' ')):'';
  document.getElementById('updated').textContent='Live · updated '+new Date().toLocaleTimeString()+lc;
}
function renderMode(){var m=document.getElementById('mode');var live=_desk&&_desk.dry_run===false;m.textContent=live?'LIVE':'DRY-RUN';m.className='badge '+(live?'live':'dry');}
function renderStats(a,realized){
  var tot=(realized&&realized.totals&&realized.totals.realized_net)||0;
  var cells=[['Equity','$'+Number(a.equity||0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}),null],
    ['Buying power',money0(a.buying_power),null],['Cash',money0(a.cash),null],
    ['Realized P&L (options)',money(tot),tot]];
  document.getElementById('stats').innerHTML=cells.map(function(x){var c=x[2]!=null?(x[2]>=0?'pos':'neg'):'';return '<div class="stat"><div class="l">'+x[0]+'</div><div class="v tabnum '+c+'">'+x[1]+'</div></div>';}).join('');
}
function stateFor(u){var sp=0,sh=0,sc=0;_positions.forEach(function(p){var o=occ(p.symbol);var q=Number(p.qty||0);if(o&&o.root==u){if(o.type=='put'&&q<0)sp++;if(o.type=='call'&&q<0)sc++;}else if((p.symbol||'').toUpperCase()==u)sh+=q;});
  if(sp>0)return['short put','st-put'];if(sc>0)return['covered call','st-call'];if(sh>=100)return['holding shares','st-shares'];return['flat','st-flat'];}
function renderWheel(){
  var acts=(_desk&&_desk.actions)||[];
  var el=document.getElementById('wheel');if(!el)return;
  el.innerHTML=UNDER.map(function(u){
    var st=stateFor(u);var a=acts.filter(function(x){return x.symbol==u;})[0]||{};
    var pd=_prices[u]||{};var px=pd.price!=null?('$'+pd.price.toFixed(2)):'—';
    var dec=a.action?('<b>'+String(a.action).replace('_',' ')+'</b> — '+(a.reason||'')):'—';
    if(a.action=='sell_put'||a.action=='sell_call')dec='<b>'+String(a.action).replace('_',' ')+'</b> $'+a.strike+' @ '+a.limit_price+' &middot; '+(a.expiration||'')+' &middot; &Delta;'+(a.delta||'');
    return '<div class="wcard"><div class="hd"><span class="sym">'+u+' <span class="state '+st[1]+'">'+st[0]+'</span></span><span class="px">'+px+'</span></div><div class="dec">'+dec+'</div></div>';
  }).join('');
}
function renderPositions(){
  var opt=_positions.filter(function(p){return occ(p.symbol);});var el=document.getElementById('positions');
  if(!opt.length){el.innerHTML='<div class="empty">No open option positions.</div>';return;}
  var rows=opt.map(function(p){var o=occ(p.symbol);var q=Number(p.qty||0);var pl=Number(p.unrealized_pl||0);
    var dte=Math.round((new Date(o.exp)-new Date())/86400000);
    return '<tr><td>'+o.root+'</td><td>'+o.type+'</td><td class="num">$'+o.strike+'</td><td>'+o.exp+' ('+dte+'d)</td><td class="num">'+q+'</td><td class="num">'+Number(p.avg_entry_price||0).toFixed(2)+'</td><td class="num">'+Number(p.current_price||0).toFixed(2)+'</td><td class="num '+(pl>=0?'pos':'neg')+'">'+money(pl)+'</td></tr>';}).join('');
  el.innerHTML='<table><tr><th>Under</th><th>Type</th><th class="num">Strike</th><th>Expiry</th><th class="num">Qty</th><th class="num">Entry</th><th class="num">Now</th><th class="num">P/L</th></tr>'+rows+'</table>';
}
function renderDecisions(){
  var acts=(_desk&&_desk.actions)||[];var el=document.getElementById('decisions');var h='';
  if(_desk&&_desk.errors&&_desk.errors.length)h+='<div class="err">'+_desk.errors.join('; ')+'</div>';
  if(!acts.length){el.innerHTML=h+'<div class="empty">No decisions yet.</div>';return;}
  var rows=acts.map(function(a){var det=a.strike?('$'+a.strike+' @ '+a.limit_price+' &middot; '+(a.expiration||'')):'';
    return '<tr><td>'+(a.symbol||'')+'</td><td>'+String(a.action||'').replace('_',' ')+'</td><td>'+det+'</td><td class="mut">'+(a.reason||'')+'</td></tr>';}).join('');
  el.innerHTML=h+'<table><tr><th>Under</th><th>Decision</th><th>Detail</th><th>Reason</th></tr>'+rows+'</table>';
}
function calPrev(){_calM--;if(_calM<0){_calM=11;_calY--;}calRender();}
function calNext(){_calM++;if(_calM>11){_calM=0;_calY++;}calRender();}
function calRender(){
  var mn=['January','February','March','April','May','June','July','August','September','October','November','December'];
  document.getElementById('calLabel').textContent=mn[_calM]+' '+_calY;
  var pre=_calY+'-'+String(_calM+1).padStart(2,'0')+'-';
  var mNet=0,closes=0,green=0,red=0,best=null,worst=null,tracked=0;
  Object.keys(_calData).forEach(function(k){if(k.indexOf(pre)!==0)return;var n=Number(_calData[k].realized_net||0);mNet+=n;closes+=Number(_calData[k].closed_trades||0);tracked++;if(n>0)green++;else if(n<0)red++;if(best===null||n>best)best=n;if(worst===null||n<worst)worst=n;});
  var wr=(green+red)>0?Math.round(green/(green+red)*100):null;
  var avg=tracked>0?mNet/tracked:0;
  var K=[['Month P&L',money(mNet),mNet],['Closes',String(closes),null],['Win days',wr!=null?(wr+'%'):'—',null],['Green / Red',green+' / '+red,null],['Best day',best!=null?money(best):'—',best],['Worst day',worst!=null?money(worst):'—',worst],['Avg / day',tracked?money(avg):'—',avg]];
  document.getElementById('kpis').innerHTML=K.map(function(x){var c=x[2]!=null&&(x[0]=='Month P&L'||x[0]=='Best day'||x[0]=='Worst day'||x[0]=='Avg / day')?(x[2]>=0?'pos':'neg'):'';return '<div class="kpi"><div class="l">'+x[0]+'</div><div class="v '+c+'">'+x[1]+'</div></div>';}).join('');
  var el=document.getElementById('cal');var hd=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  var html=hd.map(function(x){return '<div class="h">'+x+'</div>';}).join('');
  var first=new Date(_calY,_calM,1).getDay();var dim=new Date(_calY,_calM+1,0).getDate();
  var t=new Date();var ts=t.getFullYear()+'-'+String(t.getMonth()+1).padStart(2,'0')+'-'+String(t.getDate()).padStart(2,'0');
  for(var e=0;e<first;e++)html+='<div class="cell we"></div>';
  for(var d=1;d<=dim;d++){var ds=pre+String(d).padStart(2,'0');var dow=new Date(_calY,_calM,d).getDay();var we=(dow===0||dow===6);
    var rec=(!we)?_calData[ds]:null;var cls='cell'+(we?' we':'')+(ds===ts?' today':'');var inner='<div class="d">'+d+'</div>';
    if(rec){var n=Number(rec.realized_net||0);cls+=n>0?' g':(n<0?' r':'');inner+='<div class="p '+(n>=0?'pos':'neg')+'">'+money(n)+'</div>';var tr=Number(rec.closed_trades||0);if(tr)inner+='<div class="t">'+tr+(tr==1?' close':' closes')+'</div>';}
    html+='<div class="'+cls+'">'+inner+'</div>';}
  el.innerHTML=html;
}
loadAll();loadPrices();
setInterval(loadPrices,1000);
setInterval(loadAll,20000);
</script></body></html>"""
