(function(root){
'use strict';
const shift=(date,n)=>{const d=new Date(date+'T00:00:00Z');d.setUTCDate(d.getUTCDate()+n);return d.toISOString().slice(0,10);};
function calculate(data,start,end,faculty='all'){
 const keys=Object.keys(data.faculties).filter(k=>faculty==='all'||k===faculty);
 const scoped=data.readings.filter(r=>keys.includes(r.faculty));
 const days=[];for(let date=start;date<=end;date=shift(date,1)){const rows=scoped.filter(r=>r.date===date);days.push({date,rows,count:rows.length,total:rows.reduce((s,r)=>s+r.hundredths,0),expected:keys.length});}
 const reported=days.filter(d=>d.count);const alerts=[];
 for(const row of scoped.filter(r=>r.date>=start&&r.date<=end)){
 const prior=scoped.filter(r=>r.faculty===row.faculty&&r.date<row.date).sort((a,b)=>b.date.localeCompare(a.date)).slice(0,7);
 if(prior.length===7){const sum=prior.reduce((s,r)=>s+r.hundredths,0);if(sum>0&&row.hundredths*70>sum*13)alerts.push({...row,baseline:sum/7,percent:(row.hundredths/(sum/7)-1)*100});}
 }
 return{keys,days,reported,latest:reported.at(-1)||null,average:reported.length?reported.reduce((s,d)=>s+d.total,0)/reported.length:null,alerts:alerts.sort((a,b)=>b.date.localeCompare(a.date))};
}
const api={shift,calculate};if(typeof module!=='undefined')module.exports=api;else root.EnergyAnalytics=api;
})(typeof window!=='undefined'?window:globalThis);
