import {useMe} from "../lib/auth";
import {useState} from "react";
import {useMutation,useQuery,useQueryClient} from "@tanstack/react-query";
import {get,post} from "../lib/api";
export function AutoFixOverrides({canWrite}:{canWrite:boolean}) {
 const [host,setHost]=useState(""); const [action,setAction]=useState("");
 const cache=useQueryClient(); const me=useMe();
 const query=useQuery({queryKey:["autofix-overrides",me.data?.active_tenant?.id],enabled:!!me.data,queryFn:()=>get<{hosts:string[];actions:Record<string,string>;overrides:Record<string,Record<string,string>>}>("/api/v1/settings/autofix/overrides")});
 const save=useMutation({mutationFn:(mode:string)=>post("/api/v1/settings/autofix/overrides",{host,action,mode}),onSuccess:()=>cache.invalidateQueries({queryKey:["autofix-overrides"]})});
 const hosts = query.data?.hosts ?? [];
 return <fieldset><legend>Exceptions: one machine, one repair</legend>
 <p>
   Vault repairs what it can on its own. This is where you carve out an exception:
   pick a machine, pick a repair, and say whether Vault may still do that one by
   itself or must ask you first. Everything you do not name here keeps following the
   organization policy above. Repairs that already demand a confirmation still demand
   it — an exception here can make Vault more cautious, never less.
 </p>
 <label>Machine <select value={host} onChange={e=>setHost(e.target.value)} disabled={hosts.length===0}>
   <option value="">{hosts.length===0?"No machines yet":"Choose a machine"}</option>
   {hosts.map(h=><option key={h}>{h}</option>)}
 </select></label>
 {hosts.length===0&&<p>Vault has not seen a server yet, so there is nothing to carve out. Machines appear here once a scan finds them or you add one under Sites &amp; targets.</p>}
 <label>Repair <select value={action} onChange={e=>setAction(e.target.value)}><option value="">Choose a repair</option>{Object.entries(query.data?.actions??{}).map(([key,label])=><option value={key} key={key}>{label}</option>)}</select></label>
 {host&&action&&<p>Right now: {query.data?.overrides?.[host]?.[action]==="manual"?"Vault asks you before doing this here.":"Vault follows the organization policy for this one."}</p>}
 <button type="button" disabled={!canWrite||!host||!action||save.isPending} onClick={()=>save.mutate("manual")}>Ask me first</button>
 <button type="button" disabled={!canWrite||!host||!action||save.isPending} onClick={()=>save.mutate("inherit")}>Follow the organization policy</button>
 {save.isSuccess&&<p role="status">Policy saved. The sensor applies it on its next sync.</p>}
 {(save.isError||query.isError)&&<p role="alert">Could not load or save policy. Try again.</p>}
 </fieldset>;
}
