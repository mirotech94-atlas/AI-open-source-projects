import {useState} from "react";
import {useQuery} from "@tanstack/react-query";
import {get} from "../lib/api";
type Story={proposals?:{created:string;expires:string;status:string;requested_by:string;target:string}[];alerts:{key:string;message:string;status:string}[];attempts:{id:string;status:string;created:string;sensor:string;started:string|null;heartbeat:string|null;retry_of:string|null;verification:string;output:string}[]};
export function JobStory({id}:{id:string}) {
 const [open,setOpen]=useState(false);
 const query=useQuery({queryKey:["job-story",id],enabled:open,queryFn:()=>get<Story>(`/api/v1/jobs/${id}/story`),refetchInterval:open?10000:false});
 return <details onToggle={e=>setOpen(e.currentTarget.open)}><summary>Evidence and related attempts</summary>
 {query.isPending&&<p role="status">Loading job history…</p>}{query.isError&&<p role="alert">Could not load job history.</p>}
 {query.data?.alerts.map(a=><p key={a.key}>{a.message} · {a.status}</p>)}
 {(query.data?.proposals?.length??0)>0&&<details><summary>Related proposals for this exact action</summary>{query.data?.proposals?.map((p,i)=><p key={i}>{p.requested_by} · {p.target} · {p.status} · proposed {new Date(p.created).toLocaleString()} · expires {new Date(p.expires).toLocaleString()}</p>)}</details>}
 <ol>{query.data?.attempts.map(a=><li key={a.id}><strong>{a.status}</strong> · {new Date(a.created).toLocaleString()} · {a.sensor}
 {a.retry_of&&<p>Retry of attempt {a.retry_of}</p>}{a.heartbeat&&<p>Last heartbeat {new Date(a.heartbeat).toLocaleString()}</p>}
 {a.verification&&<p>{a.verification}</p>}{a.output&&<details><summary>Technical output</summary><pre style={{whiteSpace:"pre-wrap"}}>{a.output}</pre></details>}
 </li>)}</ol>
 </details>;
}
