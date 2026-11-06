export function moment(x,y,lo,hi){
 if(x.length!==y.length||x.length<2||!Number.isFinite(lo)||!Number.isFinite(hi)||lo<x[0]||hi>x.at(-1)||lo>=hi)throw Error('积分上下界须在冻结范围内，且下界小于上界');
 let weight=0,first=0,negative=false;
 for(let i=0;i<x.length-1;i++){
  if(!Number.isFinite(x[i])||!Number.isFinite(y[i])||!Number.isFinite(x[i+1])||!Number.isFinite(y[i+1])||x[i+1]<=x[i])throw Error('无效的能量网格或密度');
  const a=Math.max(lo,x[i]),b=Math.min(hi,x[i+1]);if(b<=a)continue;
  const ta=Math.max(0,Math.min(1,(a-x[i])/(x[i+1]-x[i]))),tb=Math.max(0,Math.min(1,(b-x[i])/(x[i+1]-x[i]))),u=(1-ta)*y[i]+ta*y[i+1],v=(1-tb)*y[i]+tb*y[i+1];
  weight+=(b-a)*(u+v)/2;first+=(b-a)*((2*a+b)*u+(a+2*b)*v)/6;negative ||= u<0||v<0;
 }
 return {weight,first_moment_ev:first,centroid_ev:weight>0?first/weight:null,negative_values:negative};
}
export function groupMean(values,indices){if(!indices.length||indices.some(i=>!Number.isInteger(i)||i<0||i>=values.length||!Number.isFinite(values[i])))return null;return indices.reduce((sum,i)=>sum+values[i],0)/indices.length;}
export function hLevels(metrics,kind){return kind==='diagonal'?metrics.matrix.real.map((row,i)=>row[i]):metrics.eigenvalues_ev.slice();}
export function levelOffset(values, reference, fermiEv = null) {
 if (reference === null || Number.isFinite(reference)) return reference;
 if (reference === 'fermi') return fermiEv;
 if (reference !== 'centroid') return 0;
 const finite = values.filter(Number.isFinite);
 return finite.length ? finite.reduce((sum, value) => sum + value, 0) / finite.length : null;
}

export function relativeLevels(values, reference, fermiEv = null) {
 const offset = levelOffset(values, reference, fermiEv);
 return values.map(value => Number.isFinite(value) && Number.isFinite(offset) ? value - offset : null);
}
