"""Options wheel dashboard (Cockpit style) — a hero P&L number + equity curve up
top, a KPI strip, then live prices, positions (with Greeks/risk), wheel status,
working orders, bot decisions, and a daily realized-P&L calendar. Reads the
/api/options/* and /api/alpaca/* endpoints client-side; prices poll every 1s."""


def render_options_dashboard():
    return r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Options Wheel — Strategy Factory</title>
<style>
:root{--bg:#0b0e14;--card:#151a23;--card2:#1b2230;--border:#232b39;--text:#e6edf3;--muted:#8b94a3;--faint:#5b6472;--green:#3fb950;--red:#f85149;--blue:#58a6ff;--amber:#e3b341}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,sans-serif;font-size:14px;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:20px 22px 60px}
.tabnum{font-variant-numeric:tabular-nums}.pos{color:var(--green)}.neg{color:var(--red)}.mut{color:var(--muted)}
.top{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:16px}
h1{font-size:19px;font-weight:600;margin:0;letter-spacing:.2px}
.sub{color:var(--muted);font-size:12px;margin-top:3px;display:flex;align-items:center;gap:7px}
.dot{width:7px;height:7px;border-radius:50%;background:var(--green);display:inline-block;animation:pulse 1.6s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(63,185,80,.45)}70%{box-shadow:0 0 0 6px rgba(63,185,80,0)}100%{box-shadow:0 0 0 0 rgba(63,185,80,0)}}
.badge{padding:4px 11px;border-radius:20px;font-size:11px;font-weight:700;letter-spacing:.4px}
.badge.dry{background:rgba(227,179,65,.14);color:var(--amber);border:1px solid rgba(227,179,65,.4)}
.badge.live{background:rgba(63,185,80,.14);color:var(--green);border:1px solid rgba(63,185,80,.4)}
.rf{background:var(--card2);border:1px solid var(--border);color:var(--text);border-radius:7px;padding:6px 12px;cursor:pointer;font-size:12px}.rf:hover{border-color:var(--faint)}
.hero{display:grid;grid-template-columns:minmax(0,320px) 1fr;gap:18px;background:var(--card);border:1px solid var(--border);border-radius:14px;padding:20px 22px;margin-bottom:16px}
.hero-l{display:flex;flex-direction:column;justify-content:center}
.hero-lbl{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.7px}
.hero-num{font-size:42px;font-weight:600;font-variant-numeric:tabular-nums;line-height:1.05;margin-top:6px}
.hero-sub{font-size:13px;margin-top:8px;color:var(--muted)}
.hero-r{min-height:120px;display:flex;align-items:stretch}
@media(max-width:640px){.hero{grid-template-columns:1fr}}
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:22px}
.kpi{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:11px 13px}
.kpi .l{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}
.kpi .v{font-size:19px;font-weight:600;margin-top:4px;font-variant-numeric:tabular-nums}
.section{margin-bottom:24px}
.section h2{font-size:12px;font-weight:600;margin:0 0 11px;color:var(--muted);text-transform:uppercase;letter-spacing:.7px}
.ticker{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.tk{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:10px 13px;transition:background .35s}
.tk.up{background:rgba(63,185,80,.12)}.tk.down{background:rgba(248,81,73,.12)}
.tk .s{font-size:12px;color:var(--muted);font-weight:600;letter-spacing:.4px}.tk .p{font-size:20px;font-weight:600;margin-top:2px}.tk .c{font-size:12px;margin-top:1px}
.wheelgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
.wcard{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px 16px}
.wcard .hd{display:flex;align-items:center;justify-content:space-between}.wcard .sym{font-weight:600;font-size:17px}.wcard .px{font-size:15px;font-weight:600;font-variant-numeric:tabular-nums}
.state{display:inline-block;font-size:10.5px;font-weight:700;letter-spacing:.4px;padding:3px 9px;border-radius:12px;text-transform:uppercase}
.st-flat{background:rgba(139,148,163,.14);color:var(--muted)}.st-put{background:rgba(88,166,255,.15);color:var(--blue)}.st-shares{background:rgba(227,179,65,.15);color:var(--amber)}.st-call{background:rgba(63,185,80,.15);color:var(--green)}
.wcard .dec{color:var(--muted);font-size:12px;margin-top:10px;line-height:1.55}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden}
th,td{text-align:left;padding:10px 13px;font-size:13px;border-bottom:1px solid var(--border)}
th{color:var(--muted);font-weight:500;font-size:11.5px;text-transform:uppercase;letter-spacing:.4px}
tr:last-child td{border-bottom:none}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.empty{color:var(--muted);padding:16px;text-align:center;font-size:13px;background:var(--card);border:1px solid var(--border);border-radius:12px}
.calkpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(105px,1fr));gap:10px;margin-bottom:14px}
.ck{background:var(--card2);border-radius:10px;padding:10px 12px}.ck .l{color:var(--faint);font-size:11px;text-transform:uppercase;letter-spacing:.4px}.ck .v{font-size:16px;font-weight:600;margin-top:3px;font-variant-numeric:tabular-nums}
.cal{display:grid;grid-template-columns:repeat(7,1fr);gap:6px}.cal .h{color:var(--faint);font-size:11px;text-align:center;padding:3px 0}
.cell{background:var(--card);border:1px solid var(--border);border-radius:8px;min-height:58px;padding:6px 5px;text-align:center}
.cell.we{background:transparent;border-style:dashed;opacity:.35}.cell.today{border-color:var(--blue)}
.cell .d{color:var(--faint);font-size:11px}.cell .p{font-size:13px;font-weight:600;margin-top:5px;font-variant-numeric:tabular-nums}.cell .t{font-size:10px;color:var(--faint);margin-top:2px}
.cell.g{background:rgba(63,185,80,.09);border-color:rgba(63,185,80,.3)}.cell.r{background:rgba(248,81,73,.09);border-color:rgba(248,81,73,.3)}
.calnav{display:flex;align-items:center;gap:12px;margin-bottom:10px}.calnav button{background:var(--card2);border:1px solid var(--border);color:var(--text);border-radius:7px;padding:5px 11px;cursor:pointer;font-size:12px}
.err{color:var(--red);font-size:12px;margin-bottom:8px}
</style></head>
<body><div class="wrap">
<div class="top">
  <div><h1>Options Wheel <span id="mode" class="badge dry">DRY-RUN</span></h1>
  <div class="sub"><span class="dot"></span><span id="updated">connecting…</span></div></div>
  <button class="rf" onclick="loadAll();loadPrices()">Refresh now</button>
