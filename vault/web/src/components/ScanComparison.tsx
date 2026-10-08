import {useState} from "react";
import {useQuery} from "@tanstack/react-query";
import {get} from "../lib/api";
type Finding={id?:string;title?:string;name?:string;key?:string};
type Comparison={comparable:boolean;reason:string;new:Finding[];unchanged:Finding[];resolved:Finding[];unverified:Finding[]};
export function ScanComparison({id}:{id:string}) {
 const [open,setOpen]=useState(false);
 const query=useQuery({queryKey:["scan-comparison",id],enabled:open,queryFn:()=>get<Comparison>(`/api/v1/jobs/${id}/comparison`)});
 return <details onToggle={e=>setOpen(e.currentTarget.open)}><summary>Compare with previous scan</summary>
 {query.isPending&&<p role="status">Loading comparison…</p>}
 {query.isError&&<p role="alert">Could not load comparison.</p>}
 {query.data&&<><p>{query.data.reason}</p>{query.data.comparable&&(["new","unchanged","resolved","unverified"] as const).map(kind=><div key={kind}><strong>{kind}: {query.data![kind].length}</strong><ul>{query.data![kind].map((f,i)=><li key={i}>{f.title||f.name||f.key||f.id}</li>)}</ul></div>)}</>}
 </details>;
}
