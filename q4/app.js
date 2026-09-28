(function(){
  if(!window.Q4||!Q4.length){document.querySelector('.page').innerHTML='<div class="empty">No Q4 projections yet.</div>';return;}
  var S=Q4, idx=S.length-1, acct='ALL', st='ACTION', q='';
  var $=function(id){return document.getElementById(id)};
  function fmt(n,d){if(n===null||n===undefined)return '—';return Number(n).toLocaleString('en-US',{maximumFractionDigits:d===undefined?0:d})}
  function usd(n){return n===null||n===undefined?'—':'$'+fmt(n)}
  function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
  function amz(mk,asin){var d={US:'com',CA:'ca',UK:'co.uk',DE:'de',FR:'fr',IT:'it',ES:'es',NL:'nl',AE:'ae',SA:'sa',AU:'com.au',EU:'de'};return 'https://www.amazon.'+(d[mk]||'com')+'/dp/'+asin}
  function md(s){if(!s)return '—';var p=s.split('-');return ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][+p[1]-1]+' '+(+p[2])}
  var SL={OUT:['CRITICAL','Out'],BELOW_FLOOR:['URGENT','Below floor'],BELOW_TARGET:['WATCH','Below target'],OK:['OK','At target']};
  function badge(r){var s=SL[r.status];var b='<span class="badge badge-'+s[0]+'">'+s[1]+'</span>';if(r.blocked)b+=' <span class="badge badge-INFO" title="'+esc(r.blocked)+'">Hold</span>';return b}
  function toast(m){var t=$('toast');t.textContent=m;t.style.display='block';setTimeout(function(){t.style.display='none'},1800)}
  function copy(txt,m){(navigator.clipboard?navigator.clipboard.writeText(txt):Promise.reject()).then(function(){toast(m)},function(){var a=document.createElement('textarea');a.value=txt;document.body.appendChild(a);a.select();document.execCommand('copy');a.remove();toast(m)})}
  function mk(r){return r.pooled?'EU':r.markets[0]}
  function spark(w){var mx=Math.max.apply(null,w)||1,W=112,H=22,n=w.length,bw=W/n;var s='<svg width="'+W+'" height="'+H+'" viewBox="0 0 '+W+' '+H+'" style="display:block">';
    for(var i=0;i<n;i++){var h=Math.max(1,w[i]/mx*(H-2));s+='<rect x="'+(i*bw+0.5).toFixed(1)+'" y="'+(H-h).toFixed(1)+'" width="'+(bw-1.5).toFixed(1)+'" height="'+h.toFixed(1)+'" rx="1" fill="var(--accent)" opacity="0.75"><title>wk '+(i+1)+': '+fmt(w[i])+' u</title></rect>'}
    return s+'</svg>'}
  function snap(){return S[idx]}
  function rowsIn(){var d=snap();return d.rows.filter(function(r){return acct==='ALL'||r.account===acct||(acct.indexOf('BRAND:')===0&&r.brand===acct.slice(6))})}

  function header(){var d=snap();$('hdr-week').textContent=d.week+' · as of '+md(d.as_of);$('hdr-gen').textContent='sales through '+md(d.sales_through);
    $('wk-select').innerHTML=S.map(function(s,i){return '<option value="'+i+'"'+(i===idx?' selected':'')+'>'+s.week+' · '+md(s.as_of)+'</option>'}).join('');
    $('wk-prev').disabled=idx===0;$('wk-next').disabled=idx===S.length-1;
    $('wk-note').textContent='Target '+d.rules.target_days+' days = '+d.rules.floor_days/7+'-week floor x '+d.rules.buffer+'. All money USD.';
    $('foot-week').textContent=d.week;}

  function kpis(){var rs=rowsIn(),t={out:0,bf:0,now:0,q4:0,lost:0,hold:0,short:0};
    rs.forEach(function(r){if(r.status==='OUT')t.out++;if(r.status==='BELOW_FLOOR')t.bf++;if(r.blocked){t.hold++;return}t.now+=r.ship_now;t.q4+=r.q4_units;t.short+=r.proj_short_usd||0});
    rs.forEach(function(r){if(!r.blocked)t.lost+=r.lost_usd||0});
    function k(l,v,c){return '<div class="trend-item"><span class="trend-lbl">'+l+'</span><span class="trend-v '+(c||'')+'">'+v+'</span></div><div class="trend-div"></div>'}
    $('kpis').innerHTML=k('Out of stock',t.out,t.out?'c-CRITICAL':'c-OK')+k('Below 8-wk floor',t.bf,t.bf?'c-URGENT':'c-OK')+k('Ship now',fmt(t.now)+' u',t.now?'c-URGENT':'c-OK')+k('Q4 units to send',fmt(t.q4)+' u')+k('Lost sales to date',usd(t.lost),t.lost?'c-CRITICAL':'c-OK')+k('Short before next arrival (est)',usd(t.short),t.short?'c-URGENT':'c-OK')+(t.hold?k('On hold (listing)',t.hold,'c-WATCH'):'');}

  function tabs(){var d=snap(),b=['ALL','BRAND:CC','BRAND:CL','BRAND:PP'],L={ALL:'All','BRAND:CC':'Cerakote Auto','BRAND:CL':'Cerakote Legacy','BRAND:PP':'Prismatic Powders'};
    $('brand-bar').innerHTML=b.map(function(x){var n=d.rows.filter(function(r){return (x==='ALL'||r.brand===x.slice(6))&&(r.status==='OUT'||r.status==='BELOW_FLOOR')}).length;
      return '<div class="mkt-tab'+((acct===x||(x!=='ALL'&&acct.indexOf('_')>0&&acct.indexOf(x.slice(6))===0))?' active':'')+'" data-a="'+x+'">'+L[x]+(n?' <span class="cnt">'+n+'</span>':' <span class="cnt z">0</span>')+'</div>'}).join('');
    [].forEach.call($('brand-bar').children,function(el){el.onclick=function(){acct=el.dataset.a;render()}});}

  function cards(){var d=snap(),br=acct.indexOf('BRAND:')===0?acct.slice(6):(acct==='ALL'?null:(d.accounts.filter(function(a){return a.code===acct})[0]||{}).brand);
    $('cards').innerHTML=d.accounts.filter(function(a){return !br||a.brand===br}).map(function(a){
      var s=a.out?'CRITICAL':a.below_floor?'URGENT':a.below_target?'WATCH':'OK';
      function tile(l,v,c){return '<div class="tile t-'+c+'"><div class="t-lbl">'+l+'</div><div class="t-val">'+v+'</div></div>'}
      return '<div class="mc s-'+s+(acct===a.code?' active':'')+'" data-a="'+a.code+'"><div class="mc-head"><div class="mc-name">'+esc(a.label)+'</div><div class="mc-sub">'+a.lead+'d lead</div></div>'+
        '<div class="tiles">'+tile('Out',a.out,a.out?'CRITICAL':'OK')+tile('Below floor',a.below_floor,a.below_floor?'URGENT':'OK')+tile('Below tgt',a.below_target,a.below_target?'WATCH':'OK')+'</div>'+
        '<div class="mc-foot"><span>ship now '+fmt(a.ship_now)+' u</span><span>Q4 '+fmt(a.q4_units)+' u</span></div>'+
        '<div class="mc-foot" style="margin-top:3px"><span>'+a.products+' products</span><span style="color:'+(a.lost_usd?'var(--red)':'var(--dim)')+'">lost '+usd(a.lost_usd)+'</span></div></div>'}).join('');
    [].forEach.call($('cards').children,function(el){el.onclick=function(){acct=(acct===el.dataset.a?'ALL':el.dataset.a);render()}});}

  function lost(){var rs=rowsIn().filter(function(r){return r.status==='OUT'}).sort(function(a,b){return (b.lost_usd||0)-(a.lost_usd||0)});
    var live=rs.filter(function(r){return !r.blocked}),hold=rs.filter(function(r){return r.blocked});
    var tot=live.reduce(function(s,r){return s+(r.lost_usd||0)},0),u=live.reduce(function(s,r){return s+(r.lost_units||0)},0),th=hold.reduce(function(s,r){return s+(r.lost_usd||0)},0);
    $('lost-count').textContent='('+rs.length+')';
    $('lost-total').innerHTML='Lost to date, estimate: <b>'+usd(tot)+'</b> &middot; '+fmt(u)+' units across '+live.length+' products'+(hold.length?' &middot; plus '+usd(th)+' on '+hold.length+' listing'+(hold.length>1?'s':'')+' on hold (removed/restricted, not a stock problem)':'');
    $('lost-body').innerHTML=rs.length?rs.map(function(r){return '<tr><td><div class="prod">'+esc(r.name)+(r.blocked?' <span class="badge badge-INFO" title="'+esc(r.blocked)+'">Hold</span>':'')+'</div><div class="sub"><a href="'+amz(mk(r),r.asin)+'" target="_blank" rel="noopener">'+r.asin+'</a> · '+esc(r.sku||'')+'</div>'+(r.blocked?'<div class="reason">'+esc(r.blocked)+'</div>':'')+'</td>'+
      '<td class="sub">'+esc(r.label)+'</td><td class="age" title="'+esc(r.oos_basis||'')+'">'+md(r.oos_start)+'</td><td class="num">'+fmt(r.oos_days)+'</td><td class="num">'+fmt(r.base_vel,1)+'</td><td class="num">'+(r.price_usd?'$'+fmt(r.price_usd,2):'—')+'</td>'+
      '<td class="num">'+fmt(r.lost_units)+'</td><td class="num lost-amt">'+usd(r.lost_usd)+'</td><td class="num">'+(r.inbound_known?fmt(r.inbound):'?')+'</td><td class="num">'+(r.blocked?'<span class="sub">hold</span>':fmt(r.ship_now))+'</td></tr>'}).join(''):'<tr><td colspan="10" class="empty">Nothing out of stock in this view.</td></tr>';}

  function ship(){var d=snap(),rs=rowsIn().filter(function(r){return !r.blocked}),today=d.as_of,dates={},accts=[];
    rs.forEach(function(r){r.schedule.forEach(function(s){var k=s.ship_by<=today?'NOW':s.ship_by;dates[k]=dates[k]||{};dates[k][r.label]=(dates[k][r.label]||0)+s.units;if(accts.indexOf(r.label)<0)accts.push(r.label)})});
    var order=d.accounts.map(function(a){return a.label});accts.sort(function(a,b){return order.indexOf(a)-order.indexOf(b)});
    var keys=Object.keys(dates).sort(function(a,b){return a==='NOW'?-1:b==='NOW'?1:a<b?-1:1});
    if(!keys.length){$('ship-table').innerHTML='<tr><td class="empty">Nothing to ship in this view.</td></tr>';return}
    var h='<thead><tr><th>Ship by</th>'+accts.map(function(a){return '<th class="num">'+esc(a)+'</th>'}).join('')+'<th class="num">Total</th></tr></thead><tbody>';
    keys.forEach(function(k){var tot=0;h+='<tr><td class="mono" style="'+(k==='NOW'?'color:var(--red);font-weight:700':'')+'">'+(k==='NOW'?'Now (overdue)':md(k))+'</td>'+accts.map(function(a){var v=dates[k][a]||0;tot+=v;return '<td class="num">'+(v?fmt(v):'<span class="sub">·</span>')+'</td>'}).join('')+'<td class="num" style="font-weight:700">'+fmt(tot)+'</td></tr>'});
    $('ship-table').innerHTML=h+'</tbody>';
    $('copy-ship').onclick=function(){var lines=['account\tasin\tsku\tproduct\tship_by\tarrive_by\tunits'];rs.forEach(function(r){r.schedule.forEach(function(s){lines.push([r.label,r.asin,r.sku,r.name,s.ship_by<=today?'NOW':s.ship_by,s.arrive_by,s.units].join('\t'))})});copy(lines.join('\n'),'Ship list copied')};}

  function prods(){var rs=rowsIn().filter(function(r){if(st==='ALL')return true;if(st==='ACTION')return r.status!=='OK'||r.ship_now>0;return r.status===st})
      .filter(function(r){return !q||(r.name+' '+r.asin+' '+(r.sku||'')).toLowerCase().indexOf(q)>=0});
    $('prod-count').textContent='('+rs.length+')';
    $('prod-body').innerHTML=rs.length?rs.map(function(r){var nx=r.schedule.length?r.schedule[0]:null,ev=r.event_index||{};
      function ix(v){return v==null?'—':'<span style="color:'+(v>=1.3?'var(--accent)':v<0.8?'var(--dim)':'var(--text2)')+'">'+v.toFixed(2)+'x</span>'}
      return '<tr><td>'+badge(r)+'</td><td><div class="prod">'+esc(r.name)+'</div><div class="sub"><a href="'+amz(mk(r),r.asin)+'" target="_blank" rel="noopener">'+r.asin+'</a> · '+esc(r.sku||'')+' · '+esc(r.index_basis)+'</div></td>'+
      '<td class="sub">'+esc(r.label)+(r.pooled?'<div class="sub">'+r.markets.join(' ')+'</div>':'')+'</td><td class="num">'+fmt(r.available)+'</td><td class="num">'+(r.inbound_known?fmt(r.inbound):'?')+'</td>'+
      '<td class="num" title="raw 30d avg '+fmt(r.raw_vel30,1)+'/day; '+r.instock_days+' in-stock days used">'+fmt(r.base_vel,1)+(r.low_sample?'<div class="sub" style="color:var(--amber)">'+r.instock_days+'d data</div>':'')+(Math.abs(r.base_vel-r.raw_vel30)>0.15*r.base_vel?'<div class="sub">raw '+fmt(r.raw_vel30,1)+'</div>':'')+'</td>'+
      '<td class="num" title="days of forecast demand covered by available / available + inbound">'+fmt(r.cover_avail)+'d<div class="sub">'+fmt(r.cover_total)+'d w/ inb</div></td>'+
      '<td class="num">'+fmt(r.floor_units)+'</td><td class="num">'+fmt(r.target_units)+'</td><td class="num" style="font-weight:700;color:'+(r.ship_now&&!r.blocked?'var(--orange)':'var(--dim)')+'">'+(r.blocked?'hold':fmt(r.ship_now))+'</td><td class="num">'+fmt(r.q4_units)+'</td>'+
      '<td class="age">'+(nx?(nx.late?'<span style="color:var(--red)">now</span>':md(nx.ship_by)):'—')+'</td><td class="num">'+ix(ev.pbdd)+'</td><td class="num">'+ix(ev.bf)+'</td><td class="num">'+ix(ev.hol)+'</td><td>'+spark(r.weekly)+'</td></tr>'}).join(''):'<tr><td colspan="16" class="empty">No products match.</td></tr>';
    $('copy-csv').onclick=function(){var cols=['label','asin','sku','name','status','available','inbound','base_vel','raw_vel30','cover_avail','cover_total','floor_units','target_units','ship_now','q4_units','lost_units','lost_usd','oos_start','blocked'];
      copy([cols.join(',')].concat(rs.map(function(r){return cols.map(function(c){var v=r[c];v=v==null?'':String(v);return /[",]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v}).join(',')})).join('\n'),'CSV copied')};
    var d=snap();$('method').innerHTML='Demand = base velocity (last '+d.rules.base_days+' in-stock days, FBA + FBM) x last-year seasonal index (same week in 2025 vs Sep 1-28 2025 average, clipped '+d.rules.index_clip[0]+'-'+d.rules.index_clip[1]+'x). Last-year weeks under 30% of the September base (last year\'s stockouts, or no data) fall through to the next curve. No 2025 history outside North America, so UK, EU, SA and AU use the same ASIN\'s US curve. Basis per row: own LY, US LY, brand LY, or flat. PBDD / BF / Dec columns = demand multiplier that week. Floor = 56 days of forecast demand, Target = 67 days. Ship now = forecast demand over lead time + 67 days, minus available and inbound. FBA capacity limits are ignored by design.';}

  function files(){var d=snap(),f=d.files;if(!f){$('files-panel').style.display='none';return}$('files-panel').style.display='';
    var base='files/'+d.week+'/';$('review-link').href=base+f.review;$('review-link').setAttribute('download','');
    $('files-body').innerHTML=f.files.map(function(x){return '<tr><td><a href="'+base+x.file+'" download>'+esc(x.file)+'</a></td><td class="sub">'+esc(x.where)+'</td><td class="num">'+fmt(x.skus)+'</td><td class="num">'+fmt(x.units)+'</td><td class="num" style="color:'+(x.no_case_pack?'var(--amber)':'var(--dim)')+'">'+fmt(x.no_case_pack)+'</td></tr>'}).join('')}
  function render(){header();tabs();kpis();cards();lost();files();ship();prods()}
  $('wk-select').onchange=function(){idx=+this.value;render()};
  $('wk-prev').onclick=function(){if(idx>0){idx--;render()}};
  $('wk-next').onclick=function(){if(idx<S.length-1){idx++;render()}};
  [].forEach.call($('st-chips').children,function(el){el.onclick=function(){st=el.dataset.st;[].forEach.call($('st-chips').children,function(c){c.classList.toggle('active',c===el)});prods()}});
  $('search').oninput=function(){q=this.value.trim().toLowerCase();prods()};
  render();
})();
