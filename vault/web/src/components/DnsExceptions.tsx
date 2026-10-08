import {useState} from "react";
import {useQuery,useMutation,useQueryClient} from "@tanstack/react-query";
import {get,post} from "../lib/api";
import {useMe} from "../lib/auth";
export function DnsExceptions({canWrite}:{canWrite:boolean}) {
 const me=useMe();const cache=useQueryClient();const [domain,setDomain]=useState("");const [hours,setHours]=useState(24);
 const query=useQuery({queryKey:["dns-exceptions",me.data?.active_tenant?.id],enabled:!!me.data,refetchInterval:15_000,queryFn:()=>get<{exceptions:{domain:string;expires:string;verification:string}[]}>("/api/v1/dns/exceptions")});
 const save=useMutation({mutationFn:(body:{domain:string;hours:number;remove?:boolean})=>post("/api/v1/dns/exceptions",body),onSuccess:()=>cache.invalidateQueries({queryKey:["dns-exceptions"]})});
 return <fieldset><legend>Temporary domain exception</legend><p>Allow this exact domain for all clients in this organization until it expires. Other filtering stays active. Allowing a domain does not prove it is safe.</p>
 <label>Domain <input value={domain} onChange={e=>setDomain(e.target.value)} placeholder="service.example" disabled={!canWrite}/></label>
 <label>Expires after <select value={hours} onChange={e=>setHours(Number(e.target.value))} disabled={!canWrite}><option value={1}>1 hour</option><option value={24}>24 hours</option><option value={168}>7 days</option></select></label>
 <button type="button" disabled={!canWrite||!domain||save.isPending} onClick={()=>save.mutate({domain,hours})}>Allow temporarily</button>
 {query.data?.exceptions?.map(e=><p key={e.domain}><strong>{e.domain}</strong> · expires {new Date(e.expires).toLocaleString()} · {e.verification} <button type="button" disabled={!canWrite||save.isPending} onClick={()=>save.mutate({domain:e.domain,hours:24,remove:true})}>Remove exception</button></p>)}
 {save.isSuccess&&<p role="status">Exception saved. The sensor applies it on its next sync.</p>}
 {(save.isError||query.isError)&&<p role="alert">Could not load or save exceptions. Try again.</p>}
 </fieldset>;
}
