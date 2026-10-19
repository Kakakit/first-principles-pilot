export const windowTags=['dis_win_min','dis_win_max','dis_froz_min','dis_froz_max'] as const;
export type WindowTag=typeof windowTags[number];
export type Windows=Partial<Record<WindowTag,number>>;
export type Counts={min:number;max:number;min_kpoint:number;max_kpoint:number};
export type Gap={lower_band:number;upper_band:number;i_max:number;'i+1_min':number;width_eV:number;midpoint_eV:number};
export type Diagnostic={value_eV:number;classification:string;crossing_bands:{band:number;min_eV:number;max_eV:number}[];containing_gap:Gap|null;nearest_below:Gap|null;nearest_above:Gap|null};
export type BandExtrema={band:number;min_eV:number;max_eV:number;min_kpoint:number;max_kpoint:number};
export type FrozenCutoff={tag:'dis_froz_min'|'dis_froz_max';operator:'<'|'>';limit_eV:number;limiting_states:(BandExtrema & {kpoint:number;energy_eV:number;rank:number})[];limiting_state_count:number;trial_value_eV:number;trial_offset_eV:number;trial_counts:Counts;trial_valid:boolean;exact_n_interval_eV:[number,number]|null};
export type FrozenBoundary={nearby_bands:(BandExtrema & {roles:string[]})[];upper:FrozenCutoff|null;lower:FrozenCutoff|null};
export type WindowCheck={absolute_windows_eV:Record<WindowTag,number|null>;relative_windows_eV:Record<WindowTag,number|null>;outer:Counts;frozen:Counts|null;valid:boolean;errors:string[];analysis_range_covered:boolean;defaulted_tags:WindowTag[];gap?:Gap|null};
export type MeshReport={basis:{num_wann:number;spinors:boolean;channel:string;projections:string[];exclude_bands:string};nkpts:number;nbands:number;fermi_eV:number;analysis_counts:Counts;gaps:Gap[];analysis_lower:Diagnostic;outer_lower:Diagnostic;current:WindowCheck;source_hashes?:Record<string,string>;frozen_boundary?:FrozenBoundary|null};
export type Candidate=Windows & {check:WindowCheck;[key:string]:unknown};
export type DosNumerics={method:'spline'|'gauss300';order:number|null;revision:string;spline_fits?:number};
export type ProjectionBasis={status:string;source:string;lines:string[];keys?:string[];reason?:string};
export type DosMeasure=Windows & {pdos:number|null;tdos:number|null;percent:number|null;warning?:string};
export type Advice={mode:'eig'|'pre';seed:string;source_attempt_id:string;source_hashes:Record<string,string>;interface_source_hashes:Record<string,string>;fermi_eV:number;relative_range_eV:number[];pyw90_version:string;interface:MeshReport;runs:{arguments:string[];stdout:string;stderr:string;exit_code:number}[];advice:{outer:Candidate[];frozen:Candidate[];projections?:string[];minimum_outer_max?:number;eigenval?:{channel:string;spinors?:boolean;nkpts:number;nbands:number;suggested_num_wann?:number;window_num_wann?:number};dos_selection?:{lb:number;threshold:number;spatial_orbitals:number;suggested_num_wann:number;orbitals:{species:string;structure_id:number;orb_name:string;dos:number;selected:boolean}[]}};note:string};
export type AdviceResult=Advice & {ui_fingerprint?:string;advice:Advice['advice'] & {numerics?:DosNumerics;projection_basis?:ProjectionBasis;current_dos?:DosMeasure}};

export function draftProjections(win:string):string[]{
 const block=win.match(/begin\s+projections\s*\n([\s\S]*?)\n\s*end\s+projections/i);
 return block?block[1].split('\n').map(line=>line.split(/[!#]/)[0].trim()).filter(Boolean):[];
}

export function candidateWindows(row:Windows):Windows{
  return Object.fromEntries(windowTags.filter(tag=>typeof row[tag]==='number'&&Number.isFinite(row[tag])).map(tag=>[tag,row[tag]]));
}

// An explicit blank removes the tag; absent fields still come from the final .win.
export function draftWindows(parameters:Record<string,string>,win:string,fermi:number):Windows{
  const result:Windows={};
  for(const line of win.split('\n')){
    const match=line.split(/[!#]/)[0].match(/^\s*(dis_(?:win|froz)_(?:min|max))\s*[:=]\s*(\S+)\s*$/i);
    if(match){const tag=match[1].toLowerCase() as WindowTag;const value=Number(match[2].replace(/[dD]/,'e'));if(!Number.isFinite(value))throw new Error(`${tag} 必须是有限数值`);result[tag]=value-fermi;}
  }
  for(const tag of windowTags){
    const raw=parameters[tag]?.trim();
    if(raw){const value=Number(raw.replace(/[dD]/,'e'));if(!Number.isFinite(value))throw new Error(`${tag} 必须是有限的相对能量`);result[tag]=value;}
    else if(Object.hasOwn(parameters,tag))delete result[tag];
  }
  return result;
}