</div>
<div class="hero">
  <div class="hero-l"><div class="hero-lbl">Total P&amp;L</div><div class="hero-num" id="heroPnl">—</div><div class="hero-sub" id="heroSub"></div></div>
  <div class="hero-r" id="curve"></div>
</div>
<div class="strip" id="strip"></div>
<div class="section"><h2>Live prices</h2><div class="ticker" id="ticker"></div></div>
<div class="section"><h2>Open positions</h2><div id="positions"></div></div>
<div class="section"><h2>Wheel status</h2><div class="wheelgrid" id="wheel"></div></div>
<div class="section"><h2>Working orders</h2><div id="orders"></div></div>
<div class="section"><h2>Bot decisions — last cycle</h2><div id="decisions"></div></div>
<div class="section"><h2>Daily realized P&amp;L</h2>
  <div class="calkpi" id="calkpi"></div>
  <div class="calnav"><button onclick="calPrev()">&#9664;</button><b id="calLabel"></b><button onclick="calNext()">&#9654;</button></div>
  <div class="cal" id="cal"></div>
</div>
</div>
<script>
var UNDER=["SOFI","PFE","T","F"];
var _prices={},_px={},_calData={},_realized={},_desk={},_detail={},_orders=[];
var _calY=new Date().getFullYear(),_calM=new Date().getMonth();
function money(n){n=Number(n||0);return (n<0?'-':'+')+'$'+Math.abs(n).toFixed(2);}
function money0(n){return '$'+Number(n||0).toLocaleString(undefined,{maximumFractionDigits:0});}
async function j(u){try{var r=await fetch(u,{credentials:'include'});return await r.json();}catch(e){return {error:String(e)};}}
function occ(s){var m=/^([A-Z]+)(\d{6})([CP])(\d{8})$/.exec(s||'');if(!m)return null;return{root:m[1],exp:'20'+m[2].slice(0,2)+'-'+m[2].slice(2,4)+'-'+m[2].slice(4,6),type:m[3]=='P'?'put':'call',strike:parseInt(m[4])/1000};}

