import type {Kind} from './model';
import type {SVGProps} from 'react';

export const nodePalette:Record<Kind,Record<'light'|'dark',{fill:string;ink:string;border:string}>>={
"structure":{light:{fill:'hsl(210.0,16.7%,95.3%)',ink:'hsl(208.1,18.8%,33.3%)',border:'hsl(210.0,16.7%,95.3%)'},dark:{fill:'hsl(205.7,16.3%,16.9%)',ink:'hsl(206.3,25.0%,87.5%)',border:'hsl(205.7,16.3%,16.9%)'}},
"relax":{light:{fill:'hsl(130.9,35.5%,93.9%)',ink:'hsl(144.0,35.9%,30.0%)',border:'hsl(130.9,35.5%,93.9%)'},dark:{fill:'hsl(145.0,26.7%,17.6%)',ink:'hsl(130.0,32.7%,78.4%)',border:'hsl(145.0,26.7%,17.6%)'}},
"scf":{light:{fill:'hsl(187.5,50.0%,93.7%)',ink:'hsl(186.0,42.9%,27.5%)',border:'hsl(187.5,50.0%,93.7%)'},dark:{fill:'hsl(190.7,30.4%,18.0%)',ink:'hsl(184.9,44.1%,78.2%)',border:'hsl(190.7,30.4%,18.0%)'}},
"bands":{light:{fill:'hsl(228.0,71.4%,95.9%)',ink:'hsl(226.1,33.0%,41.0%)',border:'hsl(228.0,71.4%,95.9%)'},dark:{fill:'hsl(225.5,26.6%,21.4%)',ink:'hsl(225.4,54.7%,85.3%)',border:'hsl(225.5,26.6%,21.4%)'}},
"dos":{light:{fill:'hsl(30.0,80.0%,94.1%)',ink:'hsl(31.2,60.0%,33.3%)',border:'hsl(30.0,80.0%,94.1%)'},dark:{fill:'hsl(28.9,26.7%,19.8%)',ink:'hsl(30.5,56.4%,80.2%)',border:'hsl(28.9,26.7%,19.8%)'}},
"vasp-wannier":{light:{fill:'hsl(263.1,56.5%,95.5%)',ink:'hsl(266.3,23.1%,40.8%)',border:'hsl(263.1,56.5%,95.5%)'},dark:{fill:'hsl(264.4,24.3%,21.8%)',ink:'hsl(265.9,46.8%,84.5%)',border:'hsl(264.4,24.3%,21.8%)'}},
"wannier90":{light:{fill:'hsl(325.0,50.0%,95.3%)',ink:'hsl(331.3,21.9%,41.2%)',border:'hsl(325.0,50.0%,95.3%)'},dark:{fill:'hsl(318.0,19.2%,20.4%)',ink:'hsl(325.3,41.3%,82.0%)',border:'hsl(318.0,19.2%,20.4%)'}},
"scan":{light:{fill:'hsl(50.5,57.6%,93.5%)',ink:'hsl(48.8,58.1%,29.0%)',border:'hsl(50.5,57.6%,93.5%)'},dark:{fill:'hsl(51.8,23.9%,18.0%)',ink:'hsl(51.3,47.8%,77.5%)',border:'hsl(51.8,23.9%,18.0%)'}},
};
const drawings:Record<Kind,React.ReactNode>={
 structure:<><path d="m5 7 8-3 6 5-8 3zM5 7v10l6 4 8-4V9M11 12v9"/><circle cx="5" cy="7" r="1"/><circle cx="19" cy="9" r="1"/></>,
 relax:<><path d="M3 8V3h5M21 16v5h-5M3 3l6 6m12 12-6-6"/><path d="m8 13 5-5 5 5-5 5z"/></>,
 scf:<><path d="M19 8a8 8 0 0 0-13-2L3 9m0-5v5h5M5 16a8 8 0 0 0 13 2l3-3m0 5v-5h-5"/><circle cx="12" cy="12" r="2"/></>,
 bands:<><path d="M3 3v18h18M5 7c4 0 4 8 8 8s4-8 8-8M5 17c5 0 5-10 9-10s4 10 7 10"/></>,
 dos:<><path d="M4 3v18h17M5 19h3c1 0 1-4 3-4s1 3 3 3 1-6 4-6 2 5 3 5M5 6h3c2 0 2 5 4 5s1-7 3-7 1 4 3 4h3"/></>,
 'vasp-wannier':<><path d="M3 4h11v15H3zM7 4v15m3-15v15M3 9h11M3 14h11M16 12h6m-3-3 3 3-3 3"/></>,
 wannier90:<><ellipse cx="12" cy="12" rx="10" ry="4" transform="rotate(45 12 12)"/><ellipse cx="12" cy="12" rx="10" ry="4" transform="rotate(-45 12 12)"/><circle cx="12" cy="12" r="1.5"/></>,
 scan:<><path d="M3 6h18M3 12h18M3 18h18"/><path d="M7 3v6m10 0v6M10 15v6"/></>,
};
export function NodeIcon({kind,size=24,...props}:SVGProps<SVGSVGElement>&{kind:Kind;size?:number}){return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{drawings[kind]}</svg>;}
