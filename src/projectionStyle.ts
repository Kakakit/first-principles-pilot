// Allocate aggregate projections first, so common element/orbital selections use
// distinct categorical colors. The full source catalog fixes colors across
// selection changes, spin channels, fatband, PDOS and exports.
const palette=['#d55e00','#0072b2','#009e73','#c62828','#a6761d','#7b3294',
  '#008b9e','#ce4e99','#5d6b00','#443c8f','#4d4d4d','#bd6e00',
  '#2c7fb8','#af477f','#397d49','#8c564b','#585db0','#b54a23',
  '#64853e','#ad3a5d','#417d86','#8c6bb1','#8b7034','#3d618b'];
const orbitalOrder=['d','p','s','dxy','dyz','dz2','dxz','dx2-y2','px','py','pz'];
export function projectionColors(elements:string[]):(element:string,orbital:string)=>string {
  const ordered=[...new Set(elements)].sort();
  const colors=new Map<string,string>();
  for(const orbital of orbitalOrder)for(const element of ordered){
    const index=colors.size;
    colors.set(`${element}|${orbital}`,palette[index]??`hsl(${(index*137.508)%360} 65% ${index%2?35:52}%)`);
  }
  return (element,orbital)=>colors.get(`${element}|${orbital}`)||'#555555';
}

/** Use the actual path extent, without ECharts rounding to a larger nice tick. */
export function kPathBounds(x:number[]):{min:number;max:number} {
  let min=Infinity,max=-Infinity;
  for(const value of x)if(Number.isFinite(value)){min=Math.min(min,value);max=Math.max(max,value);}
  return Number.isFinite(min)?{min,max}:{min:0,max:1};
}