async function loadPrices(){
  var q=await j('/api/options/quotes');var qs=(q&&q.quotes)||{};_prices=qs;
  document.getElementById('ticker').innerHTML=UNDER.map(function(u){
    var d=qs[u]||{};var p=d.price;var c=d.change_pct;var cls='tk';
    if(p!=null&&_px[u]!=null){if(p>_px[u])cls='tk up';else if(p<_px[u])cls='tk down';}
    if(p!=null)_px[u]=p;
    var ch=(c!=null)?('<span class="'+(c>=0?'pos':'neg')+'">'+(c>=0?'+':'')+c.toFixed(2)+'%</span>'):'<span class="mut">—</span>';
    return '<div class="'+cls+'"><div class="s">'+u+'</div><div class="p tabnum">'+(p!=null?('$'+p.toFixed(2)):'—')+'</div><div class="c">'+ch+'</div></div>';
  }).join('');
  renderWheel();
}
async function loadAll(){
  var acct=await j('/api/alpaca/account');
  _desk=await j('/api/options/desk-state');
  _detail=await j('/api/options/positions-detail');
  var ordR=await j('/api/alpaca/orders?status=open&limit=25');_orders=(ordR&&ordR.orders)||(Array.isArray(ordR)?ordR:[]);
  _realized=await j('/api/options/realized-by-day');_calData=(_realized&&_realized.days)||{};
  renderMode();renderHero(acct);renderStrip(acct);renderWheel();renderPositions();renderOrders();renderDecisions();calRender();
  var lc=_desk.timestamp?(' · bot cycle '+String(_desk.timestamp).slice(0,16).replace('T',' ')):'';
  document.getElementById('updated').textContent='Live · updated '+new Date().toLocaleTimeString()+lc;
}
function renderMode(){var m=document.getElementById('mode');var live=_desk&&_desk.dry_run===false;m.textContent=live?'LIVE':'DRY-RUN';m.className='badge '+(live?'live':'dry');}
function renderHero(a){
  var T=(_detail&&_detail.totals)||{};
  var realizedTot=(_realized&&_realized.totals&&_realized.totals.realized_net)||0;
  var unreal=T.unrealized_pl||0;var total=realizedTot+unreal;
  var h=document.getElementById('heroPnl');h.textContent=money(total);h.className='hero-num tabnum '+(total>=0?'pos':'neg');
  document.getElementById('heroSub').innerHTML='<span class="'+(realizedTot>=0?'pos':'neg')+'">'+money(realizedTot)+' realized</span> &nbsp;·&nbsp; <span class="'+(unreal>=0?'pos':'neg')+'">'+money(unreal)+' open</span>';
  var keys=Object.keys(_calData).sort();var cum=0,series=[];
  keys.forEach(function(k){cum+=Number(_calData[k].realized_net||0);series.push(cum);});
  drawCurve(series);
}
function drawCurve(series){
  var el=document.getElementById('curve');
  if(!series.length){el.innerHTML='<div style="margin:auto;color:var(--faint);font-size:12px">Equity curve builds as trades close</div>';return;}
  var W=100,H=100,n=series.length;var vals=series.concat([0]);
  var mn=Math.min.apply(null,vals),mx=Math.max.apply(null,vals);if(mx===mn)mx=mn+1;
  function x(i){return n<=1?W:(i/(n-1)*W);}function y(v){return H-((v-mn)/(mx-mn)*H);}
  var line=(n<=1)?('0,'+y(series[0])+' '+W+','+y(series[0])):series.map(function(v,i){return x(i)+','+y(v);}).join(' ');
  var area=line+' '+W+','+H+' 0,'+H;var last=series[n-1];var col=last>=0?'var(--green)':'var(--red)';
  el.innerHTML='<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none" style="width:100%;height:120px"><polygon points="'+area+'" fill="'+col+'" opacity="0.12"/><line x1="0" y1="'+y(0)+'" x2="'+W+'" y2="'+y(0)+'" stroke="var(--border)" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/><polyline points="'+line+'" fill="none" stroke="'+col+'" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>';
}
function renderStrip(a){
  var T=(_detail&&_detail.totals)||{};
  var days=_calData;var green=0,red=0;Object.keys(days).forEach(function(k){var v=Number(days[k].realized_net||0);if(v>0)green++;else if(v<0)red++;});
  var wr=(green+red)>0?Math.round(green/(green+red)*100)+'%':'—';
  var cells=[
    ['Equity','$'+Number(a.equity||0).toLocaleString(undefined,{maximumFractionDigits:0}),null],
    ['Unrealized',money(T.unrealized_pl||0),T.unrealized_pl||0],
    ['Daily theta',T.portfolio_theta!=null?money(T.portfolio_theta):'—',T.portfolio_theta],
    ['Capital used',(T.utilization_pct!=null?T.utilization_pct+'%':'—'),null],
    ['Open',(T.open_count!=null?String(T.open_count):'0'),null],
    ['Win days',wr,null],
    ['Buying power',money0(a.buying_power),null]];
  document.getElementById('strip').innerHTML=cells.map(function(x){var c=x[2]!=null?(x[2]>=0?'pos':'neg'):'';return '<div class="kpi"><div class="l">'+x[0]+'</div><div class="v '+c+'">'+x[1]+'</div></div>';}).join('');
}
function stateFor(u){var sp=0,sh=0,sc=0;((_detail&&_detail.positions)||[]).forEach(function(p){if(p.underlying==u){if(p.type=='put'&&p.qty<0)sp++;if(p.type=='call'&&p.qty<0)sc++;}});
  ((_detail&&_detail.positions)||[]).forEach(function(){});
  if(sp>0)return['short put','st-put'];if(sc>0)return['covered call','st-call'];if(sh>=100)return['holding shares','st-shares'];return['flat','st-flat'];}
