/** Schedule the next read only after this one settles; cleanup prevents rescheduling. */
export function pollSerial(read:()=>Promise<unknown>, failed:(error:unknown)=>void, interval=2000){
  let cancelled=false,timer:ReturnType<typeof setTimeout>|undefined;
  const poll=async()=>{
    try{await read();}catch(error){if(!cancelled)failed(error);}
    if(!cancelled)timer=setTimeout(()=>void poll(),interval);
  };
  timer=setTimeout(()=>void poll(),interval);
  return ()=>{cancelled=true;clearTimeout(timer);};
}