function renderWheel(){
  var acts=(_desk&&_desk.actions)||[];var el=document.getElementById('wheel');if(!el)return;
  el.innerHTML=UNDER.map(function(u){
    var st=stateFor(u);var a=acts.filter(function(x){return x.symbol==u;})[0]||{};
    var pd=_prices[u]||{};var px=pd.price!=null?('$'+pd.price.toFixed(2)):'—';
    var dec=a.action?('<b>'+String(a.action).replace('_',' ')+'</b> — '+(a.reason||'')):'—';
    if(a.action=='sell_put'||a.action=='sell_call')dec='<b>'+String(a.action).replace('_',' ')+'</b> $'+a.strike+' @ '+a.limit_price+' &middot; '+(a.expiration||'')+' &middot; &Delta;'+(a.delta||'');
    return '<div class="wcard"><div class="hd"><span class="sym">'+u+' <span class="state '+st[1]+'">'+st[0]+'</span></span><span class="px">'+px+'</span></div><div class="dec">'+dec+'</div></div>';
  }).join('');
}
function renderPositions(){
  var opt=(_detail&&_detail.positions)||[];var el=document.getElementById('positions');
  if(!opt.length){el.innerHTML='<div class="empty">No open option positions.</div>';return;}
  var rows=opt.map(function(p){var pl=Number(p.unrealized_pl||0);var tgt=p.pct_to_target;var cu=p.cushion_pct;var thetaD=(p.theta!=null&&p.qty)?(p.theta*100*p.qty):null;
    return '<tr><td>'+p.underlying+' '+p.type+'</td><td class="num">$'+p.strike+'</td><td>'+(p.expiration||'')+' ('+(p.dte!=null?p.dte:'?')+'d)</td><td class="num">'+p.qty+'</td><td class="num">'+Number(p.entry||0).toFixed(2)+'</td><td class="num">'+Number(p.mark||0).toFixed(2)+'</td><td class="num '+(pl>=0?'pos':'neg')+'">'+money(pl)+'</td><td class="num '+((tgt!=null&&tgt>=50)?'pos':'')+'">'+(tgt!=null?(tgt+'%'):'—')+'</td><td class="num">'+(p.delta!=null?p.delta:'—')+'</td><td class="num '+(thetaD>=0?'pos':'neg')+'">'+(thetaD!=null?money(thetaD):'—')+'</td><td class="num">'+(p.iv_pct!=null?(p.iv_pct+'%'):'—')+'</td><td class="num">'+(p.breakeven!=null?('$'+p.breakeven):'—')+'</td><td class="num '+(cu!=null&&cu>=0?'pos':'neg')+'">'+(cu!=null?(cu+'%'):'—')+'</td></tr>';}).join('');
  el.innerHTML='<table><tr><th>Position</th><th class="num">Strike</th><th>Expiry</th><th class="num">Qty</th><th class="num">Credit</th><th class="num">Mark</th><th class="num">Unreal</th><th class="num">% Tgt</th><th class="num">&Delta;</th><th class="num">&Theta;/day</th><th class="num">IV</th><th class="num">B/E</th><th class="num">Cushion</th></tr>'+rows+'</table>';
}
function renderOrders(){
  var oo=(_orders||[]).filter(function(o){return occ(o.symbol);});var el=document.getElementById('orders');
  if(!oo.length){el.innerHTML='<div class="empty">No working orders.</div>';return;}
  var rows=oo.map(function(o){var info=occ(o.symbol);var age='';var ts=o.submitted_at||o.created_at;if(ts)age=Math.round((Date.now()-new Date(ts))/60000)+'m';
    return '<tr><td>'+info.root+' '+info.type+' $'+info.strike+'</td><td>'+(o.side||'')+'</td><td class="num">'+(o.limit_price!=null?('$'+o.limit_price):(o.order_type||o.type||''))+'</td><td class="num">'+(o.filled_qty||0)+' / '+(o.qty||1)+'</td><td>'+(o.status||'')+'</td><td class="num">'+age+'</td></tr>';}).join('');
  el.innerHTML='<table><tr><th>Contract</th><th>Side</th><th class="num">Limit</th><th class="num">Filled</th><th>Status</th><th class="num">Age</th></tr>'+rows+'</table>';
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
  var wr=(green+red)>0?Math.round(green/(green+red)*100):null;var avg=tracked>0?mNet/tracked:0;
  var K=[['Month P&L',money(mNet),mNet],['Closes',String(closes),null],['Win days',wr!=null?(wr+'%'):'—',null],['Green / Red',green+' / '+red,null],['Best day',best!=null?money(best):'—',best],['Worst day',worst!=null?money(worst):'—',worst],['Avg / day',tracked?money(avg):'—',avg]];
  document.getElementById('calkpi').innerHTML=K.map(function(x){var c=x[2]!=null&&(x[0]=='Month P&L'||x[0].indexOf('day')>=0)?(x[2]>=0?'pos':'neg'):'';return '<div class="ck"><div class="l">'+x[0]+'</div><div class="v '+c+'">'+x[1]+'</div></div>';}).join('');
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
